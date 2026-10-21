"""
Payment Gateway Selection & Sanitization.
Handles determining active gateway (Razorpay / ICICI), checkout payload preparation,
and cross-gateway session exclusivity checks.
"""

import os
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.models.payment import PaymentTransaction, PaymentStatus
from app.config import settings

logger = logging.getLogger(__name__)


def active_payment_gateway(preferred: Optional[str] = None) -> str:
    """RAZORPAY (primary/default) or ICICI when selected and credentials are present.

    Reads process env on every call so a restarted process / updated env is honored
    without relying on a stale Settings singleton.
    """
    from app.providers.icici_provider import icici_provider
    from app.providers.razorpay_provider import razorpay_provider

    if preferred:
        pref = preferred.strip().upper()
        if pref == "RAZORPAY":
            return "RAZORPAY"
        if pref == "ICICI":
            icici_provider.reload()
            if icici_provider.is_configured():
                return "ICICI"
            raise ValueError(
                "PAYMENT_GATEWAY=ICICI requested but ICICI_MID / ICICI_KEY / ICICI_AGG_ID are not configured."
            )

    requested = (
        os.getenv("PRIMARY_PAYMENT_GATEWAY")
        or os.getenv("PAYMENT_GATEWAY")
        or getattr(settings, "PAYMENT_GATEWAY", None)
        or "RAZORPAY"
    )
    requested = str(requested).strip().upper()
    if requested == "ICICI":
        icici_provider.reload()
        if icici_provider.is_configured():
            return "ICICI"
        raise ValueError(
            "PAYMENT_GATEWAY=ICICI but ICICI_MID / ICICI_KEY / ICICI_AGG_ID are not configured."
        )
    return "RAZORPAY"


def public_checkout_payload(
    *,
    booking_ref: str,
    gateway: str,
    transaction_ref: Optional[str] = None,
    total_amount: Optional[float] = None,
    currency: str = "INR",
    razorpay_order_id: Optional[str] = None,
    razorpay_key_id: Optional[str] = None,
    razorpay_amount_paise: Optional[int] = None,
    icici_redirect_url: Optional[str] = None,
    icici_merchant_txn_no: Optional[str] = None,
) -> Dict[str, Any]:
    """Checkout fields only – never passenger PII or raw gateway payloads."""
    from app.security.payment_token import mint_payment_token

    data: Dict[str, Any] = {
        "bookingRef": booking_ref,
        "gateway": gateway,
        "payment_gateway": gateway,
        "payment_token": mint_payment_token(booking_ref),
        "totalAmount": total_amount,
        "currency": currency or "INR",
    }
    if transaction_ref:
        data["transactionRef"] = transaction_ref
    if gateway == "RAZORPAY":
        data["razorpay_order_id"] = razorpay_order_id
        data["razorpay_key_id"] = razorpay_key_id
        data["razorpay_amount_paise"] = razorpay_amount_paise
    elif gateway == "ICICI":
        data["icici_redirect_url"] = icici_redirect_url
        data["icici_merchant_txn_no"] = icici_merchant_txn_no
    return data


def frozen_gateway_for_booking(db: Session, booking) -> Optional[str]:
    """Return gateway already bound to an open/paid session for this booking, if any."""
    refs = [booking.booking_ref]
    if getattr(booking, "id", None):
        refs.append(str(booking.id))
    tx = db.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.entity_id.in_(refs),
            PaymentTransaction.is_duplicate.isnot(True),
            PaymentTransaction.status.in_(
                [
                    PaymentStatus.PENDING,
                    PaymentStatus.PROCESSING,
                    PaymentStatus.SUCCESSFUL,
                    PaymentStatus.PARTIALLY_REFUNDED,
                ]
            ),
        )
        .order_by(desc(PaymentTransaction.created_at))
    )
    if not tx:
        return None
    return (tx.gateway_provider or "").strip().upper() or None


def assert_gateway_exclusive(db: Session, booking, requested_gateway: str) -> None:
    """Reject dual Razorpay+ICICI sessions for new initiates on the same pending booking."""
    requested = (requested_gateway or "").strip().upper()
    frozen = frozen_gateway_for_booking(db, booking)
    if frozen and frozen != requested:
        raise ValueError(
            f"Booking already has an active {frozen} payment session. "
            f"Cannot start a {requested} checkout for the same booking."
        )


def sanitize_gateway_response(gateway_response: Any) -> Optional[Dict[str, Any]]:
    """Strip secrets and bulky raw payloads before returning to API clients."""
    if not isinstance(gateway_response, dict):
        return None
    blocked = {
        "securehash", "key", "icici_key", "raw", "raw_response", "request",
        "headers", "authorization", "password", "secret",
    }
    safe: Dict[str, Any] = {}
    for key, value in gateway_response.items():
        if str(key).lower() in blocked:
            continue
        if isinstance(value, dict):
            nested = sanitize_gateway_response(value)
            if nested:
                safe[key] = nested
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
    return safe or None
