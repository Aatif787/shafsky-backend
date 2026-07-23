import time
import threading
from typing import Dict, Any, Optional
from app.integrations.aerodatabox.constants import (
    FLIGHT_CACHE_TTL,
    AIRPORT_CACHE_TTL,
    AIRLINE_CACHE_TTL,
    AIRCRAFT_CACHE_TTL
)
from app.monitoring.metrics import PrometheusMetricsCollector

class FlightCacheManager:
    _cache: Dict[str, tuple[Any, float]] = {}
    _lock = threading.Lock()

    @classmethod
    def format_flight_key(cls, flight_number: str, date: str) -> str:
        clean_fn = flight_number.strip().upper().replace(" ", "")
        return f"flight:{clean_fn}:{date}"

    @classmethod
    def format_airport_key(cls, iata: str) -> str:
        return f"airport:{iata.strip().upper()}"

    @classmethod
    def format_airline_key(cls, iata: str) -> str:
        return f"airline:{iata.strip().upper()}"

    @classmethod
    def format_aircraft_key(cls, reg: str) -> str:
        return f"aircraft:{reg.strip().upper()}"

    @classmethod
    def get(cls, key: str) -> Optional[Any]:
        with cls._lock:
            if key in cls._cache:
                data, expire_at = cls._cache[key]
                if time.time() < expire_at:
                    PrometheusMetricsCollector.record_flight_cache_hit()
                    return data
                else:
                    del cls._cache[key]
        PrometheusMetricsCollector.record_flight_cache_miss()
        return None

    @classmethod
    def set(cls, key: str, data: Any, ttl_seconds: int = FLIGHT_CACHE_TTL):
        with cls._lock:
            cls._cache[key] = (data, time.time() + ttl_seconds)

    @classmethod
    def invalidate(cls, key: str):
        with cls._lock:
            cls._cache.pop(key, None)

    @classmethod
    def clear_expired(cls) -> int:
        now = time.time()
        evicted = 0
        with cls._lock:
            expired_keys = [k for k, (_, exp) in cls._cache.items() if now >= exp]
            for k in expired_keys:
                del cls._cache[k]
                evicted += 1
        return evicted
