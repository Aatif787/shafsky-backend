import re
from typing import Optional, Dict, Any
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from fastapi import Request, HTTPException
from app.security.headers import get_security_headers
from app.security.rate_limit import RateLimiter
from app.security.client_ip import get_client_ip


def normalize_request_path(path: str) -> str:
    """Normalize request path for deterministic rate limiting matching."""
    cleaned = re.sub(r"/+", "/", path).strip()
    if cleaned != "/" and cleaned.endswith("/"):
        cleaned = cleaned[:-1]
    return cleaned.lower()


def get_rate_limit_policy(method: str, path: str) -> Optional[Dict[str, Any]]:
    """Determine explicit route/category-based rate limit policy.

    Order of evaluation is strict and deterministic:
    1. Webhooks (high ceiling for legitimate external event callbacks)
    2. Payment Order Creation (strict transactional limits)
    3. Payment Verification
    4. Password Reset & Recovery
    5. Authentication (Login / Register)
    6. Flight Validation
    7. AI Chat
    8. Booking & Enquiry Creation Mutations
    9. Broad /api/ bucket
    Non-API endpoints (e.g. /health, /ready, /live, /metrics) return None.
    """
    m = method.upper()
    p = normalize_request_path(path)

    # 1. Webhook endpoints (high ceiling for legitimate external event callbacks)
    if p.startswith("/api/payments/webhook") or p.startswith("/api/whatsapp/webhook"):
        return {"category": "webhook", "key_prefix": "rate_limit_webhook", "max_requests": 1000, "window_seconds": 60}

    # 2. Payment Order Creation (strict transactional limits)
    if m == "POST" and (
        p in ("/api/create-order", "/api/payments/create-order", "/api/payments/orders", "/api/payments/initiate")
        or p.startswith("/api/payments/create-order")
        or p.startswith("/api/payments/orders")
        or p.startswith("/api/payments/initiate")
    ):
        return {"category": "payment_order", "key_prefix": "rate_limit_payment_order", "max_requests": 15, "window_seconds": 60}

    # 3. Payment Verification
    if m == "POST" and (
        p in ("/api/verify-payment", "/api/payments/verify-payment", "/api/payments/verify")
        or p.startswith("/api/payments/verify-payment")
        or p.startswith("/api/payments/verify")
    ):
        return {"category": "payment_verify", "key_prefix": "rate_limit_payment_verify", "max_requests": 15, "window_seconds": 60}

    # 4. Password Reset & Recovery
    if m == "POST" and (
        p in (
            "/api/auth/request-password-reset",
            "/api/auth/reset-password",
            "/api/auth/forgot-password",
            "/api/auth/change-password",
        )
        or p.startswith("/api/auth/request-password-reset")
        or p.startswith("/api/auth/reset-password")
        or p.startswith("/api/auth/forgot-password")
        or p.startswith("/api/auth/change-password")
    ):
        return {"category": "password_reset", "key_prefix": "rate_limit_password_reset", "max_requests": 10, "window_seconds": 60}

    # 5. Authentication (Login / Register)
    if m == "POST" and (p.startswith("/api/auth/login") or p.startswith("/api/auth/register")):
        return {"category": "auth", "key_prefix": "rate_limit_auth", "max_requests": 10, "window_seconds": 60}

    # 6. Flight Validation
    if p.startswith("/api/flight/validate") or p.startswith("/api/flights/validate") or p.startswith("/api/airport/flow/flight-info"):
        return {"category": "flight", "key_prefix": "rate_limit_flight", "max_requests": 15, "window_seconds": 60}

    # 7. AI Chat
    if p.startswith("/api/ai/chat"):
        return {"category": "ai", "key_prefix": "rate_limit_ai", "max_requests": 20, "window_seconds": 60}

    # 8. Booking & Enquiry Creation Mutations
    if m == "POST" and (
        p.startswith("/api/bookings")
        or p.startswith("/api/airport/bookings")
        or p.startswith("/api/airport/validate-booking")
        or p.startswith("/api/airport/draft")
        or p.startswith("/api/airport/save-draft")
        or p.startswith("/api/airport/flow/customer-details")
        or p.startswith("/api/v1/charter/requests")
        or p.startswith("/api/charter/requests")
    ):
        return {"category": "booking_create", "key_prefix": "rate_limit_booking_create", "max_requests": 15, "window_seconds": 60}

    # 9. Broad /api/ bucket
    if p.startswith("/api/"):
        return {"category": "api", "key_prefix": "rate_limit_api", "max_requests": 200, "window_seconds": 60}

    return None


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_ip = get_client_ip(request)
        policy = get_rate_limit_policy(request.method, request.url.path)

        if policy:
            try:
                RateLimiter.check_rate_limit(
                    f"{policy['key_prefix']}:{client_ip}",
                    max_requests=policy["max_requests"],
                    window_seconds=policy["window_seconds"],
                )
            except HTTPException as exc:
                headers = dict(exc.headers or {})
                for key, val in get_security_headers().items():
                    headers[key] = val
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                    headers=headers,
                )

        response = await call_next(request)

        for key, val in get_security_headers().items():
            response.headers[key] = val

        return response

