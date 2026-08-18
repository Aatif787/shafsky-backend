import time
import threading
from typing import Dict, Tuple, Optional
from fastapi import Request, HTTPException
import logging

from app.config import settings

logger = logging.getLogger("shafsky.security.rate_limit")


class RateLimiter:
    """Rate limiter that uses Redis if available, falling back to in-memory counters.

    To enable Redis-based limiting, set `REDIS_URL` in environment and ensure
    `redis` Python package is installed. If Redis is unavailable the limiter
    gracefully falls back to an in-process implementation (not suitable for
    multi-instance deployments).
    """

    _storage: Dict[str, Tuple[int, float]] = {}
    _lock = threading.Lock()
    _redis = None

    # Attempt to initialize Redis client lazily
    try:
        redis_url = getattr(settings, "REDIS_URL", None)
        if redis_url:
            import redis

            _redis = redis.Redis.from_url(redis_url, decode_responses=True)
            # quick ping to validate connection
            try:
                _redis.ping()
                logger.info("RateLimiter: connected to Redis at %s", redis_url)
            except Exception as e:
                logger.warning("RateLimiter: Redis ping failed, falling back to in-memory: %s", e)
                _redis = None
    except Exception as e:
        logger.warning("RateLimiter: redis client not available: %s", e)
        _redis = None

    @classmethod
    def check_rate_limit(cls, key: str, max_requests: int = 100, window_seconds: int = 60):
        now = time.time()

        # Use Redis-backed counter when available
        if cls._redis:
            try:
                count = cls._redis.incr(key)
                if count == 1:
                    cls._redis.expire(key, window_seconds)

                if count > max_requests:
                    ttl = cls._redis.ttl(key)
                    retry_after = int(ttl if ttl and ttl > 0 else window_seconds)
                    raise HTTPException(
                        status_code=429,
                        detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                        headers={"Retry-After": str(retry_after)}
                    )
                return
            except HTTPException:
                raise
            except Exception as e:
                logger.warning("Redis rate limiter failed; falling back to local limiter: %s", e)

        # In-process fallback (not distributed)
        with cls._lock:
            count, reset_at = cls._storage.get(key, (0, now + window_seconds))
            if now > reset_at:
                count = 0
                reset_at = now + window_seconds

            count += 1
            cls._storage[key] = (count, reset_at)

            if count > max_requests:
                retry_after = int(reset_at - now)
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                    headers={"Retry-After": str(retry_after)}
                )
