#!/bin/sh
# Production entrypoint: optional migrations, then multi-worker ASGI server.
set -eu

PORT="${PORT:-4000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-2}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-true}"

if [ "$RUN_MIGRATIONS" = "true" ] || [ "$RUN_MIGRATIONS" = "1" ]; then
  echo "[entrypoint] Running alembic upgrade head..."
  alembic upgrade head
  echo "[entrypoint] Migrations complete."
fi

echo "[entrypoint] Starting gunicorn (workers=${WEB_CONCURRENCY}) on 0.0.0.0:${PORT}"
exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind "0.0.0.0:${PORT}" \
  --workers "${WEB_CONCURRENCY}" \
  --timeout "${GUNICORN_TIMEOUT:-120}" \
  --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
  --keep-alive "${GUNICORN_KEEPALIVE:-5}" \
  --access-logfile - \
  --error-logfile -
