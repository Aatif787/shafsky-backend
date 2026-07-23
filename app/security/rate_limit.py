import time
import threading
from typing import Dict, Tuple
from fastapi import Request, HTTPException

class RateLimiter:
    _storage: Dict[str, Tuple[int, float]] = {}
    _lock = threading.Lock()

    @classmethod
    def check_rate_limit(cls, key: str, max_requests: int = 100, window_seconds: int = 60):
        now = time.time()
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
