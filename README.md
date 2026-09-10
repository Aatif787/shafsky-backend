# Shafsky Aviation — FastAPI Backend (`shafsky-backend-main`)

Enterprise FastAPI backend for the Shafsky Aviation concierge platform: bookings,
flight intelligence, WhatsApp booking automation, payments (Razorpay),
notifications (Resend email + Meta WhatsApp Cloud API), RBAC authentication,
and observability.

## Stack

- Python 3.13, FastAPI, Uvicorn (dev) + Gunicorn/UvicornWorker (production)
- SQLAlchemy 2.x (sync, `psycopg2-binary`), Alembic migrations
- PostgreSQL — RDS/Neon in production, local Docker (`docker-compose.test.yml`) for tests
- Redis (rate limits, idempotency, locks — optional in dev, required for multi-instance AWS)
- RS256 JWT + HttpOnly refresh cookies
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
deploy/aws/               # ECS Fargate task definition + AWS deploy checklist
tests/                    # pytest suite (isolated test database)
```

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env    # then fill in real credentials

# Optional local services
docker compose -f docker-compose.test.yml up -d
docker compose -f docker-compose.redis.yml up -d
```

## Run

```powershell
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8003
```

Docs are served at `/docs` in development only (disabled in production).

Health endpoints: `/live` (liveness), `/ready` (readiness — **503** when not ready),
`/health` (deep — **503** when the database is unhealthy), `/metrics` (admin-only).

## Tests (isolated database — never against production)

```powershell
docker compose -f docker-compose.test.yml up -d
# TEST_DATABASE_URL is read from .env (see .env.example)
.\.venv\Scripts\python -m pytest -q
```

`tests/conftest.py` refuses to run against the production database host unless
`TEST_DATABASE_URL` is set (or `SHAFSKY_ALLOW_PROD_DB_TESTS=1` is set
deliberately for emergencies).

## Production Docker

```bash
docker build -t shafsky-backend:latest .
docker run --rm -p 4000:4000 \
  -e ENVIRONMENT=production \
  -e DATABASE_URL=... \
  -e JWT_PRIVATE_KEY=... \
  -e JWT_PUBLIC_KEY=... \
  -e JWT_REFRESH_SECRET=... \
  -e ALLOWED_ORIGINS=https://app.example.com \
  -e TRUST_PROXY=true \
  -e REQUIRE_REDIS=true \
  -e REDIS_URL=redis://:pass@elasticache:6379/0 \
  shafsky-backend:latest
```

The image is multi-stage (wheels built in a builder stage) and runs as a non-root
user. `docker-entrypoint.sh` runs `alembic upgrade head` (unless
`RUN_MIGRATIONS=false`) and then Gunicorn with `WEB_CONCURRENCY` Uvicorn workers.
The container `HEALTHCHECK` polls `/ready`, so an instance is only considered
healthy once the database (and Redis, when `REQUIRE_REDIS=true`) is reachable.

`.dockerignore` keeps `.env`, PEM/key files, virtualenvs, and local audit
artifacts out of images.

## AWS deploy

See [`deploy/aws/README.md`](deploy/aws/README.md) for the ECS Fargate + ALB +
RDS + ElastiCache checklist and task definition template.

## Critical production env

| Variable | Notes |
|----------|--------|
| `ENVIRONMENT=production` | Enables fail-fast secrets, secure cookies, docs off |
| `DATABASE_URL` | RDS with TLS (`sslmode=require`) |
| `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` | RS256 PEMs |
| `JWT_REFRESH_SECRET` | Refresh token hashing |
| `ALLOWED_ORIGINS` | Exact frontend origins only |
| `TRUST_PROXY=true` | Behind ALB — rate limits use `X-Forwarded-For` |
| `REQUIRE_REDIS=true` | Multi-task rate limits / idempotency |
| `REDIS_URL` or host/password | ElastiCache |
| `RUN_STARTUP_SCHEMA_PATCHES` | Leave unset/false; use Alembic migrations |

## Operational notes

- Redis: outside `REQUIRE_REDIS=true`, connection failures fall back gracefully
  (30s cooldown) and rate limiting/caching degrade to in-memory implementations.
- CORS: permissive tunnel origins (localhost / ngrok / vercel) apply **only**
  outside production; production honours `ALLOWED_ORIGINS` strictly.
- Startup schema ALTERs are opt-in via `RUN_STARTUP_SCHEMA_PATCHES=true`;
  Alembic is the source of truth for schema changes.
- Disaster recovery: `app/disaster_recovery` produces schema-metadata **drill**
  artifacts only — real recovery relies on RDS automated snapshots/PITR.
- Secrets: never commit `.env`; rotate any credential that has been shared.
