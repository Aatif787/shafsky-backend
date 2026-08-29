"""
Idempotency Service for Managing Distributed Locks and Response Caching.

Redis Key Strategy:
- Lock Key:      `lock:idempotency:{key}`
- Response Key:  `response:idempotency:{key}`

Reuses:
- app.core.redis (get_redis_client)
- app.core.redis_lock (RedisDistributedLock, InMemoryLockStore)
"""

import hashlib
import json
import time
import threading
import logging
import uuid
from typing import Optional, Dict, Any, Tuple
from sqlalchemy import text
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

    @staticmethod
    def request_fingerprint(method: str, path: str, body: bytes) -> str:
        """Stable hash of method + path + body so a key cannot be reused across operations."""
        digest = hashlib.sha256()
        digest.update((method or "").upper().encode("utf-8"))
        digest.update(b"\0")
        digest.update((path or "").encode("utf-8"))
        digest.update(b"\0")
        digest.update(body or b"")
        return digest.hexdigest()

    @classmethod
    def fingerprints_conflict(cls, cached: Dict[str, Any], fingerprint: str) -> bool:
        stored = cached.get("fingerprint")
        return bool(stored and stored != fingerprint)

    @classmethod
    def acquire_lock(cls, idempotency_key: str, ttl_seconds: Optional[int] = None) -> Optional[str]:
        """
        Attempts to acquire a distributed lock for the given idempotency key.
        Returns lock owner token if acquired, None if lock is held by another request.
        """
        if ttl_seconds is None:
            ttl_seconds = int(getattr(settings, "IDEMPOTENCY_LOCK_TTL", 120))
        lock_name = cls._make_lock_key(idempotency_key)
        client = get_redis_client()
        if client is not None:
            token = RedisDistributedLock.acquire_lock(lock_name, ttl_seconds=ttl_seconds)
            if token:
                logger.info(f"Acquired idempotency lock '{lock_name}' with token '{token}'")
                return token
            logger.warning(
                f"Failed to acquire idempotency lock '{lock_name}' - lock held by concurrent request."
            )
            return None

        token = _PostgresIdempotencyStore.acquire_lock(idempotency_key, ttl_seconds)
        if token:
            logger.info("Acquired Postgres idempotency lock")
            return token

        token = RedisDistributedLock.acquire_lock(lock_name, ttl_seconds=ttl_seconds)
        if token:
            logger.info(f"Acquired in-memory idempotency lock '{lock_name}'")
        else:
            logger.warning(
                f"Failed to acquire idempotency lock '{lock_name}' - lock held by concurrent request."
            )
        return token

    @classmethod
    def release_lock(cls, idempotency_key: str, lock_token: str) -> bool:
        """
        Releases the distributed lock for the given idempotency key using owner token validation.
        """
        if not lock_token:
            return False
        lock_name = cls._make_lock_key(idempotency_key)
        client = get_redis_client()
        if client is not None:
            released = RedisDistributedLock.release_lock(lock_name, lock_token)
        else:
            released = _PostgresIdempotencyStore.release_lock(idempotency_key, lock_token)
            if not released:
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

        cached = _PostgresIdempotencyStore.get_cached_response(idempotency_key)
        if cached:
            logger.info("Idempotency Postgres cache HIT")
            return cached

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
        ttl_seconds: int = 86400,
        fingerprint: Optional[str] = None,
    ) -> None:
        """
        Caches completed response for the given idempotency key.
        """
        resp_key = cls._make_response_key(idempotency_key)
        data = {
            "status_code": status_code,
            "headers": headers,
            "body": body,
            "fingerprint": fingerprint,
        }
        client = get_redis_client()

        if client is not None:
            try:
                client.set(resp_key, json.dumps(data), ex=ttl_seconds)
                logger.info(f"Cached response in Redis key '{resp_key}' (TTL {ttl_seconds}s)")
            except Exception as err:
                logger.warning(f"Redis cache set error ({err}). Falling back to memory store.")

        _PostgresIdempotencyStore.set_cached_response(idempotency_key, data, ttl_seconds)
        InMemoryResponseStore.set_cached_response(idempotency_key, data, ttl_seconds)

    @classmethod
    def clear_stores(cls) -> None:
        """Clears in-memory stores (useful for testing)."""
        InMemoryLockStore.clear()
        InMemoryResponseStore.clear()


class _PostgresIdempotencyStore:
    """Shared lock + response cache when Redis is down. Safe across uvicorn workers."""

    _disabled_until = 0.0

    @staticmethod
    def _engine():
        from app.database import engine
        return engine

    @classmethod
    def _is_postgres(cls) -> bool:
        if time.time() < cls._disabled_until:
            return False
        try:
            return cls._engine().dialect.name == "postgresql"
        except Exception:
            return False

    @classmethod
    def _disable(cls, err: Exception) -> None:
        cls._disabled_until = time.time() + 30
        logger.debug("Postgres idempotency store unavailable: %s", err)

    @classmethod
    def get_cached_response(cls, key: str) -> Optional[Dict[str, Any]]:
        if not cls._is_postgres():
            return None
        try:
            with cls._engine().connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT status_code, headers, body, fingerprint
                        FROM idempotency_records
                        WHERE idempotency_key = :key
                          AND response_expires_at IS NOT NULL
                          AND response_expires_at > NOW()
                        """
                    ),
                    {"key": key},
                ).mappings().first()
            if not row:
                return None
            headers = row["headers"]
            if isinstance(headers, str):
                headers = json.loads(headers)
            return {
                "status_code": int(row["status_code"] or 200),
                "headers": headers or {},
                "body": row["body"] or "",
                "fingerprint": row["fingerprint"],
            }
        except Exception as err:
            logger.debug("Postgres idempotency cache fetch skipped: %s", err)
            cls._disable(err)
            return None

    @classmethod
    def set_cached_response(cls, key: str, data: Dict[str, Any], ttl_seconds: int) -> None:
        if not cls._is_postgres():
            return
        try:
            with cls._engine().begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO idempotency_records (
                            idempotency_key, fingerprint, status_code, headers, body,
                            lock_token, lock_expires_at, response_expires_at, created_at
                        ) VALUES (
                            :key, :fingerprint, :status_code, CAST(:headers AS json), :body,
                            NULL, NULL, NOW() + (:ttl * INTERVAL '1 second'), NOW()
                        )
                        ON CONFLICT (idempotency_key) DO UPDATE SET
                            fingerprint = EXCLUDED.fingerprint,
                            status_code = EXCLUDED.status_code,
                            headers = EXCLUDED.headers,
                            body = EXCLUDED.body,
                            lock_token = NULL,
                            lock_expires_at = NULL,
                            response_expires_at = EXCLUDED.response_expires_at
                        """
                    ),
                    {
                        "key": key,
                        "fingerprint": data.get("fingerprint"),
                        "status_code": data.get("status_code"),
                        "headers": json.dumps(data.get("headers") or {}),
                        "body": data.get("body") or "",
                        "ttl": int(ttl_seconds),
                    },
                )
        except Exception as err:
            logger.debug("Postgres idempotency cache set skipped: %s", err)
            cls._disable(err)

    @classmethod
    def acquire_lock(cls, key: str, ttl_seconds: int) -> Optional[str]:
        if not cls._is_postgres():
            return None
        token = uuid.uuid4().hex
        try:
            with cls._engine().begin() as conn:
                row = conn.execute(
                    text(
                        """
                        INSERT INTO idempotency_records (
                            idempotency_key, lock_token, lock_expires_at, created_at
                        ) VALUES (
                            :key, :token, NOW() + (:ttl * INTERVAL '1 second'), NOW()
                        )
                        ON CONFLICT (idempotency_key) DO UPDATE SET
                            lock_token = EXCLUDED.lock_token,
                            lock_expires_at = EXCLUDED.lock_expires_at
                        WHERE (
                            idempotency_records.lock_expires_at IS NULL
                            OR idempotency_records.lock_expires_at < NOW()
                        )
                        AND (
                            idempotency_records.response_expires_at IS NULL
                            OR idempotency_records.response_expires_at < NOW()
                        )
                        RETURNING lock_token
                        """
                    ),
                    {"key": key, "token": token, "ttl": int(ttl_seconds)},
                ).first()
            if row:
                return token
            return None
        except Exception as err:
            logger.debug("Postgres idempotency lock acquire skipped: %s", err)
            cls._disable(err)
            return None

    @classmethod
    def release_lock(cls, key: str, token: str) -> bool:
        if not token or not cls._is_postgres():
            return False
        try:
            with cls._engine().begin() as conn:
                result = conn.execute(
                    text(
                        """
                        UPDATE idempotency_records
                        SET lock_token = NULL, lock_expires_at = NULL
                        WHERE idempotency_key = :key
                          AND lock_token = :token
                          AND (
                            response_expires_at IS NULL
                            OR response_expires_at < NOW()
                          )
                        """
                    ),
                    {"key": key, "token": token},
                )
                return bool(result.rowcount)
        except Exception as err:
            logger.debug("Postgres idempotency lock release skipped: %s", err)
            cls._disable(err)
            return False
