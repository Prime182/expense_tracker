# OXY FIN+

_by THE OXY_

OXY FIN+ is multi-user personal expense tracking with automated analytics, anomaly detection and
six reporting pages. FastAPI + Supabase + vanilla JS/Chart.js. No build step.

Accounts are Supabase Auth. Data lives in Supabase Postgres behind Row Level Security
and is read with the signed-in user's own token, so the database — not application
code — is what enforces that one person only ever sees their own expenses.

## How it works

You enter six fields per transaction — Date, Amount, Category, Sub-category, Description,
Payment method. Everything else is derived on read: time attributes (month, quarter, week,
weekday/weekend), KPIs, month-over-month comparisons, category shares, outlier flags and
narrative insights. There are no stored aggregates to maintain and no model to redesign as
years accumulate.

## Files

| File | Purpose |
|---|---|
| `app.py` | HTTP API, validation, static hosting |
| `supa.py` | Supabase auth, PostgREST data access, JWT verification |
| `schema.sql` | Tables and RLS policies — run once in the Supabase SQL Editor |
| `analytics.py` | All aggregation. Pure functions over row dicts, no DB or framework |
| `static/` | Single-page client (`index.html`, `app.js`, `style.css`) |
| `test_analytics.py` | Self-check for the aggregation logic |

## Run locally

First, create the tables: open the Supabase dashboard → SQL Editor → New query, paste
`schema.sql`, and Run. This is required once per project and is safe to repeat.

Then copy `.env.example` to `.env`, fill in the values from Supabase → Project Settings →
API, and start the app:

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/uvicorn app:app --port 8000
```

Then open http://localhost:8000.

```bash
python3 test_analytics.py    # aggregation self-check
```

## Environment variables

| Variable | Required | Notes |
|---|---|---|
| `SUPABASE_URL` | yes | Project URL, e.g. `https://<ref>.supabase.co` |
| `SUPABASE_PUBLISHABLE_KEY` | yes | Public key. Safe to expose; RLS is what protects the data. |
| `SUPABASE_SECRET_KEY` | yes | Service key. Server-side only — it bypasses RLS. |
| `SUPABASE_JWKS_URL` | no | Defaults to `<SUPABASE_URL>/auth/v1/.well-known/jwks.json` |
| `INVITE_CODE` | no | Signup is refused unless it matches. **Unset means signup is open to anyone with the URL.** |

Accounts are created through Supabase's admin endpoint with `email_confirm`, so no
confirmation mail is sent and the built-in mailer's few-per-hour cap never blocks a
signup. `INVITE_CODE` is what gates access. Without `SUPABASE_SECRET_KEY` set, signup
falls back to the public endpoint and the project's own confirm-by-email flow.

## Deploying

`render.yaml` defines the service. Every secret is marked `sync: false`, so set the values
in the Render dashboard rather than committing them. Run `schema.sql` against the Supabase
project before the first deploy.

## Notes

- Passwords are never seen by this app; Supabase Auth handles them. Access tokens are
  verified against the project JWKS on every request and refreshed transparently.
- Isolation is enforced by RLS policies in Postgres, not by application code.
- Anomaly detection uses a median/MAD modified z-score scoped per category, so an expensive
  category doesn't flood the panel and a lone outlier can't inflate its own baseline.
- `analytics.py` aggregates a user's full history in Python per request. Comfortable to
  ~100k rows; past that, push the filters into SQL and cache.
