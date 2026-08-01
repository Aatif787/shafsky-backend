"""
Idempotency Middleware for Intercepting Transactional POST Requests.

Middleware Requirements:
1. Intercepts POST requests carrying X-Idempotency-Key header.
2. Replays completed cached responses with X-Cache: HIT immediately.
3. Returns HTTP 409 Conflict if request with same key is currently processing.
4. Acquires Redis lock, executes pipeline, caches response, releases lock.
5. Configurable TTLs (30s lock, 86400s response cache).
6. Graceful Redis failure fallback.
7. Structured logging.
"""

import logging
from typing import Optional
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.concurrency import iterate_in_threadpool
from app.services.idempotency_service import IdempotencyService
from app.config import settings

logger = logging.getLogger("shafsky.middleware.idempotency")


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Filter: Intercept POST requests carrying X-Idempotency-Key header
        if request.method.upper() != "POST":
            return await call_next(request)

        idempotency_key = request.headers.get("X-Idempotency-Key") or request.headers.get("x-idempotency-key")
        if not idempotency_key or not idempotency_key.strip():
            return await call_next(request)

        key = idempotency_key.strip()
        lock_ttl = int(getattr(settings, "IDEMPOTENCY_LOCK_TTL", 30))
        cache_ttl = int(getattr(settings, "IDEMPOTENCY_CACHE_TTL", 86400))

        # 2. Replay completed cached response (Cache Hit)
        cached = IdempotencyService.get_cached_response(key)
        if cached:
            logger.info(f"Replaying cached response for idempotency key '{key}'")
            headers = dict(cached.get("headers", {}))
            headers["X-Cache"] = "HIT"
            media_type = headers.get("content-type", "application/json")
            return Response(
                content=cached.get("body", ""),
                status_code=cached.get("status_code", 200),
                headers=headers,
                media_type=media_type
            )

        # 3. Acquire Distributed Lock (Concurrent In-Flight Protection)
        lock_token = IdempotencyService.acquire_lock(key, ttl_seconds=lock_ttl)
        if not lock_token:
            logger.warning(f"In-flight request collision for idempotency key '{key}'. Returning HTTP 409 Conflict.")
            return JSONResponse(
                status_code=409,
                content={
                    "success": False,
                    "code": "ERR_CONCURRENT_SUBMISSION",
                    "error": "A request with this X-Idempotency-Key is currently being processed. Please wait."
                }
            )

        try:
            # 4. Process request through pipeline
            response = await call_next(request)

            # 5. Read response body for caching
            response_body = [section async for section in response.body_iterator]
            response.body_iterator = iterate_in_threadpool(iter(response_body))
            body_bytes = b"".join(response_body)
            body_str = body_bytes.decode("utf-8", errors="replace")

            # 6. Cache completed responses (< 400 status)
            if response.status_code < 400:
                headers_to_cache = {
                    k: v for k, v in response.headers.items()
                    if k.lower() not in ["content-length", "x-cache", "set-cookie"]
                }
                IdempotencyService.set_cached_response(
                    key,
                    status_code=response.status_code,
                    headers=headers_to_cache,
                    body=body_str,
                    ttl_seconds=cache_ttl
                )

            response.headers["X-Cache"] = "MISS"
            return response
        finally:
            # 7. Release distributed lock using owner token
            IdempotencyService.release_lock(key, lock_token)
