"""Expense tracker API + static host."""
import hashlib
import hmac
import os
import secrets
from datetime import date
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, field_validator
from starlette.middleware.sessions import SessionMiddleware

import analytics
import db

INVITE_CODE = os.environ.get("INVITE_CODE", "")
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
SECURE_COOKIE = os.environ.get("RENDER", "") != ""

app = FastAPI(title="Expense Tracker", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=SECURE_COOKIE,
                   same_site="lax", max_age=60 * 60 * 24 * 30)


@app.on_event("startup")
def _startup():
    db.init()


# ---------------------------------------------------------------- auth

def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    return salt.hex() + ":" + hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def verify_pw(pw: str, stored: str) -> bool:
    try:
        salt, digest = stored.split(":", 1)
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc, digest)


def current_user(request: Request) -> int:
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(401, "Not signed in")
    return uid


UID = Depends(current_user)


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    invite_code: str = ""


@app.post("/api/signup")
def signup(body: Credentials, request: Request):
    if INVITE_CODE and not hmac.compare_digest(body.invite_code.strip(), INVITE_CODE):
        raise HTTPException(403, "Invalid invite code")
    email = body.email.lower().strip()
    with db.pool.connection() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email=%s", (email,)).fetchone():
            raise HTTPException(409, "An account with that email already exists")
        uid = conn.execute(
            "INSERT INTO users (email, pw_hash) VALUES (%s, %s) RETURNING id",
            (email, hash_pw(body.password)),
        ).fetchone()["id"]
        db.seed_user(conn, uid)
    request.session["uid"] = uid
    return {"email": email}


@app.post("/api/login")
def login(body: Credentials, request: Request):
    email = body.email.lower().strip()
    with db.pool.connection() as conn:
        user = conn.execute("SELECT id, pw_hash FROM users WHERE email=%s", (email,)).fetchone()
    if not user or not verify_pw(body.password, user["pw_hash"]):
        raise HTTPException(401, "Incorrect email or password")
    request.session["uid"] = user["id"]
    return {"email": email}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return {"user": None, "invite_required": bool(INVITE_CODE)}
    with db.pool.connection() as conn:
        user = conn.execute("SELECT email FROM users WHERE id=%s", (uid,)).fetchone()
    if not user:
        request.session.clear()
        return {"user": None, "invite_required": bool(INVITE_CODE)}
    return {"user": user["email"], "invite_required": bool(INVITE_CODE)}


# ---------------------------------------------------------------- lookups

def load_categories(conn, uid):
    rows = conn.execute("""
        SELECT c.id, c.name, s.id AS sub_id, s.name AS sub_name
        FROM categories c LEFT JOIN subcategories s ON s.category_id = c.id
        WHERE c.user_id = %s ORDER BY c.name, s.name
    """, (uid,)).fetchall()
    cats = {}
    for r in rows:
        c = cats.setdefault(r["id"], {"id": r["id"], "name": r["name"], "subcategories": []})
        if r["sub_id"]:
            c["subcategories"].append({"id": r["sub_id"], "name": r["sub_name"]})
    return list(cats.values())


def load_limits(conn, uid):
    return [dict(r) for r in conn.execute("""
        SELECT l.id, l.kind, l.amount, c.name AS category
        FROM limits l LEFT JOIN categories c ON c.id = l.category_id
        WHERE l.user_id = %s ORDER BY l.kind, c.name
    """, (uid,)).fetchall()]


@app.get("/api/bootstrap")
def bootstrap(uid: int = UID):
    with db.pool.connection() as conn:
        return {
            "categories": load_categories(conn, uid),
            "payment_methods": db.PAYMENT_METHODS,
            "limits": [{**l, "amount": float(l["amount"])} for l in load_limits(conn, uid)],
        }


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    category_id: Optional[int] = None


@app.post("/api/categories")
def add_category(body: CategoryIn, uid: int = UID):
    name = body.name.strip()
    with db.pool.connection() as conn:
        if body.category_id:  # adding a sub-category
            own(conn, "categories", body.category_id, uid)
            conn.execute("INSERT INTO subcategories (category_id, name) VALUES (%s, %s)"
                         " ON CONFLICT DO NOTHING", (body.category_id, name))
        else:
            conn.execute("INSERT INTO categories (user_id, name) VALUES (%s, %s)"
                         " ON CONFLICT DO NOTHING", (uid, name))
        return {"categories": load_categories(conn, uid)}


@app.delete("/api/categories/{cid}")
def del_category(cid: int, sub: bool = False, uid: int = UID):
    with db.pool.connection() as conn:
        if sub:
            conn.execute("""DELETE FROM subcategories WHERE id=%s AND category_id IN
                            (SELECT id FROM categories WHERE user_id=%s)""", (cid, uid))
        else:
            own(conn, "categories", cid, uid)
            if conn.execute("SELECT 1 FROM expenses WHERE category_id=%s LIMIT 1", (cid,)).fetchone():
                raise HTTPException(409, "Category is in use by existing transactions")
            conn.execute("DELETE FROM categories WHERE id=%s AND user_id=%s", (cid, uid))
        return {"categories": load_categories(conn, uid)}


def own(conn, table, row_id, uid):
    """Ownership guard. `table` is always a literal from this module, never user input."""
    if not conn.execute(f"SELECT 1 FROM {table} WHERE id=%s AND user_id=%s",
                        (row_id, uid)).fetchone():
        raise HTTPException(404, "Not found")


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
        if v not in db.PAYMENT_METHODS:
            raise ValueError("Invalid payment method")
        return v

    @field_validator("txn_date")
    @classmethod
    def _date(cls, v):
        if v.year < 2000 or v > date.today().replace(year=date.today().year + 1):
            raise ValueError("Date out of range")
        return v


def check_refs(conn, body: ExpenseIn, uid: int):
    own(conn, "categories", body.category_id, uid)
    if body.subcategory_id and not conn.execute(
        "SELECT 1 FROM subcategories WHERE id=%s AND category_id=%s",
        (body.subcategory_id, body.category_id),
    ).fetchone():
        raise HTTPException(400, "Sub-category does not belong to that category")


@app.post("/api/expenses")
def add_expense(body: ExpenseIn, uid: int = UID):
    with db.pool.connection() as conn:
        check_refs(conn, body, uid)
        row = conn.execute("""
            INSERT INTO expenses (user_id, txn_date, amount, category_id, subcategory_id,
                                  description, payment_method)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (uid, body.txn_date, body.amount, body.category_id, body.subcategory_id,
              body.description.strip(), body.payment_method)).fetchone()
    return {"id": row["id"]}


@app.put("/api/expenses/{eid}")
def edit_expense(eid: int, body: ExpenseIn, uid: int = UID):
    with db.pool.connection() as conn:
        check_refs(conn, body, uid)
        updated = conn.execute("""
            UPDATE expenses SET txn_date=%s, amount=%s, category_id=%s, subcategory_id=%s,
                   description=%s, payment_method=%s
            WHERE id=%s AND user_id=%s RETURNING id
        """, (body.txn_date, body.amount, body.category_id, body.subcategory_id,
              body.description.strip(), body.payment_method, eid, uid)).fetchone()
    if not updated:
        raise HTTPException(404, "Transaction not found")
    return {"id": eid}


@app.delete("/api/expenses/{eid}")
def del_expense(eid: int, uid: int = UID):
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM expenses WHERE id=%s AND user_id=%s", (eid, uid))
    return {"ok": True}


def fetch_rows(conn, uid):
    return [{"id": r["id"], "d": r["txn_date"], "amount": float(r["amount"]),
             "category": r["category"], "subcategory": r["subcategory"],
             "description": r["description"], "payment": r["payment_method"]}
            for r in conn.execute("""
                SELECT e.id, e.txn_date, e.amount, e.description, e.payment_method,
                       e.category_id, e.subcategory_id, c.name AS category, s.name AS subcategory
                FROM expenses e
                JOIN categories c ON c.id = e.category_id
                LEFT JOIN subcategories s ON s.id = e.subcategory_id
                WHERE e.user_id = %s ORDER BY e.txn_date DESC, e.id DESC
            """, (uid,)).fetchall()]


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
def get_analytics(uid: int = UID, filters: Optional[Filters] = None):
    filters = filters or Filters()
    with db.pool.connection() as conn:
        rows = fetch_rows(conn, uid)
        limits = [{**l, "amount": float(l["amount"])} for l in load_limits(conn, uid)]
    result = analytics.compute(rows, filters.as_dict(), limits)
    result["transactions"] = [
        {"id": r["id"], "date": r["d"].isoformat(), "amount": r["amount"], "category": r["category"],
         "subcategory": r["subcategory"], "description": r["description"], "payment": r["payment"]}
        for r in analytics.apply_filters(rows, filters.as_dict())
    ]
    result["available"] = {
        "years": sorted({r["d"].year for r in rows}, reverse=True),
        "first_entry": min((r["d"] for r in rows), default=None),
    }
    if result["available"]["first_entry"]:
        result["available"]["first_entry"] = result["available"]["first_entry"].isoformat()
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
def set_limit(body: LimitIn, uid: int = UID):
    if body.kind == "category" and not body.category_id:
        raise HTTPException(400, "Pick a category for a category limit")
    with db.pool.connection() as conn:
        if body.category_id:
            own(conn, "categories", body.category_id, uid)
        conn.execute("""
            INSERT INTO limits (user_id, kind, category_id, amount) VALUES (%s,%s,%s,%s)
            ON CONFLICT (user_id, kind, COALESCE(category_id, 0))
            DO UPDATE SET amount = EXCLUDED.amount
        """, (uid, body.kind, body.category_id if body.kind == "category" else None, body.amount))
        return {"limits": [{**l, "amount": float(l["amount"])} for l in load_limits(conn, uid)]}


@app.delete("/api/limits/{lid}")
def del_limit(lid: int, uid: int = UID):
    with db.pool.connection() as conn:
        conn.execute("DELETE FROM limits WHERE id=%s AND user_id=%s", (lid, uid))
        return {"limits": [{**l, "amount": float(l["amount"])} for l in load_limits(conn, uid)]}


# ---------------------------------------------------------------- static

@app.get("/healthz")
def healthz():
    with db.pool.connection() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
