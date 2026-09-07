"""Expense tracker API + static host.

Accounts and storage are Supabase. The browser holds a Supabase access token and
sends it as a Bearer header; every data call is made with that same token, so Row
Level Security in Postgres is what actually enforces one-user-one-dataset.
"""
import hmac
import os
from datetime import date
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, field_validator

import analytics
import supa

INVITE_CODE = os.environ.get("INVITE_CODE", "")

app = FastAPI(title="Expense Tracker", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------- auth

class Session:
    def __init__(self, token: str, user_id: str):
        self.token = token
        self.user_id = user_id


def current(authorization: str = Header(default="")) -> Session:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Not signed in")
    return Session(token, supa.verify(token)["sub"])


ME = Depends(current)


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    invite_code: str = ""


def _session_response(auth: dict) -> dict:
    user = auth.get("user") or {}
    return {
        "access_token": auth["access_token"],
        "refresh_token": auth.get("refresh_token", ""),
        "email": user.get("email", ""),
        "prefs": user.get("user_metadata") or {},
    }


@app.post("/api/signup")
def signup(body: Credentials):
    if INVITE_CODE and not hmac.compare_digest(body.invite_code.strip(), INVITE_CODE):
        raise HTTPException(403, "Invalid invite code")
    created = supa.sign_up(body.email.lower().strip(), body.password)

    # With email confirmation switched on, signup returns a user but no session.
    # That is a success, not an error -- report it as one so the UI can say so plainly.
    if not created.get("access_token"):
        return {"pending": True,
                "message": "Account created. Check your email for the confirmation link, "
                           "then come back and sign in."}

    session = _session_response(created)
    supa.seed_user(session["access_token"], created["user"]["id"])
    return session


@app.post("/api/login")
def login(body: Credentials):
    auth = supa.sign_in(body.email.lower().strip(), body.password)
    session = _session_response(auth)
    # A user created outside this app (or one whose seeding failed) still needs lookups.
    if not supa.select(session["access_token"], "categories", {"select": "id", "limit": "1"}):
        supa.seed_user(session["access_token"], auth["user"]["id"])
    return session


class RefreshIn(BaseModel):
    refresh_token: str


@app.post("/api/refresh")
def refresh(body: RefreshIn):
    return _session_response(supa.refresh(body.refresh_token))


@app.post("/api/logout")
def logout(me: Session = ME):
    supa.sign_out(me.token)
    return {"ok": True}


class Prefs(BaseModel):
    mode: str = Field(default="light", pattern="^(light|dark)$")
    preset: str = Field(default="oxy", max_length=32, pattern="^[a-z0-9_-]+$")


@app.put("/api/preferences")
def save_preferences(body: Prefs, me: Session = ME):
    """Appearance lives in Supabase Auth user_metadata -- no table, no migration."""
    return supa.update_user(me.token, body.model_dump())


@app.get("/api/me")
def me_prefs(me: Session = ME):
    return {"prefs": supa.verify(me.token).get("user_metadata") or {}}


@app.get("/api/config")
def config():
    return {"invite_required": bool(INVITE_CODE)}


# ---------------------------------------------------------------- lookups

def load_categories(me: Session) -> list:
    cats = supa.select(me.token, "categories", {"select": "id,name", "order": "name"})
    subs = supa.select(me.token, "subcategories",
                       {"select": "id,name,category_id", "order": "name"})
    by_cat = {}
    for s in subs:
        by_cat.setdefault(s["category_id"], []).append({"id": s["id"], "name": s["name"]})
    return [{**c, "subcategories": by_cat.get(c["id"], [])} for c in cats]


def load_limits(me: Session) -> list:
    rows = supa.select(me.token, "limits",
                       {"select": "id,kind,amount,category_id,categories(name)", "order": "kind"})
    return [{"id": r["id"], "kind": r["kind"], "amount": float(r["amount"]),
             "category": (r.get("categories") or {}).get("name")} for r in rows]


@app.get("/api/bootstrap")
def bootstrap(me: Session = ME):
    return {
        "categories": load_categories(me),
        "payment_methods": supa.PAYMENT_METHODS,
        "limits": load_limits(me),
    }


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    category_id: Optional[int] = None


@app.post("/api/categories")
def add_category(body: CategoryIn, me: Session = ME):
    name = body.name.strip()
    if body.category_id:
        supa.insert(me.token, "subcategories",
                    {"user_id": me.user_id, "category_id": body.category_id, "name": name},
                    returning=False)
    else:
        supa.insert(me.token, "categories",
                    {"user_id": me.user_id, "name": name}, returning=False)
    return {"categories": load_categories(me)}


@app.delete("/api/categories/{cid}")
def del_category(cid: int, sub: bool = False, me: Session = ME):
    if sub:
        supa.delete(me.token, "subcategories", {"id": f"eq.{cid}"})
    else:
        if supa.select(me.token, "expenses", {"select": "id", "category_id": f"eq.{cid}", "limit": "1"}):
            raise HTTPException(409, "Category is in use by existing transactions")
        supa.delete(me.token, "categories", {"id": f"eq.{cid}"})
    return {"categories": load_categories(me)}


# ---------------------------------------------------------------- expenses

class ExpenseIn(BaseModel):
    txn_date: date
    amount: float = Field(gt=0, le=10**11)
    category_id: int
    subcategory_id: Optional[int] = None
    description: str = Field(default="", max_length=280)
    payment_method: str

    @field_validator("payment_method")
    @classmethod
    def _pm(cls, v):
        if v not in supa.PAYMENT_METHODS:
            raise ValueError("Invalid payment method")
        return v

    @field_validator("txn_date")
    @classmethod
    def _date(cls, v):
        if v.year < 2000 or v > date.today().replace(year=date.today().year + 1):
            raise ValueError("Date out of range")
        return v

    def row(self, user_id: str) -> dict:
        return {"user_id": user_id, "txn_date": self.txn_date.isoformat(), "amount": self.amount,
                "category_id": self.category_id, "subcategory_id": self.subcategory_id,
                "description": self.description.strip(), "payment_method": self.payment_method}


def check_refs(body: ExpenseIn, me: Session):
    """RLS already blocks other people's rows; this turns a silent 0-row write into a clear error."""
    if not supa.select(me.token, "categories", {"select": "id", "id": f"eq.{body.category_id}"}):
        raise HTTPException(404, "Category not found")
    if body.subcategory_id and not supa.select(
            me.token, "subcategories",
            {"select": "id", "id": f"eq.{body.subcategory_id}",
             "category_id": f"eq.{body.category_id}"}):
        raise HTTPException(400, "Sub-category does not belong to that category")


@app.post("/api/expenses")
def add_expense(body: ExpenseIn, me: Session = ME):
    check_refs(body, me)
    created = supa.insert(me.token, "expenses", body.row(me.user_id))
    return {"id": created[0]["id"]}


@app.put("/api/expenses/{eid}")
def edit_expense(eid: int, body: ExpenseIn, me: Session = ME):
    check_refs(body, me)
    updated = supa.update(me.token, "expenses", {"id": f"eq.{eid}"}, body.row(me.user_id))
    if not updated:
        raise HTTPException(404, "Transaction not found")
    return {"id": eid}


@app.delete("/api/expenses/{eid}")
def del_expense(eid: int, me: Session = ME):
    supa.delete(me.token, "expenses", {"id": f"eq.{eid}"})
    return {"ok": True}


def fetch_rows(me: Session) -> list:
    rows = supa.select(me.token, "expenses", {
        "select": "id,txn_date,amount,description,payment_method,categories(name),subcategories(name)",
        "order": "txn_date.desc,id.desc",
    })
    return [{"id": r["id"], "d": date.fromisoformat(r["txn_date"]), "amount": float(r["amount"]),
             "category": (r.get("categories") or {}).get("name") or "Uncategorised",
             "subcategory": (r.get("subcategories") or {}).get("name"),
             "description": r["description"], "payment": r["payment_method"]} for r in rows]


class Filters(BaseModel):
    model_config = {"extra": "ignore"}
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    years: list[int] = []
    months: list[int] = []
    categories: list[str] = []
    subcategories: list[str] = []
    payments: list[str] = []
    weekdays: list[int] = []
    daytype: Optional[str] = None

    def as_dict(self):
        d = self.model_dump()
        d["from"] = d.pop("from_")
        return d


@app.post("/api/analytics")
def get_analytics(me: Session = ME, filters: Optional[Filters] = None):
    filters = filters or Filters()
    rows = fetch_rows(me)
    result = analytics.compute(rows, filters.as_dict(), load_limits(me))
    result["transactions"] = [
        {"id": r["id"], "date": r["d"].isoformat(), "amount": r["amount"], "category": r["category"],
         "subcategory": r["subcategory"], "description": r["description"], "payment": r["payment"]}
        for r in analytics.apply_filters(rows, filters.as_dict())
    ]
    first = min((r["d"] for r in rows), default=None)
    result["available"] = {"years": sorted({r["d"].year for r in rows}, reverse=True),
                           "first_entry": first.isoformat() if first else None}
    return result


# ---------------------------------------------------------------- limits

class LimitIn(BaseModel):
    kind: str
    amount: float = Field(gt=0, le=10**11)
    category_id: Optional[int] = None

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v not in ("daily", "monthly", "category"):
            raise ValueError("Invalid limit type")
        return v


@app.post("/api/limits")
def set_limit(body: LimitIn, me: Session = ME):
    if body.kind == "category" and not body.category_id:
        raise HTTPException(400, "Pick a category for a category limit")
    cid = body.category_id if body.kind == "category" else None
    existing = supa.select(me.token, "limits", {
        "select": "id", "kind": f"eq.{body.kind}",
        "category_id": f"eq.{cid}" if cid else "is.null"})
    if existing:
        supa.update(me.token, "limits", {"id": f"eq.{existing[0]['id']}"}, {"amount": body.amount})
    else:
        supa.insert(me.token, "limits",
                    {"user_id": me.user_id, "kind": body.kind, "category_id": cid,
                     "amount": body.amount}, returning=False)
    return {"limits": load_limits(me)}


@app.delete("/api/limits/{lid}")
def del_limit(lid: int, me: Session = ME):
    supa.delete(me.token, "limits", {"id": f"eq.{lid}"})
    return {"limits": load_limits(me)}


# ---------------------------------------------------------------- static

# api_route with HEAD: uptime monitors send HEAD by default, and @app.get alone
# answers those with 405.
@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz():
    # Fail loudly when Supabase is unreachable -- a 200 here would tell a monitor
    # everything is fine while every page in the app was actually broken.
    if not supa.health():
        raise HTTPException(503, "Supabase unreachable")
    return {"ok": True}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.api_route("/", methods=["GET", "HEAD"])
def index():
    return FileResponse("static/index.html")
