"""
Database Connection & Engine Singleton Module.
Configures persistent, high-performance PostgreSQL connection pooling (QueuePool)
with LIFO checkout, TCP keepalive socket settings, and proactive health checks
to eliminate connection handshake latency across requests.
"""

from typing import Dict, Any
from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import QueuePool
from app.config import settings


def _database_url() -> str:
    url = (settings.DATABASE_URL or "").replace("postgres://", "postgresql://", 1)
    if settings.is_production and url and "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url




def _json_serializer(value):
    """JSON serializer that tolerates Decimal / datetime values in JSON columns."""
    import json as _json
    from decimal import Decimal as _Decimal
    import datetime as _dt

    def _default(obj):
        if isinstance(obj, _Decimal):
            return float(obj)
        if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
            return obj.isoformat()
        return str(obj)

    return _json.dumps(value, default=_default)

def _create_database_engine() -> Engine:
    """
    Creates a singleton SQLAlchemy Engine tuned for minimal latency and persistent pooling.
    """
    db_url = _database_url()

    if db_url.startswith("sqlite"):
        return create_engine(
            db_url,
            connect_args={"check_same_thread": False},
        )

    # Optimized PostgreSQL connect arguments (TCP keepalive to prevent silent socket drops)
    pg_connect_args: Dict[str, Any] = {
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
        "connect_timeout": 10,
    }

    return create_engine(
        db_url,
        json_serializer=_json_serializer,
        poolclass=QueuePool,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_recycle=300,        # Recycle connections every 5 minutes to prevent stale cloud drops
        pool_pre_ping=True,      # Proactively verify connection health before query execution
        pool_use_lifo=True,      # Re-use most recently used connection to keep database buffers hot
        connect_args=pg_connect_args,
    )


# Singleton Engine & Session Factory
engine = _create_database_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency for yielding transactional database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
