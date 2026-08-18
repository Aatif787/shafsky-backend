import os
import time
import redis
import pytest
from starlette.testclient import TestClient

from app.security.rate_limit import RateLimiter


@pytest.mark.integration
def test_rate_limiter_redis_integration():
    """Starts against a local Redis instance (docker-compose.redis.yml) and
    verifies that the Redis-backed rate limiter enforces limits.
    """
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    r = redis.Redis.from_url(redis_url, decode_responses=True)

    # Ensure Redis is reachable
    try:
        r.ping()
    except Exception as exc:
        pytest.skip(f"Redis not available at {redis_url}: {exc}")

    # Point the RateLimiter at the local Redis client
    RateLimiter._redis = r

    # Use a short window and low limit to keep test fast
    key = "test_rate_limit:integration:1"
    r.delete(key)

    max_requests = 5
    window_seconds = 2

    # First max_requests should succeed (no exception)
    for i in range(max_requests):
        RateLimiter.check_rate_limit(key, max_requests=max_requests, window_seconds=window_seconds)

    # The next request should raise HTTPException due to rate limiting
    with pytest.raises(Exception):
        RateLimiter.check_rate_limit(key, max_requests=max_requests, window_seconds=window_seconds)

    # Wait for window to expire and ensure we can issue requests again
    time.sleep(window_seconds + 0.1)
    # Should not raise now
    RateLimiter.check_rate_limit(key, max_requests=max_requests, window_seconds=window_seconds)
