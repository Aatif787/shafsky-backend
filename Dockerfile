# Production hardened multi-stage Dockerfile for Shafsky FastAPI backend

FROM python:3.11.8-slim AS builder

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

FROM python:3.11.8-slim AS runtime
# TODO: Pin the above image to an explicit digest for supply-chain integrity, e.g.: python@sha256:...

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user
RUN addgroup --system shafsky && adduser --system --ingroup shafsky shafsky

WORKDIR /app

# Install runtime deps from built wheels for reproducible installs
COPY --from=builder /wheels /wheels
COPY --from=builder /src/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels -r /app/requirements.txt \
    && rm -rf /wheels

# Copy app code and set ownership
COPY --from=builder /src /app
RUN chown -R shafsky:shafsky /app

USER shafsky

EXPOSE 4000

# Lightweight Python healthcheck (uses stdlib urllib)
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request, sys; resp=urllib.request.urlopen('http://127.0.0.1:4000/health'); sys.exit(0 if resp.status==200 else 1)"

ENV PORT=4000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "4000"]
