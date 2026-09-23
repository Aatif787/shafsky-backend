"""
Signed one-time payment session tokens for guest checkout.

Issued at booking create / payment initiate. Required for retry, create-order,
and ICICI initiate when the caller is not the booking owner or staff.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

from app.config import settings


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + pad).encode("ascii"))


def payment_session_secret() -> bytes:
    raw = (
        os.getenv("PAYMENT_SESSION_SECRET")
        or getattr(settings, "PAYMENT_SESSION_SECRET", "")
        or os.getenv("JWT_SECRET")
        or getattr(settings, "JWT_SECRET", "")
        or ""
    ).strip()
    if not raw:
        if settings.is_production:
            raise RuntimeError("PAYMENT_SESSION_SECRET (or JWT_SECRET) must be configured.")
        raw = "dev-payment-session-secret-not-for-production"
    return raw.encode("utf-8")


def payment_session_ttl_seconds() -> int:
    minutes = int(
        os.getenv("PAYMENT_SESSION_TTL_MINUTES")
        or getattr(settings, "PAYMENT_SESSION_TTL_MINUTES", 120)
        or 120
    )
    return max(60, minutes * 60)


def mint_payment_token(booking_ref: str, *, ttl_seconds: Optional[int] = None) -> str:
    """Create a signed payment session token bound to a booking_ref."""
    ref = (booking_ref or "").strip()
    if not ref:
        raise ValueError("booking_ref is required to mint a payment token.")
    now = int(time.time())
    ttl = ttl_seconds if ttl_seconds is not None else payment_session_ttl_seconds()
    payload: Dict[str, Any] = {
        "v": 1,
        "typ": "payment_session",
        "booking_ref": ref,
        "iat": now,
        "exp": now + ttl,
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url_encode(
        hmac.new(payment_session_secret(), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{body}.{sig}"


def verify_payment_token(token: Optional[str], *, booking_ref: str) -> bool:
    """Return True if token is a valid, unexpired payment session for booking_ref."""
    if not token or not isinstance(token, str) or "." not in token:
        return False
    ref = (booking_ref or "").strip()
    if not ref:
        return False
    try:
        body, sig = token.strip().split(".", 1)
        expected = _b64url_encode(
            hmac.new(payment_session_secret(), body.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(expected, sig):
            return False
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
        if payload.get("typ") != "payment_session":
            return False
        if str(payload.get("booking_ref") or "").strip() != ref:
            return False
        exp = int(payload.get("exp") or 0)
        if exp < int(time.time()):
            return False
        return True
    except Exception:
        return False


def allow_payment_simulation() -> bool:
    """Explicit opt-in for simulated Razorpay signatures/orders in non-production."""
    if settings.is_production:
        return False
    flag = (
        os.getenv("ALLOW_PAYMENT_SIMULATION")
        or getattr(settings, "ALLOW_PAYMENT_SIMULATION", "")
        or ""
    ).strip().lower()
    return flag in ("1", "true", "yes", "on")
