"""Unit and integration tests for route/category-based rate limiting policies and security headers."""

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.security.middleware import SecurityMiddleware, get_rate_limit_policy, normalize_request_path
from app.security.rate_limit import RateLimiter, _safe_log_key


@pytest.fixture(autouse=True)
def _reset_limiter():
    RateLimiter.clear()
    yield
    RateLimiter.clear()


def test_path_normalization():
    """Verify request path normalization trims duplicate slashes, trailing slashes, and lowercases."""
    assert normalize_request_path("/API/Create-Order/") == "/api/create-order"
    assert normalize_request_path("/api//payments///orders") == "/api/payments/orders"
    assert normalize_request_path("/api/auth/login") == "/api/auth/login"
    assert normalize_request_path("/") == "/"


def test_rate_limit_policy_classification():
    """Verify deterministic policy classification for high-risk transactional and auth routes."""
    # 1. Payment Order Creation
    p_root_order = get_rate_limit_policy("POST", "/api/create-order")
    assert p_root_order is not None
    assert p_root_order["category"] == "payment_order"
    assert p_root_order["max_requests"] == 15

    p_pay_order = get_rate_limit_policy("POST", "/api/payments/create-order")
    assert p_pay_order is not None
    assert p_pay_order["category"] == "payment_order"

    p_pay_orders_alias = get_rate_limit_policy("POST", "/api/payments/orders")
    assert p_pay_orders_alias is not None
    assert p_pay_orders_alias["category"] == "payment_order"

    # 2. Payment Verification
    p_verify_root = get_rate_limit_policy("POST", "/api/verify-payment")
    assert p_verify_root is not None
    assert p_verify_root["category"] == "payment_verify"
    assert p_verify_root["max_requests"] == 15

    p_verify_alias = get_rate_limit_policy("POST", "/api/payments/verify")
    assert p_verify_alias is not None
    assert p_verify_alias["category"] == "payment_verify"

    # 3. Password Reset / Recovery
    p_pw_req = get_rate_limit_policy("POST", "/api/auth/request-password-reset")
    assert p_pw_req is not None
    assert p_pw_req["category"] == "password_reset"
    assert p_pw_req["max_requests"] == 10

    p_pw_change = get_rate_limit_policy("POST", "/api/auth/change-password")
    assert p_pw_change is not None
    assert p_pw_change["category"] == "password_reset"

    # 4. Authentication
    p_login = get_rate_limit_policy("POST", "/api/auth/login")
    assert p_login is not None
    assert p_login["category"] == "auth"
    assert p_login["max_requests"] == 10

    # 5. Flight Validation
    p_flight = get_rate_limit_policy("POST", "/api/flight/validate")
    assert p_flight is not None
    assert p_flight["category"] == "flight"
    assert p_flight["max_requests"] == 15

    # 6. AI Chat
    p_ai = get_rate_limit_policy("POST", "/api/ai/chat")
    assert p_ai is not None
    assert p_ai["category"] == "ai"
    assert p_ai["max_requests"] == 20

    # 7. Booking & Enquiry Creation
    p_booking = get_rate_limit_policy("POST", "/api/bookings")
    assert p_booking is not None
    assert p_booking["category"] == "booking_create"
    assert p_booking["max_requests"] == 15

    p_charter = get_rate_limit_policy("POST", "/api/v1/charter/requests")
    assert p_charter is not None
    assert p_charter["category"] == "booking_create"

    # 8. Webhooks
    p_webhook_pay = get_rate_limit_policy("POST", "/api/payments/webhook")
    assert p_webhook_pay is not None
    assert p_webhook_pay["category"] == "webhook"
    assert p_webhook_pay["max_requests"] == 1000

    p_webhook_wa = get_rate_limit_policy("POST", "/api/whatsapp/webhook")
    assert p_webhook_wa is not None
    assert p_webhook_wa["category"] == "webhook"

    # 9. General API fallback
    p_gen = get_rate_limit_policy("GET", "/api/some-resource")
    assert p_gen is not None
    assert p_gen["category"] == "api"
    assert p_gen["max_requests"] == 200

    # 10. Non-API routes
    assert get_rate_limit_policy("GET", "/health") is None
    assert get_rate_limit_policy("GET", "/live") is None
    assert get_rate_limit_policy("GET", "/ready") is None
    assert get_rate_limit_policy("GET", "/metrics") is None


def test_safe_log_key_masks_pii():
    """Verify that logging keys with IP addresses or tokens masks them properly."""
    assert _safe_log_key("rate_limit_auth:192.168.1.50") == "rate_limit_auth:192.168.x.x"
    assert _safe_log_key("rate_limit_auth:10.0.4.12") == "rate_limit_auth:10.0.x.x"
    assert _safe_log_key("rate_limit_auth:::1") == "rate_limit_auth:ipv6_masked"
    assert _safe_log_key("rate_limit_auth:2001:0db8:85a3::8a2e:0370:7334") == "rate_limit_auth:ipv6_masked"
    assert _safe_log_key("rate_limit_api:abc123xyz789") == "rate_limit_api:abc***789"
    assert _safe_log_key("simple_key") == "simple_key"


def test_payment_order_rate_limiting_end_to_end():
    """Verify POST /api/create-order allows 15 requests and returns 429 with Retry-After on 16th."""
    async def dummy_endpoint(request):
        return JSONResponse({"status": "created"}, status_code=201)

    app = Starlette(routes=[Route("/api/create-order", dummy_endpoint, methods=["POST"])])
    app.add_middleware(SecurityMiddleware)
    client = TestClient(app)

    # First 15 requests succeed
    for i in range(15):
        res = client.post("/api/create-order", headers={"X-Forwarded-For": "198.51.100.1"})
        assert res.status_code == 201

    # 16th request rejected with 429
    res_overflow = client.post("/api/create-order", headers={"X-Forwarded-For": "198.51.100.1"})
    assert res_overflow.status_code == 429
    assert "Rate limit exceeded" in res_overflow.json().get("detail", "")
    assert "Retry-After" in res_overflow.headers
    assert int(res_overflow.headers["Retry-After"]) >= 1

    # Security headers must be present on 429 response as well
    assert res_overflow.headers.get("x-content-type-options") == "nosniff"
    assert res_overflow.headers.get("x-frame-options") == "SAMEORIGIN"


def test_password_reset_rate_limiting_end_to_end():
    """Verify POST /api/auth/request-password-reset allows 10 requests and returns 429 on 11th."""
    async def dummy_endpoint(request):
        return JSONResponse({"status": "sent"}, status_code=200)

    app = Starlette(routes=[Route("/api/auth/request-password-reset", dummy_endpoint, methods=["POST"])])
    app.add_middleware(SecurityMiddleware)
    client = TestClient(app)

    # First 10 requests succeed
    for i in range(10):
        res = client.post("/api/auth/request-password-reset", headers={"X-Forwarded-For": "203.0.113.5"})
        assert res.status_code == 200

    # 11th request rejected with 429
    res_overflow = client.post("/api/auth/request-password-reset", headers={"X-Forwarded-For": "203.0.113.5"})
    assert res_overflow.status_code == 429
    assert "Retry-After" in res_overflow.headers


def test_health_check_unthrottled():
    """Verify health check /health is not throttled."""
    async def dummy_health(request):
        return JSONResponse({"status": "ok"}, status_code=200)

    app = Starlette(routes=[Route("/health", dummy_health, methods=["GET"])])
    app.add_middleware(SecurityMiddleware)
    client = TestClient(app)

    # 50 consecutive requests without throttle
    for _ in range(50):
        res = client.get("/health", headers={"X-Forwarded-For": "203.0.113.99"})
        assert res.status_code == 200
