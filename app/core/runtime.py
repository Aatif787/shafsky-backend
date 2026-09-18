"""Runtime flags that must never leak production bypasses."""

from __future__ import annotations

import os


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def live_gateway_tests_enabled() -> bool:
    return _truthy_env("SHAFSKY_LIVE_GATEWAY_TESTS")


def in_automated_test_runtime() -> bool:
    """True for pytest / CI even when a test patches `settings.is_production`."""
    if live_gateway_tests_enabled():
        return False
    env = (os.getenv("ENVIRONMENT") or "").strip().lower()
    if _truthy_env("TESTING") or env in {"test", "testing"}:
        return True
    return False


def offline_third_party_calls() -> bool:
    """Return True when live Razorpay / Meta / aviation HTTP must not run.

    Production always returns False — including when tests patch
    `settings.is_production`. SHAFSKY_LIVE_GATEWAY_TESTS=1 also forces live HTTP.
    """
    if live_gateway_tests_enabled():
        return False
    try:
        from app.config import settings
        if getattr(settings, "is_production", False):
            return False
    except Exception:
        pass
    env = (os.getenv("ENVIRONMENT") or "").strip().lower()
    if env in {"production", "prod"}:
        return False
    return in_automated_test_runtime() or env in {"development", "dev"}
