"""
Idempotency Service for Managing Distributed Locks and Response Caching.

Redis Key Strategy:
- Lock Key:      `lock:idempotency:{key}`
- Response Key:  `response:idempotency:{key}`

Reuses:
- app.core.redis (get_redis_client)
- app.core.redis_lock (RedisDistributedLock, InMemoryLockStore)
"""

import json
import time
import threading
import logging
from typing import Optional, Dict, Any, Tuple
from app.config import settings
from app.core.redis import get_redis_client
from app.core.redis_lock import RedisDistributedLock, InMemoryLockStore

logger = logging.getLogger("shafsky.services.idempotency")


class InMemoryResponseStore:
    """Thread-safe in-memory response cache fallback when Redis is unreachable."""
    _lock = threading.Lock()
    _responses: Dict[str, Tuple[Dict[str, Any], float]] = {}

    @classmethod
    def get_cached_response(cls, key: str) -> Optional[Dict[str, Any]]:
        with cls._lock:
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
        with cls._lock:
            now = time.time()
            cls._responses[key] = (data, now + ttl_seconds)

    @classmethod
    def _clean_expired(cls, now: float) -> None:
        expired = [k for k, (_, exp) in cls._responses.items() if exp <= now]
        for k in expired:
            cls._responses.pop(k, None)

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._responses.clear()


class IdempotencyService:
    LOCK_PREFIX = "lock:idempotency:"
    RESPONSE_PREFIX = "response:idempotency:"

    @classmethod
    def _make_lock_key(cls, key: str) -> str:
        clean_key = key.strip()
        if clean_key.startswith(cls.LOCK_PREFIX):
            return clean_key
        return f"{cls.LOCK_PREFIX}{clean_key}"

    @classmethod
    def _make_response_key(cls, key: str) -> str:
        clean_key = key.strip()
        if clean_key.startswith(cls.RESPONSE_PREFIX):
            return clean_key
        return f"{cls.RESPONSE_PREFIX}{clean_key}"

    @classmethod
    def acquire_lock(cls, idempotency_key: str, ttl_seconds: int = 30) -> Optional[str]:
        """
        Attempts to acquire a distributed lock for the given idempotency key.
        Returns lock owner token if acquired, None if lock is held by another request.
        """
        lock_name = cls._make_lock_key(idempotency_key)
        token = RedisDistributedLock.acquire_lock(lock_name, ttl_seconds=ttl_seconds)
        if token:
            logger.info(f"Acquired idempotency lock '{lock_name}' with token '{token}'")
        else:
            logger.warning(f"Failed to acquire idempotency lock '{lock_name}' - lock held by concurrent request.")
        return token

    @classmethod
    def release_lock(cls, idempotency_key: str, lock_token: str) -> bool:
        """
        Releases the distributed lock for the given idempotency key using owner token validation.
        """
        if not lock_token:
            return False
        lock_name = cls._make_lock_key(idempotency_key)
        released = RedisDistributedLock.release_lock(lock_name, lock_token)
        if released:
            logger.info(f"Released idempotency lock '{lock_name}'")
        else:
            logger.warning(f"Could not release idempotency lock '{lock_name}' (token mismatch or expired)")
        return released

    @classmethod
    def is_locked(cls, idempotency_key: str) -> bool:
        """Checks if an idempotency lock is currently active."""
        lock_name = cls._make_lock_key(idempotency_key)
        return RedisDistributedLock.is_locked(lock_name)

    @classmethod
    def get_cached_response(cls, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves cached response dictionary for the given idempotency key.
        Returns dict containing status_code, headers, body if hit, else None.
        """
        resp_key = cls._make_response_key(idempotency_key)
        client = get_redis_client()

        if client is not None:
            try:
                raw_data = client.get(resp_key)
                if raw_data:
                    logger.info(f"Idempotency Redis cache HIT for key '{resp_key}'")
                    return json.loads(raw_data)
            except Exception as err:
                logger.warning(f"Redis cache fetch error ({err}). Falling back to memory store.")

        cached = InMemoryResponseStore.get_cached_response(idempotency_key)
        if cached:
            logger.info(f"Idempotency memory cache HIT for key '{idempotency_key}'")
        return cached

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
        Caches completed response for the given idempotency key.
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
                logger.info(f"Cached response in Redis key '{resp_key}' (TTL {ttl_seconds}s)")
            except Exception as err:
                logger.warning(f"Redis cache set error ({err}). Falling back to memory store.")

        InMemoryResponseStore.set_cached_response(idempotency_key, data, ttl_seconds)

    @classmethod
    def clear_stores(cls) -> None:
        """Clears in-memory stores (useful for testing)."""
        InMemoryLockStore.clear()
        InMemoryResponseStore.clear()
