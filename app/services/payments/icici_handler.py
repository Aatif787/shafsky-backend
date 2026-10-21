"""
ICICI & Payment Retry Handler.
Handles payment retry logic, ICICI callback parsing/verification,
and browser Back/Cancel redirects.
"""

import uuid
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.models.payment import (
    PaymentTransaction, PaymentStatus, PaymentMethod, PaymentStateMachine
)
from app.schemas.payment import PaymentInitiateRequest
from app.services.timeline_service import TimelineService
from app.config import settings

logger = logging.getLogger(__name__)


def retry_booking_payment(
    cls,
    db: Session,
    booking_ref: str,
    customer_email: Optional[str] = None,
    user_id: Optional[str] = None,
    preferred_gateway: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retries payment on an existing PENDING booking without duplicating the booking.
    Reuses an unpaid Razorpay order when one already exists; otherwise creates a new order.
    """
    from app.models.schema import Booking, BookingStatus
    from app.providers.razorpay_provider import razorpay_provider

    booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
    if not booking:
        raise ValueError(f"Booking with reference '{booking_ref}' not found.")

    if booking.status == BookingStatus.CONFIRMED:
        raise ValueError(f"Booking '{booking_ref}' is already confirmed and paid.")

    if booking.status in (BookingStatus.CANCELLED, BookingStatus.REJECTED):
        raise ValueError(f"Booking '{booking_ref}' is cancelled/rejected and cannot be paid.")

    frozen = cls.frozen_gateway_for_booking(db, booking)
    gateway_to_use = frozen or cls.active_payment_gateway(preferred=preferred_gateway)
    if gateway_to_use == "ICICI":
        return cls._retry_icici_payment(db, booking, user_id=user_id)

    cls.assert_gateway_exclusive(db, booking, "RAZORPAY")

    authoritative_amount = float(booking.total_amount)
    amount_paise = round(authoritative_amount * 100)

    existing_tx = db.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.entity_id.in_([booking.booking_ref, str(booking.id)]),
            PaymentTransaction.status == PaymentStatus.PENDING,
            PaymentTransaction.gateway_provider == "RAZORPAY",
        )
        .order_by(desc(PaymentTransaction.created_at))
    )
    existing_oid = (existing_tx.gateway_payment_id if existing_tx else "") or ""
    if existing_tx and existing_oid.startswith("order_") and not existing_oid.startswith("order_sim_"):
        return cls._public_checkout_payload(
            booking_ref=booking.booking_ref,
            gateway="RAZORPAY",
            transaction_ref=existing_tx.transaction_ref,
            total_amount=authoritative_amount,
            currency=booking.currency or "INR",
            razorpay_order_id=existing_oid,
            razorpay_key_id=razorpay_provider.key_id,
            razorpay_amount_paise=amount_paise,
        )
    ref = f"PAY-RETRY-{uuid.uuid4().hex[:6].upper()}"

    intent = razorpay_provider.create_order(
        amount=authoritative_amount,
        currency=booking.currency or "INR",
        receipt=booking.booking_ref,
        notes={
            "transaction_ref": ref,
            "booking_ref": booking.booking_ref,
            "is_retry": "true",
            "channel": "web"
        }
    )
    if not intent.get("success") or not intent.get("order_id"):
        raise ValueError(intent.get("error") or "Razorpay order could not be created.")
    if str(intent.get("order_id", "")).startswith("order_sim_") and settings.is_production:
        raise ValueError("Payment gateway is not configured for live checkout.")
    order_id = intent.get("order_id")
    amount_paise = int(intent.get("amount") or amount_paise)

    tx = PaymentTransaction(
        transaction_ref=ref,
        entity_type="AIRPORT_BOOKING",
        entity_id=booking.booking_ref,
        customer_id=user_id or (str(booking.user_id) if booking.user_id else None),
        amount=authoritative_amount,
        currency=booking.currency or "INR",
        payment_method=PaymentMethod.CREDIT_CARD,
        status=PaymentStatus.PENDING,
        gateway_provider="RAZORPAY",
        gateway_payment_id=order_id,
        gateway_response=intent
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    try:
        TimelineService.add_entry(
            db,
            entity_type="AIRPORT_BOOKING",
            entity_id=booking.booking_ref,
            event_type="PAYMENT_RETRY_INITIATED",
            title=f"Payment Retry Initiated ({ref})",
            details={"order_id": order_id, "amount": authoritative_amount}
        )
    except Exception:
        pass

    return cls._public_checkout_payload(
        booking_ref=booking.booking_ref,
        gateway="RAZORPAY",
        transaction_ref=ref,
        total_amount=authoritative_amount,
        currency=booking.currency or "INR",
        razorpay_order_id=order_id,
        razorpay_key_id=razorpay_provider.key_id,
        razorpay_amount_paise=amount_paise,
    )


def retry_icici_payment(cls, db: Session, booking, user_id: Optional[str] = None) -> Dict[str, Any]:
    cls.assert_gateway_exclusive(db, booking, "ICICI")
    existing_tx = db.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.entity_id.in_([booking.booking_ref, str(booking.id)]),
            PaymentTransaction.status == PaymentStatus.PENDING,
            PaymentTransaction.gateway_provider == "ICICI",
        )
        .order_by(desc(PaymentTransaction.created_at))
    )
    if existing_tx and isinstance(existing_tx.gateway_response, dict):
        payment_url = existing_tx.gateway_response.get("payment_url")
        if payment_url:
            return cls._public_checkout_payload(
                booking_ref=booking.booking_ref,
                gateway="ICICI",
                transaction_ref=existing_tx.transaction_ref,
                total_amount=float(booking.total_amount),
                currency=booking.currency or "INR",
                icici_redirect_url=payment_url,
                icici_merchant_txn_no=existing_tx.gateway_payment_id,
            )

    init_request = PaymentInitiateRequest(
        entity_type="AIRPORT_BOOKING",
        entity_id=str(booking.booking_ref),
        customer_name=booking.passenger_name or "Valued Guest",
        customer_email=booking.passenger_email or "guest@shafsky.com",
        amount=float(booking.total_amount),
        currency=booking.currency or "INR",
        payment_method=PaymentMethod.CREDIT_CARD,
        customer_id=user_id or (str(booking.user_id) if booking.user_id else None),
    )
    tx = cls._initiate_icici_payment(db, init_request)
    gw = tx.gateway_response if isinstance(tx.gateway_response, dict) else {}
    return cls._public_checkout_payload(
        booking_ref=booking.booking_ref,
        gateway="ICICI",
        transaction_ref=tx.transaction_ref,
        total_amount=float(booking.total_amount),
        currency=booking.currency or "INR",
        icici_redirect_url=gw.get("payment_url"),
        icici_merchant_txn_no=tx.gateway_payment_id,
    )


def handle_icici_browser_cancel(
    cls,
    db: Session,
    query_params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    ICICI hosted-page Back/Cancel uses browser GET to merchant returnURL.
    Never confirms payment. Redirects customer to payment-result as cancelled.
    """
    from app.providers.icici_provider import icici_provider

    params = icici_provider.flatten_callback_payload(query_params or {})
    merchant_txn_no = str(
        params.get("merchantTxnNo")
        or params.get("merchant_txn_no")
        or ""
    ).strip()
    booking_ref = str(params.get("ref") or params.get("booking_ref") or "").strip() or "unknown"

    if merchant_txn_no:
        transaction = db.scalar(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.gateway_provider == "ICICI",
                PaymentTransaction.gateway_payment_id == merchant_txn_no,
            )
            .order_by(desc(PaymentTransaction.created_at))
        )
        if transaction and transaction.entity_id:
            booking_ref = transaction.entity_id
            logger.info(
                "[ICICI] browser Back/Cancel for txn=%s booking=%s status=%s (no confirmation)",
                merchant_txn_no,
                booking_ref,
                transaction.status.value if hasattr(transaction.status, "value") else transaction.status,
            )

    redirect_url = icici_provider.resolve_customer_return_url(
        booking_ref,
        success=False,
        cancelled=True,
    )
    return {
        "success": False,
        "cancelled": True,
        "booking_ref": booking_ref,
        "redirect_url": redirect_url,
    }


def handle_icici_browser_back(
    cls,
    db: Session,
    query_params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    ICICI hosted-page Back/Cancel navigates with GET to merchant returnURL.
    Never confirms payment. Resolves booking_ref when merchantTxnNo is known,
    then redirects to the customer payment-result page as cancelled.
    """
    from app.providers.icici_provider import icici_provider

    params = icici_provider.flatten_callback_payload(query_params or {})
    merchant_txn_no = str(params.get("merchantTxnNo") or "").strip()
    booking_ref = str(params.get("ref") or params.get("booking_ref") or "").strip() or "unknown"

    if merchant_txn_no:
        transaction = db.scalar(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.gateway_provider == "ICICI",
                PaymentTransaction.gateway_payment_id == merchant_txn_no,
            )
            .order_by(desc(PaymentTransaction.created_at))
        )
        if transaction and transaction.entity_id:
            booking_ref = transaction.entity_id

    redirect_url = icici_provider.resolve_customer_return_url(
        booking_ref,
        success=False,
        cancelled=True,
    )
    logger.info(
        "[ICICI] browser Back/Cancel GET handled txn=%s booking=%s (no confirmation)",
        merchant_txn_no or "-",
        booking_ref,
    )
    return {
        "success": False,
        "cancelled": True,
        "booking_ref": booking_ref,
        "redirect_url": redirect_url,
    }


def process_icici_callback(cls, db: Session, raw_params: Dict[str, Any]) -> Dict[str, Any]:
    """Verify ICICI callback + STATUS, then confirm via handle_verified_payment."""
    from app.providers.icici_provider import icici_provider

    params = icici_provider.flatten_callback_payload(raw_params)
    merchant_txn_no = str(params.get("merchantTxnNo") or "").strip()
    if not merchant_txn_no:
        return {
            "success": False,
            "error": "Missing merchantTxnNo",
            "redirect_url": icici_provider.resolve_customer_return_url("unknown", success=False),
        }

    transaction = db.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.gateway_provider == "ICICI",
            PaymentTransaction.gateway_payment_id == merchant_txn_no,
        )
        .order_by(desc(PaymentTransaction.created_at))
    )
    if not transaction:
        logger.warning("[ICICI] callback for unknown merchantTxnNo=%s", merchant_txn_no)
        return {
            "success": False,
            "error": "Unknown transaction",
            "redirect_url": icici_provider.resolve_customer_return_url("unknown", success=False),
        }

    booking_ref = transaction.entity_id
    redirect_fail = icici_provider.resolve_customer_return_url(booking_ref, success=False)
    redirect_ok = icici_provider.resolve_customer_return_url(booking_ref, success=True)

    if transaction.status == PaymentStatus.SUCCESSFUL:
        return {
            "success": True,
            "already_confirmed": True,
            "booking_ref": booking_ref,
            "redirect_url": redirect_ok,
        }

    identity = icici_provider.verify_callback_identity(
        params,
        expected_merchant_txn_no=merchant_txn_no,
        expected_amount=float(transaction.amount),
    )
    if not identity.get("ok"):
        logger.warning(
            "[ICICI] callback verification failed txn=%s errors=%s",
            merchant_txn_no,
            identity.get("errors"),
        )
        try:
            if PaymentStateMachine.can_transition(transaction.status, PaymentStatus.FAILED):
                transaction.status = PaymentStatus.FAILED
                notes = (transaction.notes or "") + f" | ICICI callback verify failed: {identity.get('errors')}"
                transaction.notes = notes[:2000]
                db.commit()
        except Exception:
            db.rollback()
        return {
            "success": False,
            "error": "Callback verification failed",
            "booking_ref": booking_ref,
            "redirect_url": redirect_fail,
        }

    status_result = icici_provider.check_transaction_status(
        merchant_txn_no=merchant_txn_no,
        original_txn_no=str(params.get("txnID") or params.get("originalTxnNo") or merchant_txn_no),
    )
    if status_result.get("timeout") or status_result.get("pending"):
        try:
            if PaymentStateMachine.can_transition(transaction.status, PaymentStatus.PROCESSING):
                transaction.status = PaymentStatus.PROCESSING
                merged = dict(transaction.gateway_response or {})
                merged["last_status"] = status_result.get("data")
                transaction.gateway_response = merged
                db.commit()
        except Exception:
            db.rollback()
        redirect_pending = icici_provider.resolve_customer_return_url(
            booking_ref, success=False, pending=True
        )
        return {
            "success": False,
            "pending": True,
            "error": status_result.get("error") or "Payment pending",
            "booking_ref": booking_ref,
            "redirect_url": redirect_pending,
        }

    if not status_result.get("success") or not status_result.get("paid"):
        try:
            if PaymentStateMachine.can_transition(transaction.status, PaymentStatus.FAILED):
                transaction.status = PaymentStatus.FAILED
                merged = dict(transaction.gateway_response or {})
                merged["last_status"] = status_result.get("data")
                transaction.gateway_response = merged
                db.commit()
        except Exception:
            db.rollback()
        return {
            "success": False,
            "error": status_result.get("error")
            or status_result.get("txnRespDescription")
            or "Payment not successful",
            "booking_ref": booking_ref,
            "redirect_url": redirect_fail,
        }

    # STATUS amount must match booking/transaction (rupees, not paise)
    status_amount_raw = status_result.get("amount")
    if status_amount_raw in (None, ""):
        logger.warning("[ICICI] STATUS missing amount for txn=%s", merchant_txn_no)
        return {
            "success": False,
            "error": "STATUS amount missing",
            "booking_ref": booking_ref,
            "redirect_url": redirect_fail,
        }
    try:
        from app.providers.icici_provider import format_icici_amount
        if format_icici_amount(status_amount_raw) != format_icici_amount(transaction.amount):
            logger.error(
                "[ICICI] STATUS amount mismatch txn=%s status=%s expected=%s",
                merchant_txn_no,
                status_amount_raw,
                transaction.amount,
            )
            return {
                "success": False,
                "error": "STATUS amount mismatch",
                "booking_ref": booking_ref,
                "redirect_url": redirect_fail,
            }
    except Exception:
        logger.exception("[ICICI] STATUS amount parse failed txn=%s", merchant_txn_no)
        return {
            "success": False,
            "error": "STATUS amount invalid",
            "booking_ref": booking_ref,
            "redirect_url": redirect_fail,
        }

    txn_id = str(status_result.get("txnID") or params.get("txnID") or merchant_txn_no)
    from app.providers.icici_provider import format_icici_amount as _fmt_amt
    result = cls.handle_verified_payment(
        db,
        event_name="ICICI_STATUS_SUC",
        gateway_provider="ICICI",
        order_id=merchant_txn_no,
        payment_id=txn_id,
        booking_ref=booking_ref,
        signature=None,
        raw_payload={
            "icici_callback": {k: v for k, v in params.items() if k.lower() != "securehash"},
            "icici_status": status_result.get("data"),
        },
        channel="web",
        amount=float(_fmt_amt(status_amount_raw)),
        currency=transaction.currency or "INR",
    )
    ok = bool(result.get("success") or result.get("already_processed"))
    return {
        "success": ok,
        "booking_ref": booking_ref,
        "redirect_url": redirect_ok if ok else redirect_fail,
        "result": result,
    }
