"""
Production Distributed Locking Service with Owner Validation and Timeout Support.

Lock Strategy:
- Key format: `lock:{lock_name}`
- Owner Token: Unique UUID hex assigned on lock acquisition.
- Safe Release: Atomic Lua script verifies owner token before deleting lock key.
"""

import uuid
import time
import threading
import logging
from typing import Optional, Dict, Tuple
from app.core.redis import get_redis_client

logger = logging.getLogger("shafsky.core.redis_lock")

# Lua script for atomic owner-validated lock release
_LUA_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


class InMemoryLockStore:
    """Thread-safe fallback in-memory lock store with TTL and owner validation."""
    _lock = threading.Lock()
    _store: Dict[str, Tuple[str, float]] = {}

    @classmethod
    def acquire(cls, key: str, token: str, ttl_seconds: int) -> bool:
        with cls._lock:
            now = time.time()
            cls._clean_expired(now)
            item = cls._store.get(key)
            if item and item[1] > now:
                return False
            cls._store[key] = (token, now + ttl_seconds)
            return True

    @classmethod
    def release(cls, key: str, token: str) -> bool:
        with cls._lock:
            now = time.time()
            cls._clean_expired(now)
            item = cls._store.get(key)
            if not item:
                return False
            stored_token, expire_at = item
            if expire_at > now and stored_token == token:
                cls._store.pop(key, None)
                return True
            return False

    @classmethod
    def is_locked(cls, key: str) -> bool:
        with cls._lock:
            now = time.time()
            cls._clean_expired(now)
            item = cls._store.get(key)
            return bool(item and item[1] > now)

    @classmethod
    def _clean_expired(cls, now: float) -> None:
        expired_keys = [k for k, (_, exp) in cls._store.items() if exp <= now]
        for k in expired_keys:
            cls._store.pop(k, None)

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._store.clear()


class RedisDistributedLock:
    @staticmethod
    def _format_key(lock_name: str) -> str:
        if lock_name.startswith("lock:"):
            return lock_name
        return f"lock:{lock_name}"

    @classmethod
    def acquire_lock(
        cls,
        lock_name: str,
        lock_value: Optional[str] = None,
        ttl_seconds: int = 30
    ) -> Optional[str]:
        """
        Acquire a distributed lock.
        Returns the unique lock_value owner token if acquired, or None if lock is held.
        """
        key = cls._format_key(lock_name)
        token = lock_value or uuid.uuid4().hex
        client = get_redis_client()

        if client is not None:
            try:
                acquired = client.set(key, token, nx=True, ex=ttl_seconds)
                if acquired:
                    logger.debug(f"Acquired Redis lock '{key}' with token '{token}' (TTL {ttl_seconds}s)")
                    return token
                else:
                    logger.debug(f"Redis lock collision for '{key}'. Lock held by another process.")
                    return None
            except Exception as err:
                logger.warning(f"Redis lock acquire error ({err}). Falling back to memory store.")

        # Fallback to in-memory lock store
        success = InMemoryLockStore.acquire(key, token, ttl_seconds)
        return token if success else None

    @classmethod
    def release_lock(cls, lock_name: str, lock_value: str) -> bool:
        """
        Safely release a distributed lock with owner validation using an atomic Lua script.
        Returns True if released, False if token mismatch or lock expired.
        """
        if not lock_value:
            return False

        key = cls._format_key(lock_name)
        client = get_redis_client()

        if client is not None:
            try:
                res = client.eval(_LUA_RELEASE_SCRIPT, 1, key, lock_value)
                released = bool(res == 1)
                if released:
                    logger.debug(f"Released Redis lock '{key}' for owner '{lock_value}'")
                else:
                    logger.warning(f"Lock release failed for '{key}'. Owner token mismatch or lock expired.")
                return released
            except Exception as err:
                logger.warning(f"Redis lock release error ({err}). Falling back to memory store.")

        return InMemoryLockStore.release(key, lock_value)

    @classmethod
    def is_locked(cls, lock_name: str) -> bool:
        """Checks if lock_name is currently held by any process."""
        key = cls._format_key(lock_name)
        client = get_redis_client()

        if client is not None:
            try:
                return bool(client.exists(key))
            except Exception as err:
                logger.warning(f"Redis is_locked check error ({err}). Falling back to memory store.")

        return InMemoryLockStore.is_locked(key)
