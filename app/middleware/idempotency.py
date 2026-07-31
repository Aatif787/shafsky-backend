"""
Idempotency Middleware for Transactional POST Requests.

Intersects POST requests with X-Idempotency-Key header:
- Replays cached successful responses with X-Cache: HIT.
- Acquires Redis distributed lock; returns HTTP 409 Conflict if another request with same key is currently processing.
- Caches successful responses (< 400 status) for 24 hours.
"""

import logging
from typing import Optional
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.concurrency import iterate_in_threadpool
from app.integrations.idempotency import IdempotencyManager

logger = logging.getLogger("shafsky.middleware.idempotency")


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Filter: Only process POST requests carrying X-Idempotency-Key header
        if request.method.upper() != "POST":
            return await call_next(request)

        idempotency_key = request.headers.get("X-Idempotency-Key") or request.headers.get("x-idempotency-key")
        if not idempotency_key or not idempotency_key.strip():
            return await call_next(request)

        key = idempotency_key.strip()

        # 2. Check for cached response replay (Cache Hit)
        cached = IdempotencyManager.get_cached_response(key)
        if cached:
            logger.info(f"Idempotency Cache HIT for key '{key}'")
            headers = dict(cached.get("headers", {}))
            headers["X-Cache"] = "HIT"
            media_type = headers.get("content-type", "application/json")
            return Response(
                content=cached.get("body", ""),
                status_code=cached.get("status_code", 200),
                headers=headers,
                media_type=media_type
            )

        # 3. Acquire Distributed Lock (In-Flight Concurrency Protection)
        locked = IdempotencyManager.acquire_lock(key, ttl_seconds=30)
        if not locked:
            logger.warning(f"In-flight request collision detected for key '{key}'. Returning HTTP 409 Conflict.")
            return JSONResponse(
                status_code=409,
                content={
                    "success": False,
                    "error": "A request with this X-Idempotency-Key is currently being processed. Please wait."
                }
            )

        try:
            # 4. Execute request pipeline
            response = await call_next(request)

            # 5. Read response body for caching
            response_body = [section async for section in response.body_iterator]
            response.body_iterator = iterate_in_threadpool(iter(response_body))
            body_bytes = b"".join(response_body)
            body_str = body_bytes.decode("utf-8", errors="replace")

            # 6. Cache successful responses (< 400)
            if response.status_code < 400:
                headers_to_cache = {
                    k: v for k, v in response.headers.items()
                    if k.lower() not in ["content-length", "x-cache"]
                }
                IdempotencyManager.set_cached_response(
                    key,
                    status_code=response.status_code,
                    headers=headers_to_cache,
                    body=body_str,
                    ttl_seconds=86400
                )

            response.headers["X-Cache"] = "MISS"
            return response
        finally:
            # 7. Release lock on completion or error
            IdempotencyManager.release_lock(key)
