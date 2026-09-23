"""
Pytest bootstrap — isolate tests from the production Neon database.

This module is imported before test files. It rewrites DATABASE_URL when
TEST_DATABASE_URL is set, and refuses to run against the known production
Neon host otherwise.
"""

from __future__ import annotations

import os
import sys

import pytest

from dotenv import load_dotenv

PROD_HOST_MARKERS = (
    "neon.tech",
    "ep-flat-frost",
)


def _is_production_url(url: str) -> bool:
    lowered = (url or "").lower()
    return any(marker in lowered for marker in PROD_HOST_MARKERS)


def configure_test_database() -> None:
    load_dotenv()

    allow_prod = os.getenv("SHAFSKY_ALLOW_PROD_DB_TESTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    test_url = (os.getenv("TEST_DATABASE_URL") or "").strip()
    current = (os.getenv("DATABASE_URL") or "").strip()

    os.environ["TESTING"] = "1"
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("ALLOW_PAYMENT_SIMULATION", "true")
    os.environ.setdefault("PAYMENT_SESSION_SECRET", "test-payment-session-secret")
    os.environ.setdefault("PAYMENT_WEBHOOK_SECRET", "test-payment-webhook-secret")
    # CI has no Razorpay key, so the placeholders below apply. A local .env may
    # already contain a live key; load_dotenv() will not override an rzp_test_
    # key exported by the shell. Pytest must not keep a non-test key.
    loaded_razorpay_key = (os.getenv("RAZORPAY_KEY_ID") or "").strip()
    if not loaded_razorpay_key.startswith("rzp_test_"):
        os.environ["RAZORPAY_KEY_ID"] = "rzp_test_ci_key"
        os.environ["RAZORPAY_KEY_SECRET"] = "rzp_test_ci_secret_not_live"
    else:
        os.environ.setdefault("RAZORPAY_KEY_SECRET", "rzp_test_ci_secret_not_live")
    os.environ.setdefault("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "shafsky_wa_verify_token")
    os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "shafsky_wa_verify_token")
    os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test_wa_access_token")
    os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "test_wa_phone_id")
    os.environ.setdefault("WHATSAPP_OFFICER_NOTIFY_PHONE", "919999999999")

    if test_url:
        os.environ["DATABASE_URL"] = test_url
        return

    if _is_production_url(current) and not allow_prod:
        sys.stderr.write(
            "\nRefusing to run pytest against the production Neon database.\n\n"
            "This suite inserts fixture bookings into whatever DATABASE_URL points at.\n"
            "Use an isolated Postgres database:\n\n"
            "  docker compose -f docker-compose.test.yml up -d\n"
            "  $env:TEST_DATABASE_URL = "
            "'postgresql://shafsky:shafsky@127.0.0.1:55432/shafsky_test'\n"
            "  python -m pytest -q\n\n"
            "Emergency override only (will pollute live data):\n"
            "  $env:SHAFSKY_ALLOW_PROD_DB_TESTS = '1'\n\n"
        )
        raise SystemExit(2)


configure_test_database()


@pytest.fixture(autouse=True)
def isolate_process_and_redis_state():
    """Keep order-dependent request counters out of unrelated tests.

    CI deliberately runs a real Redis service.  Without this boundary, rate
    limits and idempotency entries from an earlier test are inherited by later
    HTTP tests that share the TestClient IP address.
    """
    from app.security.rate_limit import RateLimiter
    from app.services.idempotency_service import InMemoryLockStore, InMemoryResponseStore
    from app.core.redis import get_redis_client
    from app.flight.unified_cache import clear_unified_cache
    from app.flight.providers import aviation_edge_provider as ae_mod

    RateLimiter.clear()
    InMemoryLockStore.clear()
    InMemoryResponseStore.clear()
    clear_unified_cache()
    ae_mod._IN_MEMORY_CACHE.clear()
    client = get_redis_client()
    if client is not None:
        client.flushdb()
    yield
    RateLimiter.clear()
    InMemoryLockStore.clear()
    InMemoryResponseStore.clear()
    clear_unified_cache()
    ae_mod._IN_MEMORY_CACHE.clear()
    if client is not None:
        client.flushdb()
    try:
        from sqlalchemy.orm import close_all_sessions
        close_all_sessions()
    except Exception:
        pass

