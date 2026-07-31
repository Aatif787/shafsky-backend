"""
Redis Integration and Connection Client with Fallback Resilience.

Provides singleton connection pool management to Redis server using app settings.
Safely handles connection errors and returns None when Redis is unreachable.
"""

import logging
from typing import Optional
import redis
from app.config import settings

logger = logging.getLogger("shafsky.integrations.redis")

_redis_client: Optional[redis.Redis] = None
_redis_available: Optional[bool] = None


def get_redis_client() -> Optional[redis.Redis]:
    """
    Returns active Redis client instance or None if Redis is unreachable/disabled.
    """
    global _redis_client, _redis_available

    if _redis_available is False:
        return None

    if _redis_client is not None:
        try:
            # Quick ping test to verify connection health
            _redis_client.ping()
            return _redis_client
        except Exception:
            logger.warning("Redis ping failed. Resetting client instance.")
            _redis_client = None

    try:
        host = getattr(settings, "REDIS_HOST", "localhost")
        port = int(getattr(settings, "REDIS_PORT", 6379))
        password = getattr(settings, "REDIS_PASSWORD", None) or None

        client = redis.Redis(
            host=host,
            port=port,
            password=password,
            decode_responses=True,
            socket_timeout=1.5,
            socket_connect_timeout=1.5,
            retry_on_timeout=False
        )
        client.ping()
        _redis_client = client
        _redis_available = True
        logger.info(f"Connected to Redis at {host}:{port}")
        return _redis_client
    except Exception as err:
        logger.warning(f"Redis unavailable ({err}). Fallback mechanism active.")
        _redis_client = None
        # Don't permanently disable so reconnection can be attempted on next call
        return None


def reset_redis_connection_state() -> None:
    """Reset cached connection state (primarily for unit test isolation)."""
    global _redis_client, _redis_available
    _redis_client = None
    _redis_available = None
