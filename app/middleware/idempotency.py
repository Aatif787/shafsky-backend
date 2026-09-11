"""
Idempotency Middleware for Intercepting Transactional POST Requests.

Middleware Requirements:
1. Intercepts POST requests carrying X-Idempotency-Key header.
2. Replays completed cached responses with X-Cache: HIT immediately.
3. Returns HTTP 409 Conflict if request with same key is currently processing.
4. Acquires lock, re-checks cache, executes pipeline, caches response, releases lock.
5. Binds cached responses to method + path + body fingerprint.
6. Configurable TTLs (120s lock, 86400s response cache).
7. Redis first; Postgres shared store if Redis is down; in-memory last (single process).
8. Structured logging.
"""

import logging
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.concurrency import iterate_in_threadpool
from app.services.idempotency_service import IdempotencyService
from app.config import settings

logger = logging.getLogger("shafsky.middleware.idempotency")

_DROP_HEADERS = {
    "content-length",
    "x-cache",
    "set-cookie",
    "transfer-encoding",
    "connection",
    "keep-alive",
}


class IdempotencyMiddleware(BaseHTTPMiddleware):
    @staticmethod
    def _replay_cached(cached: dict) -> Response:
        headers = {
            k: v
            for k, v in dict(cached.get("headers", {})).items()
            if k.lower() not in _DROP_HEADERS
        }
        headers["X-Cache"] = "HIT"
        media_type = headers.get("content-type", "application/json")
        return Response(
            content=cached.get("body", ""),
            status_code=cached.get("status_code", 200),
            headers=headers,
            media_type=media_type,
        )

    @staticmethod
    def _fingerprint_conflict_response() -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "code": "ERR_IDEMPOTENCY_KEY_REUSE",
                "error": "Idempotency-Key was already used for a different request.",
            },
        )

    async def dispatch(self, request: Request, call_next):
        if request.method.upper() != "POST":
            return await call_next(request)

        # Primary header: Idempotency-Key (RFC standard); Backward-compatibility: X-Idempotency-Key
        primary_key = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
        legacy_key = request.headers.get("X-Idempotency-Key") or request.headers.get("x-idempotency-key")

        primary_clean = primary_key.strip() if primary_key is not None else None
        legacy_clean = legacy_key.strip() if legacy_key is not None else None

        # Reject conflicting values if both headers are supplied with different non-empty values
        if primary_clean and legacy_clean and primary_clean != legacy_clean:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "code": "ERR_IDEMPOTENCY_HEADER_CONFLICT",
                    "error": "Conflicting Idempotency-Key and X-Idempotency-Key headers supplied.",
                },
            )

        key = primary_clean or legacy_clean
        if not key:
            return await call_next(request)

        max_len = int(getattr(settings, "IDEMPOTENCY_MAX_KEY_LENGTH", 256))
        if len(key) > max_len:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "code": "ERR_IDEMPOTENCY_KEY_INVALID",
                    "error": f"Idempotency-Key must be at most {max_len} characters.",
                },
            )

        lock_ttl = int(getattr(settings, "IDEMPOTENCY_LOCK_TTL", 120))
        cache_ttl = int(getattr(settings, "IDEMPOTENCY_CACHE_TTL", 86400))
        body = await request.body()

        async def _replay_receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(request.scope, _replay_receive)
        fingerprint = IdempotencyService.request_fingerprint(
            request.method, request.url.path, body
        )

        cached = IdempotencyService.get_cached_response(key)
        if cached:
            if IdempotencyService.fingerprints_conflict(cached, fingerprint):
                logger.warning("Idempotency key reused for a different request payload")
                return self._fingerprint_conflict_response()
            logger.info("Replaying cached response for idempotency key")
            return self._replay_cached(cached)

        lock_token = IdempotencyService.acquire_lock(key, ttl_seconds=lock_ttl)
        if not lock_token:
            cached = IdempotencyService.get_cached_response(key)
            if cached:
                if IdempotencyService.fingerprints_conflict(cached, fingerprint):
                    return self._fingerprint_conflict_response()
                return self._replay_cached(cached)
            logger.warning(
                "In-flight request collision for idempotency key. Returning HTTP 409 Conflict."
            )
            return JSONResponse(
                status_code=409,
                content={
                    "success": False,
                    "code": "ERR_CONCURRENT_SUBMISSION",
                    "error": "A request with this Idempotency-Key is currently being processed. Please wait.",
                },
            )

        try:
            cached = IdempotencyService.get_cached_response(key)
            if cached:
                if IdempotencyService.fingerprints_conflict(cached, fingerprint):
                    return self._fingerprint_conflict_response()
                logger.info("Replaying cached response after lock acquire")
                return self._replay_cached(cached)

            response = await call_next(request)

            response_body = [section async for section in response.body_iterator]
            response.body_iterator = iterate_in_threadpool(iter(response_body))  # pyright: ignore[reportAttributeAccessIssue]
            body_bytes = b"".join(response_body)
            body_str = body_bytes.decode("utf-8", errors="replace")

            if response.status_code < 400:
                headers_to_cache = {
                    k: v
                    for k, v in response.headers.items()
                    if k.lower() not in _DROP_HEADERS
                }
                IdempotencyService.set_cached_response(
                    key,
                    status_code=response.status_code,
                    headers=headers_to_cache,
                    body=body_str,
                    ttl_seconds=cache_ttl,
                    fingerprint=fingerprint,
                )

            response.headers["X-Cache"] = "MISS"
            return response
        finally:
            IdempotencyService.release_lock(key, lock_token)
