# Do not import app.main here. Alembic loads `app.database` during pre-deploy,
# and an eager import of main.py would run secret validation before migrations.
# gunicorn app:app still works via __getattr__.
__all__ = ["app"]


def __getattr__(name: str):
    if name == "app":
        from app.main import app as fastapi_app

        return fastapi_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
