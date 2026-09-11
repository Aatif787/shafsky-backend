import time
import threading
from typing import Dict, Tuple, Optional
from fastapi import Request, HTTPException
import logging

from app.config import settings

logger = logging.getLogger("shafsky.security.rate_limit")


def _safe_log_key(key: str) -> str:
    """Mask client identifiers (IPs, tokens) when logging rate limit events."""
    parts = key.split(":", 1)
    if len(parts) == 2:
        prefix, ident = parts
        if "." in ident:
            ip_parts = ident.split(".")
            masked = ".".join(ip_parts[:2]) + ".x.x"
            return f"{prefix}:{masked}"
        elif ":" in ident:
            return f"{prefix}:ipv6_masked"
        elif len(ident) > 6:
            return f"{prefix}:{ident[:3]}***{ident[-3:]}"
        return f"{prefix}:***"
    return parts[0]


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

    @classmethod
    def clear(cls) -> None:
        """Clear in-memory storage (primarily for testing)."""
        with cls._lock:
            cls._storage.clear()

    @classmethod
    def _get_redis(cls):
        if cls._redis is not None:
            return cls._redis
        try:
            from app.core.redis import get_redis_client

            cls._redis = get_redis_client()
        except Exception as exc:
            logger.warning("RateLimiter: Redis client unavailable: %s", exc)
            cls._redis = None
        return cls._redis

    @classmethod
    def check_rate_limit(cls, key: str, max_requests: int = 100, window_seconds: int = 60):
        now = time.time()

        # Use the centralized reconnecting Redis client when available.
        redis_client = cls._get_redis()
        if redis_client:
            try:
                count = redis_client.incr(key)
                if count == 1:
                    redis_client.expire(key, window_seconds)

                if count > max_requests:
                    ttl = redis_client.ttl(key)
                    retry_after = int(ttl if ttl and ttl > 0 else window_seconds)
                    logger.warning(
                        "Rate limit exceeded for %s: count=%d > max=%d (window=%ds, retry_after=%ds)",
                        _safe_log_key(key),
                        count,
                        max_requests,
                        window_seconds,
                        retry_after,
                    )
                    raise HTTPException(
                        status_code=429,
                        detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                        headers={"Retry-After": str(retry_after)}
                    )
                return
            except HTTPException:
                raise
            except Exception as e:
                cls._redis = None
                if getattr(settings, "REQUIRE_REDIS", False):
                    logger.error(
                        "Redis rate limiter failed with REQUIRE_REDIS=true; refusing in-memory fallback: %s",
                        e,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail="Rate limiting unavailable. Please retry shortly.",
                    ) from e
                logger.warning("Redis rate limiter failed; falling back to local limiter: %s", e)

        if getattr(settings, "REQUIRE_REDIS", False):
            raise HTTPException(
                status_code=503,
                detail="Rate limiting unavailable (Redis required).",
            )

        if getattr(settings, "is_production", False) and cls._redis is None:
            logger.warning(
                "RateLimiter using in-memory fallback in production — "
                "set REQUIRE_REDIS=true and ElastiCache for multi-instance safety."
            )

        # In-process fallback (not distributed)
        with cls._lock:
            # Periodic / capacity-based cleanup of expired entries
            if len(cls._storage) > 10000:
                expired_keys = [k for k, (_, exp) in cls._storage.items() if now > exp]
                for k in expired_keys:
                    del cls._storage[k]
                # If still at capacity, discard oldest entries to prevent memory exhaustion DoS
                if len(cls._storage) > 10000:
                    sorted_by_expiry = sorted(cls._storage.items(), key=lambda item: item[1][1])
                    for k, _ in sorted_by_expiry[:2000]:
                        cls._storage.pop(k, None)

            count, reset_at = cls._storage.get(key, (0, now + window_seconds))
            if now > reset_at:
                count = 0
                reset_at = now + window_seconds

            count += 1
            cls._storage[key] = (count, reset_at)

            if count > max_requests:
                retry_after = max(1, int(reset_at - now))
                logger.warning(
                    "Rate limit exceeded for %s: count=%d > max=%d (window=%ds, retry_after=%ds)",
                    _safe_log_key(key),
                    count,
                    max_requests,
                    window_seconds,
                    retry_after,
                )
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                    headers={"Retry-After": str(retry_after)}
                )
