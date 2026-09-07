# Expense Tracker

Multi-user personal expense tracking with automated analytics, anomaly detection and
six reporting pages. FastAPI + Postgres (Supabase) + vanilla JS/Chart.js. No build step.

## How it works

You enter six fields per transaction — Date, Amount, Category, Sub-category, Description,
Payment method. Everything else is derived on read: time attributes (month, quarter, week,
weekday/weekend), KPIs, month-over-month comparisons, category shares, outlier flags and
narrative insights. There are no stored aggregates to maintain and no model to redesign as
years accumulate.

## Files

| File | Purpose |
|---|---|
| `app.py` | HTTP API, auth, validation, static hosting |
| `db.py` | Connection pool, schema, per-user seed data |
| `analytics.py` | All aggregation. Pure functions over row dicts, no DB or framework |
| `static/` | Single-page client (`index.html`, `app.js`, `style.css`) |
| `test_analytics.py` | Self-check for the aggregation logic |

## Run locally

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
export INVITE_CODE="something-secret"     # required to create an account
export SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
./venv/bin/uvicorn app:app --port 8000
```

Then open http://localhost:8000. Schema is created automatically on startup.

```bash
python3 test_analytics.py    # aggregation self-check
```

## Environment variables

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | Postgres URL. Use Supabase's **pooler** host — the direct host is IPv6-only and Render dials IPv4. |
| `INVITE_CODE` | yes | Signup is refused without a match. Leave unset only if you want open signup. |
| `SECRET_KEY` | yes | Signs the session cookie. Changing it signs everyone out. |
| `RENDER` | auto | Set by Render; switches the session cookie to HTTPS-only. |

## Deploying

`render.yaml` defines the service. `DATABASE_URL` and `INVITE_CODE` are marked `sync: false`,
so set them in the Render dashboard rather than committing them.

## Notes

- Passwords are hashed with scrypt (n=2^14). Sessions are signed cookies, 30-day expiry.
- Every query is scoped by `user_id`; ownership is re-checked on write.
- Anomaly detection uses a median/MAD modified z-score scoped per category, so an expensive
  category doesn't flood the panel and a lone outlier can't inflate its own baseline.
- `analytics.py` aggregates a user's full history in Python per request. Comfortable to
  ~100k rows; past that, push the filters into SQL and cache.
