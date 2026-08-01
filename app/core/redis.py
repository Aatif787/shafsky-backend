"""
Centralized Redis Connection Client and Health Diagnostics.

Provides singleton connection pool management, health checks, latency monitoring,
and structured logging for Redis infrastructure.
"""

import time
import logging
from typing import Optional, Dict, Any
import redis
from app.config import settings

logger = logging.getLogger("shafsky.core.redis")

_redis_client: Optional[redis.Redis] = None
_redis_pool: Optional[redis.ConnectionPool] = None


def get_redis_client() -> Optional[redis.Redis]:
    """
    Retrieves or initializes the centralized Redis client connection.
    Returns None gracefully if Redis server is unreachable.
    """
    global _redis_client, _redis_pool

    if _redis_client is not None:
        try:
            _redis_client.ping()
            return _redis_client
        except Exception as err:
            logger.warning(f"Redis connection dropped ({err}). Reconnecting...")
            _redis_client = None

    try:
        host = getattr(settings, "REDIS_HOST", "localhost")
        port = int(getattr(settings, "REDIS_PORT", 6379))
        password = getattr(settings, "REDIS_PASSWORD", None) or None

        if _redis_pool is None:
            _redis_pool = redis.ConnectionPool(
                host=host,
                port=port,
                password=password,
                decode_responses=True,
                max_connections=20,
                socket_timeout=1.5,
                socket_connect_timeout=1.5,
                retry_on_timeout=False
            )

        client = redis.Redis(connection_pool=_redis_pool)
        client.ping()
        _redis_client = client
        logger.info(f"Centralized Redis client connected to {host}:{port}")
        return _redis_client
    except Exception as err:
        logger.warning(f"Redis connection failed ({err}). Graceful fallback active.")
        _redis_client = None
        return None


def check_redis_health() -> Dict[str, Any]:
    """
    Executes a health check diagnostic ping against the Redis cluster.
    Returns status, latency_ms, host, port, and connection details.
    """
    host = getattr(settings, "REDIS_HOST", "localhost")
    port = int(getattr(settings, "REDIS_PORT", 6379))
    start_time = time.perf_counter()

    client = get_redis_client()
    if client is None:
        return {
            "status": "unhealthy",
            "connected": False,
            "host": host,
            "port": port,
            "latency_ms": None,
            "error": "Failed to connect to Redis instance."
        }

    try:
        client.ping()
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        info = client.info(section="server")
        return {
            "status": "healthy",
            "connected": True,
            "host": host,
            "port": port,
            "latency_ms": latency_ms,
            "redis_version": info.get("redis_version", "unknown")
        }
    except Exception as err:
        return {
            "status": "unhealthy",
            "connected": False,
            "host": host,
            "port": port,
            "latency_ms": None,
            "error": str(err)
        }


def reset_redis_client() -> None:
    """Reset cached connection pool and client instance (useful for unit tests/reconnects)."""
    global _redis_client, _redis_pool
    if _redis_client:
        try:
            _redis_client.close()
        except Exception:
            pass
    _redis_client = None
    _redis_pool = None
