"""
Idempotency and Distributed Locking Manager with In-Memory Fallback.

Key Naming Strategy:
- Locks:      `idempotency:lock:{idempotency_key}`
- Responses:  `idempotency:response:{idempotency_key}`
"""

import json
import time
import threading
import logging
from typing import Optional, Dict, Any, Tuple
from app.integrations.redis_client import get_redis_client

logger = logging.getLogger("shafsky.integrations.idempotency")


class InMemoryIdempotencyStore:
    """Thread-safe in-memory store for locks and cached responses (fallback when Redis is offline)."""
    _instance_lock = threading.Lock()
    _locks: Dict[str, float] = {}
    _responses: Dict[str, Tuple[Dict[str, Any], float]] = {}

    @classmethod
    def acquire_lock(cls, key: str, ttl_seconds: int = 30) -> bool:
        with cls._instance_lock:
            now = time.time()
            cls._clean_expired(now)
            expire_at = cls._locks.get(key)
            if expire_at and expire_at > now:
                return False
            cls._locks[key] = now + ttl_seconds
            return True

    @classmethod
    def release_lock(cls, key: str) -> None:
        with cls._instance_lock:
            cls._locks.pop(key, None)

    @classmethod
    def get_cached_response(cls, key: str) -> Optional[Dict[str, Any]]:
        with cls._instance_lock:
            now = time.time()
            cls._clean_expired(now)
            item = cls._responses.get(key)
            if not item:
                return None
            data, expire_at = item
            if expire_at <= now:
                cls._responses.pop(key, None)
                return None
            return data

    @classmethod
    def set_cached_response(cls, key: str, data: Dict[str, Any], ttl_seconds: int = 86400) -> None:
        with cls._instance_lock:
            now = time.time()
            cls._responses[key] = (data, now + ttl_seconds)

    @classmethod
    def _clean_expired(cls, now: float) -> None:
        expired_locks = [k for k, exp in cls._locks.items() if exp <= now]
        for k in expired_locks:
            cls._locks.pop(k, None)
        expired_resps = [k for k, (_, exp) in cls._responses.items() if exp <= now]
        for k in expired_resps:
            cls._responses.pop(k, None)

    @classmethod
    def clear(cls) -> None:
        with cls._instance_lock:
            cls._locks.clear()
            cls._responses.clear()


class IdempotencyManager:
    @staticmethod
    def _make_lock_key(idempotency_key: str) -> str:
        return f"idempotency:lock:{idempotency_key}"

    @staticmethod
    def _make_response_key(idempotency_key: str) -> str:
        return f"idempotency:response:{idempotency_key}"

    @classmethod
    def acquire_lock(cls, idempotency_key: str, ttl_seconds: int = 30) -> bool:
        """
        Attempts to acquire a distributed lock for the given idempotency key.
        Returns True if lock acquired, False if lock is held by another request.
        """
        lock_key = cls._make_lock_key(idempotency_key)
        client = get_redis_client()

        if client is not None:
            try:
                acquired = client.set(lock_key, "1", nx=True, ex=ttl_seconds)
                return bool(acquired)
            except Exception as err:
                logger.warning(f"Redis lock acquisition error ({err}). Falling back to memory store.")

        # Fallback to thread-safe in-memory store
        return InMemoryIdempotencyStore.acquire_lock(idempotency_key, ttl_seconds)

    @classmethod
    def release_lock(cls, idempotency_key: str) -> None:
        """Releases the distributed lock for the given idempotency key."""
        lock_key = cls._make_lock_key(idempotency_key)
        client = get_redis_client()

        if client is not None:
            try:
                client.delete(lock_key)
            except Exception as err:
                logger.warning(f"Redis lock release error ({err}).")

        InMemoryIdempotencyStore.release_lock(idempotency_key)

    @classmethod
    def get_cached_response(cls, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves cached response dictionary for the given idempotency key.
        Returns dict with status_code, headers, body if hit, else None.
        """
        resp_key = cls._make_response_key(idempotency_key)
        client = get_redis_client()

        if client is not None:
            try:
                raw_data = client.get(resp_key)
                if raw_data:
                    return json.loads(raw_data)
            except Exception as err:
                logger.warning(f"Redis cache fetch error ({err}). Falling back to memory store.")

        return InMemoryIdempotencyStore.get_cached_response(idempotency_key)

    @classmethod
    def set_cached_response(
        cls,
        idempotency_key: str,
        status_code: int,
        headers: Dict[str, str],
        body: str,
        ttl_seconds: int = 86400
    ) -> None:
        """
        Caches successful response for the given idempotency key.
        """
        resp_key = cls._make_response_key(idempotency_key)
        data = {
            "status_code": status_code,
            "headers": headers,
            "body": body
        }
        client = get_redis_client()

        if client is not None:
            try:
                client.set(resp_key, json.dumps(data), ex=ttl_seconds)
            except Exception as err:
                logger.warning(f"Redis cache set error ({err}). Falling back to memory store.")

        InMemoryIdempotencyStore.set_cached_response(idempotency_key, data, ttl_seconds)
