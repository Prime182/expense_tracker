"""Supabase access: auth (GoTrue), data (PostgREST) and JWT verification.

Accounts live in Supabase Auth. Data lives in Postgres behind Row Level Security,
reached with the caller's own access token so the database enforces isolation --
this module never has to remember to add a user_id filter for reads.
"""
import os

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import HTTPException
from jwt import PyJWKClient

load_dotenv()

URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
JWKS_URL = os.environ.get("SUPABASE_JWKS_URL") or f"{URL}/auth/v1/.well-known/jwks.json"

if not URL or not PUBLISHABLE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY must be set (see .env.example)")

AUTH = f"{URL}/auth/v1"
REST = f"{URL}/rest/v1"

# One pooled client: PostgREST is hit on every request, and re-doing the TLS
# handshake each time would dominate the latency.
http = httpx.Client(timeout=20.0, headers={"apikey": PUBLISHABLE_KEY})

_jwks = PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600)

PAYMENT_METHODS = ["Cash", "UPI", "Debit", "Others"]

DEFAULT_CATEGORIES = {
    "Food & Dining": ["Groceries", "Restaurants", "Cafe", "Food Delivery", "Snacks"],
    "Transport": ["Fuel", "Cab", "Public Transport", "Parking", "Maintenance"],
    "Housing": ["Rent", "Maintenance", "Repairs", "Furniture"],
    "Utilities": ["Electricity", "Water", "Internet", "Mobile", "Gas"],
    "Health": ["Pharmacy", "Doctor", "Insurance", "Fitness"],
    "Shopping": ["Clothing", "Electronics", "Home", "Gifts"],
    "Entertainment": ["Streaming", "Movies", "Events", "Games"],
    "Education": ["Courses", "Books", "Subscriptions"],
    "Travel": ["Flights", "Hotels", "Local Transport", "Sightseeing"],
    "Personal": ["Grooming", "Laundry", "Miscellaneous"],
    "Financial": ["EMI", "Fees", "Taxes", "Investments"],
}


# ---------------------------------------------------------------- errors

def _raise(resp: httpx.Response, fallback: str):
    """Turn a Supabase error body into a clean HTTPException."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    msg = (body.get("msg") or body.get("error_description") or body.get("message")
           or body.get("hint") or fallback)
    # Never surface Postgres/PostgREST internals to the browser.
    code = resp.status_code if resp.status_code in (400, 401, 403, 404, 409, 422, 429) else 502
    raise HTTPException(code, msg)


# ---------------------------------------------------------------- auth

def sign_up(email: str, password: str) -> dict:
    r = http.post(f"{AUTH}/signup", json={"email": email, "password": password})
    if r.status_code == 429:
        # Supabase's built-in mailer is capped at a few messages an hour, so with
        # "Confirm email" on, signups fail here and no account is created at all.
        raise HTTPException(429, "Sign-ups are temporarily blocked by the email provider's "
                                 "rate limit. Turn off 'Confirm email' in the Supabase "
                                 "Authentication settings, or try again in an hour.")
    if r.status_code >= 400:
        _raise(r, "Could not create that account")
    return r.json()


def sign_in(email: str, password: str) -> dict:
    r = http.post(f"{AUTH}/token", params={"grant_type": "password"},
                  json={"email": email, "password": password})
    if r.status_code >= 400:
        _raise(r, "Incorrect email or password")
    return r.json()


def refresh(refresh_token: str) -> dict:
    r = http.post(f"{AUTH}/token", params={"grant_type": "refresh_token"},
                  json={"refresh_token": refresh_token})
    if r.status_code >= 400:
        _raise(r, "Session expired, please sign in again")
    return r.json()


def update_user(token: str, metadata: dict) -> dict:
    """Store per-user preferences in Supabase Auth's user_metadata bag.

    Keeps appearance settings out of the schema entirely: the values ride back
    in the access token's claims, so reading them costs no extra request.
    """
    r = http.put(f"{AUTH}/user", json={"data": metadata},
                 headers={"Authorization": f"Bearer {token}", "apikey": PUBLISHABLE_KEY})
    if r.status_code >= 400:
        _raise(r, "Could not save your preferences")
    return (r.json() or {}).get("user_metadata", {})


def sign_out(token: str):
    http.post(f"{AUTH}/logout", headers={"Authorization": f"Bearer {token}"})


def verify(token: str) -> dict:
    """Verify an access token against the project's JWKS. Returns its claims."""
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["RS256", "ES256"],
                          audience="authenticated", options={"require": ["exp", "sub"]})
    except Exception:
        raise HTTPException(401, "Session expired, please sign in again")


# ---------------------------------------------------------------- data

def _headers(token: str, extra: dict | None = None) -> dict:
    h = {"Authorization": f"Bearer {token}", "apikey": PUBLISHABLE_KEY}
    if extra:
        h.update(extra)
    return h


def select(token: str, table: str, params: dict) -> list:
    r = http.get(f"{REST}/{table}", params=params, headers=_headers(token))
    if r.status_code >= 400:
        _raise(r, "Could not read your data")
    return r.json()


def insert(token: str, table: str, rows, returning: bool = True):
    prefer = "return=representation" if returning else "return=minimal"
    r = http.post(f"{REST}/{table}", json=rows, headers=_headers(token, {"Prefer": prefer}))
    if r.status_code >= 400:
        _raise(r, "Could not save that")
    return r.json() if returning and r.content else []


def update(token: str, table: str, params: dict, patch: dict) -> list:
    r = http.patch(f"{REST}/{table}", params=params, json=patch,
                   headers=_headers(token, {"Prefer": "return=representation"}))
    if r.status_code >= 400:
        _raise(r, "Could not update that")
    return r.json()


def delete(token: str, table: str, params: dict):
    r = http.delete(f"{REST}/{table}", params=params, headers=_headers(token))
    if r.status_code >= 400:
        _raise(r, "Could not delete that")


def seed_user(token: str, user_id: str):
    """Give a new account the default master lookups."""
    cats = insert(token, "categories",
                  [{"user_id": user_id, "name": n} for n in DEFAULT_CATEGORIES])
    by_name = {c["name"]: c["id"] for c in cats}
    subs = [{"user_id": user_id, "category_id": by_name[cat], "name": s}
            for cat, names in DEFAULT_CATEGORIES.items() for s in names]
    insert(token, "subcategories", subs, returning=False)


def health() -> bool:
    r = http.get(f"{REST}/", headers={"apikey": PUBLISHABLE_KEY})
    return r.status_code < 500
