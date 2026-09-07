"""Postgres access: pool, schema, per-user seeding."""
import os
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "")
if DSN.startswith("postgres://"):  # SQLAlchemy-style URL some providers hand out
    DSN = DSN.replace("postgres://", "postgresql://", 1)

# prepare_threshold=None disables server-side prepared statements. Supabase's transaction
# pooler (port 6543) hands each transaction a different backend, so a prepared statement
# from one request is missing on the next -- this is the standard pgbouncer workaround.
pool = ConnectionPool(DSN, min_size=1, max_size=5, open=False,
                      kwargs={"row_factory": dict_row, "prepare_threshold": None})

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id          bigserial PRIMARY KEY,
  email       text NOT NULL UNIQUE,
  pw_hash     text NOT NULL,
  currency    text NOT NULL DEFAULT 'INR',
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS categories (
  id       bigserial PRIMARY KEY,
  user_id  bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name     text NOT NULL,
  UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS subcategories (
  id           bigserial PRIMARY KEY,
  category_id  bigint NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  name         text NOT NULL,
  UNIQUE (category_id, name)
);

CREATE TABLE IF NOT EXISTS expenses (
  id              bigserial PRIMARY KEY,
  user_id         bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  txn_date        date NOT NULL,
  amount          numeric(14,2) NOT NULL CHECK (amount > 0),
  category_id     bigint NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
  subcategory_id  bigint REFERENCES subcategories(id) ON DELETE SET NULL,
  description     text NOT NULL DEFAULT '',
  payment_method  text NOT NULL CHECK (payment_method IN ('Cash','UPI','Debit','Others')),
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS expenses_user_date ON expenses (user_id, txn_date);

CREATE TABLE IF NOT EXISTS limits (
  id           bigserial PRIMARY KEY,
  user_id      bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN ('daily','monthly','category')),
  category_id  bigint REFERENCES categories(id) ON DELETE CASCADE,
  amount       numeric(14,2) NOT NULL CHECK (amount > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS limits_unique
  ON limits (user_id, kind, COALESCE(category_id, 0));
"""

# Master lookup defaults, seeded per user so each account owns its own config.
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

PAYMENT_METHODS = ["Cash", "UPI", "Debit", "Others"]


def init():
    pool.open()
    with pool.connection() as conn:
        conn.execute(SCHEMA)


def seed_user(conn, user_id):
    with conn.cursor() as cur:
        for cat, subs in DEFAULT_CATEGORIES.items():
            cur.execute("INSERT INTO categories (user_id, name) VALUES (%s, %s) RETURNING id",
                        (user_id, cat))
            cid = cur.fetchone()["id"]
            cur.executemany("INSERT INTO subcategories (category_id, name) VALUES (%s, %s)",
                            [(cid, s) for s in subs])
