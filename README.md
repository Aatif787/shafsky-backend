# Shafsky Aviation — FastAPI Backend (`shafsky-backend-main`)

Enterprise FastAPI backend for the Shafsky Aviation concierge platform: bookings,
flight intelligence, WhatsApp booking automation, payments (Razorpay),
notifications (Resend email + Meta WhatsApp Cloud API), RBAC authentication,
and observability.

## Stack

- Python 3.13, FastAPI, SQLAlchemy 2.x (sync, `psycopg2-binary`), Alembic migrations
- PostgreSQL — Neon in production, local Docker (`docker-compose.test.yml`) for tests
- Redis (optional — the app degrades gracefully with an in-memory fallback)
- Resend (email), Meta WhatsApp Cloud API, Razorpay (payments)
- AviationEdge (primary flight data), AviationStack (secondary)

## Repository layout

```
app/
  main.py                 # FastAPI app, middleware, health endpoints
  config.py               # Pydantic settings (reads .env)
  routers/                # REST endpoints
  services/               # Business logic (booking, auth, crm, payment, ...)
  integrations/whatsapp/  # WhatsApp Cloud API client + booking state machine
  flight/                 # Flight data providers (AviationEdge / AviationStack)
  security/               # JWT (RS256 + kid rotation), rate limiting, headers
  models/                 # SQLAlchemy models
alembic/                  # Database migrations
tests/                    # pytest suite (isolated test database)
```

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env    # then fill in real credentials
```

## Run

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8003
```

Health endpoints: `/health` (deep), `/ready`, `/live`, `/metrics` (admin-only).

## Database migrations

```powershell
.\.venv\Scripts\python -m alembic upgrade head
```

## Tests (isolated database — never against production)

```powershell
docker compose -f docker-compose.test.yml up -d
# TEST_DATABASE_URL is read from .env (see .env.example)
.\.venv\Scripts\python -m pytest -q
```

`tests/conftest.py` refuses to run against the production Neon host unless
`TEST_DATABASE_URL` is set (or `SHAFSKY_ALLOW_PROD_DB_TESTS=1` is set
deliberately for emergencies).

## Docker

```powershell
docker build -t shafsky-backend .
docker run --rm -p 4000:4000 --env-file .env shafsky-backend
```

`.dockerignore` keeps `.env`, virtualenvs, and local audit artifacts out of images.
The container healthcheck uses `/live`; `ENV PORT` (default 4000) is honoured by CMD.

## Operational notes

- Redis: connection failures fall back gracefully (30s cooldown); rate limiting
  and caching degrade to in-memory implementations.
- CORS: permissive tunnel origins (localhost / ngrok / vercel) apply **only**
  outside production; production honours `ALLOWED_ORIGINS` strictly.
- Secrets: never commit `.env`; rotate any credential that has been shared.
