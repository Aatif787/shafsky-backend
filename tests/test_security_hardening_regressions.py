"""
Regression tests for the October 2026 backend hardening pass.

Covers (audit IDs in BACKEND_ISSUES_REPORT.md):
- C1: /verify-payment cross-booking guard (foreign order receipt -> REF_MISMATCH)
- H3: environment-aware CORS / cookie origin allowlist
- H4: booking-ref entropy + minimal public status payload
- H5: CRM aggregation tolerates NULL departure_time; PENDING excluded from spend
- H6: notification WhatsApp channel reports honest statuses
- M1: rate limiter self-heals keys that lost their TTL
- L2: SecurityMiddleware wraps IdempotencyMiddleware (rate limits apply to replays)
- M2: WhatsApp timeouts are never blindly retried
"""
import asyncio
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.config import settings
from app.security import cors_origins


# =============================================================================
# H3: environment-aware origin allowlist
# =============================================================================

def test_cors_origin_regex_blocks_dev_origins_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    cors_origins._COMPILED.clear()
    cors_origins._ALLOWED_CACHE.clear()
    try:
        rx = cors_origins.browser_origin_regex()
        assert rx.match("https://shafsky-admin-portal.vercel.app") is not None
        assert rx.match("https://www.shafskyaviation.in") is not None
        # plain http must never match in production
        assert rx.match("http://www.shafskyaviation.in") is None
        assert rx.match("http://localhost:5173") is None
        # tunnels and foreign tenants must never match in production
        assert rx.match("https://my-tunnel.ngrok-free.app") is None
        assert rx.match("https://evil-attacker.vercel.app") is None
        assert rx.match("https://notshafsky.vercel.app") is None
    finally:
        cors_origins._COMPILED.clear()
        cors_origins._ALLOWED_CACHE.clear()


def test_cors_origin_regex_keeps_dev_tooling_in_development():
    cors_origins._COMPILED.clear()
    cors_origins._ALLOWED_CACHE.clear()
    try:
        rx = cors_origins.browser_origin_regex()
        assert rx.match("http://localhost:5173") is not None
        assert rx.match("https://my-tunnel.ngrok-free.app") is not None
        assert rx.match("https://www.shafskyaviation.in") is not None
        assert rx.match("https://evil-attacker.vercel.app") is None
    finally:
        cors_origins._COMPILED.clear()
        cors_origins._ALLOWED_CACHE.clear()


def test_allowed_origins_filters_loopback_and_tunnels_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    # ALLOWED_ORIGINS is a property that re-reads the env on every call.
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,https://shafskyaviation.in,https://x.ngrok-free.dev",
    )
    cors_origins._ALLOWED_CACHE.clear()
    try:
        assert cors_origins.allowed_origins() == ["https://shafskyaviation.in"]
    finally:
        cors_origins._ALLOWED_CACHE.clear()


# =============================================================================
# H4: booking-ref entropy
# =============================================================================

def test_booking_ref_has_high_entropy():
    from app.services.booking_service import BookingService

    ref = BookingService.generate_booking_ref()
    match = re.fullmatch(r"SHF-(\d{8})-([A-F0-9]+)", ref)
    assert match is not None, ref
    # 4 random bytes = 8 hex chars (32 bits); the old 2-byte suffix was 16 bits.
    assert len(match.group(2)) == 8, ref


# =============================================================================
# H4: public status endpoint returns the minimal poller contract only
# =============================================================================

def test_booking_status_endpoint_returns_minimal_fields():
    from fastapi.testclient import TestClient
    from sqlalchemy import delete as sa_delete

    from app.database import SessionLocal
    from app.main import app
    from app.models.schema import Booking, BookingStatus

    db = SessionLocal()
    try:
        ref = f"SHF-REG-{uuid.uuid4().hex[:8].upper()}"
        booking = Booking(
            booking_ref=ref,
            passenger_name="Regression User",
            passenger_email=f"reg-{uuid.uuid4().hex[:8]}@example.com",
            passenger_phone="+919999999999",
            service_type="Silver",
            total_amount=5000.0,
            currency="INR",
            departure_time=None,
            status=BookingStatus.CONFIRMED,
        )
        db.add(booking)
        db.commit()
        try:
            client = TestClient(app)
            resp = client.get(f"/api/bookings/{ref}/status")
            assert resp.status_code == 200, resp.text
            data = resp.json().get("data") or {}
            assert data.get("bookingRef") == ref
            assert data.get("status") == "CONFIRMED"
            assert data.get("paymentStatus") == "PAID"
            # Financial/operational details must not leak on a public endpoint.
            assert "totalAmount" not in data
            assert "currency" not in data
            assert "serviceType" not in data
            assert "createdAt" not in data
        finally:
            db.delete(booking)
            db.commit()
    finally:
        db.close()


# =============================================================================
# H5: CRM aggregation survives NULL departure_time and honest totalSpent
# =============================================================================

def _fake_db(profile, bookings):
    class _Scalars:
        def all(self):
            return bookings

    class _DB:
        def scalar(self, *_a, **_k):
            return profile

        def scalars(self, *_a, **_k):
            return _Scalars()

    return _DB()


def _fake_profile():
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="crm-reg@example.com",
        full_name="CRM Regression",
        phone_number="+919999999999",
        company=None,
        vip_status=False,
        vip_tier="NONE",
        passport_number=None,
        tags={},
        documents_config={},
        notes=None,
        created_at=datetime.now(timezone.utc),
    )


def test_crm_handles_null_departure_time_and_excludes_pending_from_total_spent():
    from app.models.schema import BookingStatus
    from app.services.crm_service import CrmService

    profile = _fake_profile()
    bookings = [
        SimpleNamespace(
            booking_ref="SHF-CRM-1",
            flight_num=None,
            origin_code="DEL",
            dest_code="BOM",
            departure_time=None,  # the crash trigger
            total_amount=1000.0,
            status=BookingStatus.PENDING,
        ),
        SimpleNamespace(
            booking_ref="SHF-CRM-2",
            flight_num="AI101",
            origin_code="DEL",
            dest_code="BOM",
            departure_time=datetime.now(timezone.utc),
            total_amount=2000.0,
            status=BookingStatus.CONFIRMED,
        ),
        SimpleNamespace(
            booking_ref="SHF-CRM-3",
            flight_num=None,
            origin_code="DEL",
            dest_code="BOM",
            departure_time=datetime.now(timezone.utc),
            total_amount=3000.0,
            status=BookingStatus.COMPLETED,
        ),
    ]

    result = CrmService.get_customer_details_and_stats(_fake_db(profile, bookings), str(profile.id))
    stats = result["travelStatistics"]
    # PENDING (unpaid) must not count as spent; CONFIRMED + COMPLETED do.
    assert stats["totalSpentINR"] == 5000.0
    upcoming = {b["bookingRef"]: b for b in result["upcomingBookings"]}
    assert upcoming["SHF-CRM-1"]["departureTime"] is None
    assert upcoming["SHF-CRM-2"]["departureTime"] is not None


# =============================================================================
# C1: cross-booking payment verification guard
# =============================================================================

def test_verify_rejects_foreign_order_receipt(monkeypatch):
    from app.database import SessionLocal
    from app.models.payment import PaymentStatus, PaymentTransaction
    from app.models.schema import Booking, BookingStatus
    from app.services.payment_service import PaymentService

    class _ForeignOrderProvider:
        """Simulates Razorpay: the presented order belongs to ANOTHER booking."""

        def is_configured(self):
            return True

        def fetch_order(self, order_id):
            return {
                "success": True,
                "order": {
                    "id": order_id,
                    "receipt": "SHF-ATTACKER-REF",
                    "amount": 500000,
                    "currency": "INR",
                },
            }

    import app.providers.razorpay_provider as rzp_mod

    monkeypatch.setattr(rzp_mod, "razorpay_provider", _ForeignOrderProvider())

    db = SessionLocal()
    try:
        victim_ref = f"SHF-REG-{uuid.uuid4().hex[:8].upper()}"
        booking = Booking(
            booking_ref=victim_ref,
            passenger_name="Victim User",
            passenger_email=f"victim-{uuid.uuid4().hex[:8]}@example.com",
            passenger_phone="+919999999998",
            service_type="Silver",
            total_amount=5000.0,
            currency="INR",
            departure_time=None,
            status=BookingStatus.PENDING,
        )
        db.add(booking)
        db.flush()
        tx = PaymentTransaction(
            transaction_ref=f"PAY-{uuid.uuid4().hex[:8].upper()}",
            entity_type="AIRPORT_BOOKING",
            entity_id=victim_ref,
            amount=5000.0,
            currency="INR",
            status=PaymentStatus.PENDING,
            gateway_provider="RAZORPAY",
            gateway_payment_id="order_victim_pending",
        )
        db.add(tx)
        db.commit()

        try:
            result = PaymentService.handle_verified_payment(
                db,
                event_name="VERIFY_ENDPOINT",
                gateway_provider="RAZORPAY",
                order_id="order_attacker_owned",
                payment_id="pay_attacker_owned",
                booking_ref=victim_ref,
                signature="any-signature",
                channel="web",
            )
            assert result.get("status") == "REF_MISMATCH", result
            assert result.get("success") is False
        finally:
            db.delete(tx)
            db.delete(booking)
            db.commit()
    finally:
        db.close()


# =============================================================================
# H6: notification WhatsApp channel honest statuses
# =============================================================================

def test_notification_whatsapp_channel_reports_honest_status(monkeypatch):
    from app.integrations.whatsapp import client as wa_client_mod
    from app.services.notification_service import NotificationService

    class _FakeClient:
        def __init__(self, result):
            self.result = result

        def send_text_message(self, to_phone, message_body):
            return self.result

    monkeypatch.setattr(
        wa_client_mod,
        "whatsapp_client",
        _FakeClient({"success": True, "message_id": "wamid.regression", "status": "sent"}),
    )
    res = asyncio.run(NotificationService.send_whatsapp_meta("919999999999", "hello"))
    assert res == {"status": "DELIVERED", "message_id": "wamid.regression"}

    monkeypatch.setattr(
        wa_client_mod,
        "whatsapp_client",
        _FakeClient({"success": False, "error": "boom", "status": "failed"}),
    )
    res = asyncio.run(NotificationService.send_whatsapp_meta("919999999999", "hello"))
    assert res["status"] == "FAILED"

    monkeypatch.setattr(
        wa_client_mod,
        "whatsapp_client",
        _FakeClient({"success": False, "error": "not configured", "status": "unconfigured"}),
    )
    res = asyncio.run(NotificationService.send_whatsapp_meta("919999999999", "hello"))
    assert res["status"] == "BYPASSED"


# =============================================================================
# M1: rate limiter self-heals keys without TTL (Redis integration)
# =============================================================================

def test_rate_limiter_self_heals_missing_ttl():
    from app.core.redis import get_redis_client
    from app.security.rate_limit import RateLimiter

    client = get_redis_client()
    if client is None:
        pytest.skip("Redis not available in this environment")

    key = "rate_limit_regress:self_heal"
    try:
        client.delete(key)
        client.set(key, 5)  # simulates the old crash artifact: value, no TTL
        RateLimiter.check_rate_limit(key, max_requests=100, window_seconds=60)
        assert int(client.get(key)) == 6
        assert client.ttl(key) > 0
    finally:
        client.delete(key)
        RateLimiter.clear()


# =============================================================================
# L2: middleware order -- Security wraps Idempotency
# =============================================================================

def test_security_middleware_wraps_idempotency():
    from app.main import app

    names = [getattr(m, "cls", None).__name__ for m in app.user_middleware]
    # This starlette version keeps user_middleware in execution order
    # (outermost first). Security must be OUTSIDE Idempotency so cached replays
    # still pass rate limiting, and CORS must be outermost for preflights.
    assert names[0] == "CORSMiddleware"
    assert names.index("SecurityMiddleware") < names.index("IdempotencyMiddleware")


# =============================================================================
# M2: WhatsApp timeouts are never blindly retried
# =============================================================================

def test_whatsapp_timeout_is_not_retried(monkeypatch):
    import httpx

    from app.integrations.whatsapp.client import whatsapp_client

    calls = {"n": 0}

    class _Boom:
        def post(self, *a, **k):
            calls["n"] += 1
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "_get_http_client", lambda: _Boom())
    res = whatsapp_client.send_message(to_phone="+919999999999", message_body="hi")
    assert calls["n"] == 1
    assert res.get("status") == "timeout_unverified"
