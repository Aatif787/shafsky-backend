# Production hardened multi-stage Dockerfile for Shafsky FastAPI backend

# python:3.13-slim aligns the container runtime with the tested dev/test Python (3.13.x).
# TODO: pin to an explicit digest for supply-chain integrity, e.g.:
#   docker buildx imagetools inspect python:3.13-slim
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src

# Install build deps only in builder stage
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# Build wheels to avoid compiling packages in the runtime image
RUN python -m pip install --upgrade pip setuptools wheel \
    && pip wheel --wheel-dir=/wheels -r requirements.txt

COPY . /src

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production \
    PORT=4000 \
    WEB_CONCURRENCY=2 \
    RUN_MIGRATIONS=true

# Create a non-root user
RUN addgroup --system shafsky && adduser --system --ingroup shafsky shafsky

WORKDIR /app

# Runtime shared libs for psycopg2-binary on slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Install runtime deps from built wheels for reproducible installs
COPY --from=builder /wheels /wheels
COPY --from=builder /src/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels -r /app/requirements.txt \
    && rm -rf /wheels

# Copy app code (exclude secrets via .dockerignore)
COPY --from=builder /src /app
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

# Ensure airports.csv exists for global airport search (use repo copy if present)
RUN mkdir -p /app/data \
    && if [ ! -f /app/data/airports.csv ]; then \
         apt-get update \
         && apt-get install -y --no-install-recommends curl ca-certificates \
         && curl -fsSL -o /app/data/airports.csv \
              "https://davidmegginson.github.io/ourairports-data/airports.csv" \
         && apt-get purge -y curl \
         && apt-get autoremove -y \
         && rm -rf /var/lib/apt/lists/*; \
       fi \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R shafsky:shafsky /app

USER shafsky

EXPOSE 4000

# Readiness-based healthcheck (stdlib urllib; /ready returns 503 when DB/Redis not ready)
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c 'import os, urllib.request, sys; port=os.environ.get("PORT", "4000"); resp=urllib.request.urlopen("http://127.0.0.1:%s/ready" % port); sys.exit(0 if resp.status == 200 else 1)'

# Entrypoint runs optional alembic migrations then gunicorn/uvicorn workers
ENTRYPOINT ["/app/docker-entrypoint.sh"]
