# Re-export the FastAPI application instance so that both
#   gunicorn app:app          (Render default)
#   gunicorn app.main:app     (docker-entrypoint.sh)
# resolve to the same ASGI application object.
from app.main import app  # noqa: F401

__all__ = ["app"]
