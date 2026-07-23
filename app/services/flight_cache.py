import time
import threading
from typing import Optional, Any, Dict
from app.config import settings

class FlightCache:
    _storage: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()

    @classmethod
    def default_ttl(cls) -> int:
        return int(getattr(settings, "FLIGHT_CACHE_TTL_SECONDS", 300))

    @classmethod
    def get(cls, key: str) -> Optional[Any]:
        with cls._lock:
            entry = cls._storage.get(key)
            if not entry:
                return None
            if time.time() > entry["expires_at"]:
                del cls._storage[key]
                return None
            return entry["data"]

    @classmethod
    def set(cls, key: str, value: Any, ttl: Optional[int] = None):
        if ttl is None:
            ttl = cls.default_ttl()
        expires_at = time.time() + ttl
        with cls._lock:
            cls._storage[key] = {
                "data": value,
                "expires_at": expires_at
            }

    @classmethod
    def delete(cls, key: str):
        with cls._lock:
            cls._storage.pop(key, None)

    @classmethod
    def clear_expired(cls) -> int:
        now = time.time()
        removed = 0
        with cls._lock:
            expired_keys = [k for k, v in cls._storage.items() if now > v["expires_at"]]
            for k in expired_keys:
                del cls._storage[k]
                removed += 1
        return removed
