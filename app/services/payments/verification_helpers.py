"""
Payment Verification Helpers.
Decomposes amount validation, post-commit fulfillment notifications,
WhatsApp state synchronization, authorized/failed/expired/refund event processing.
"""

import uuid
import logging
from typing import Dict, Any, Optional, Tuple
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, or_

from app.models.payment import (
    PaymentTransaction, Invoice, PaymentStatus, InvoiceStatus, PaymentMethod, PaymentStateMachine
)
from app.services.timeline_service import TimelineService
from app.services.admin_service import AdminService
from app.services.notification_service import NotificationService
from app.config import settings

logger = logging.getLogger(__name__)


def validate_amount_and_currency(
    db: Session,
    *,
    event_name: str,
    raw_payload: Optional[Dict[str, Any]],
    booking,
    airport_booking,
    transaction: Optional[PaymentTransaction],
    resolved_booking_ref: Optional[str],
    amount: Optional[float],
    currency: Optional[str],
    order_id: Optional[str],
    payment_id: Optional[str],
    payment_link_id: Optional[str],
    gateway_provider: str,
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[PaymentTransaction]]:
    """
    Reconciles incoming amount and currency with the persisted booking or transaction records.
    Returns (True, None, transaction) on success, or (False, error_response_dict, None) on failure.
    """
    received_amount_raw = amount
    received_currency_raw = currency

    expected_amt_val = (
        float(booking.total_amount) if booking
        else (float(airport_booking.total_price) if airport_booking
              else (float(transaction.amount) if transaction else None))
    )
    expected_curr_val = (
        booking.currency if booking
        else (airport_booking.currency if airport_booking
              else (transaction.currency if transaction else "INR"))
    ) or "INR"
    exp_curr_norm = (expected_curr_val or "INR").strip().upper()

    if event_name == "ICICI_STATUS_SUC":
        # ICICI amounts are rupees (2dp), not paise
        if received_amount_raw is None or received_currency_raw is None:
            return False, {
                "success": False,
                "status": "PAYMENT_DATA_INCOMPLETE",
                "reason": "ICICI STATUS missing amount or currency.",
                "booking_ref": resolved_booking_ref,
            }, None
        try:
            rec_rupees = Decimal(str(received_amount_raw))
            exp_rupees = Decimal(str(expected_amt_val))
        except Exception:
            return False, {
                "success": False,
                "status": "PAYMENT_DATA_INCOMPLETE",
                "reason": "Invalid ICICI amount format.",
                "booking_ref": resolved_booking_ref,
            }, None
        if (received_currency_raw or "").strip().upper() != exp_curr_norm:
            return False, {
                "success": False,
                "status": "CURRENCY_MISMATCH",
                "reason": "ICICI currency does not match booking.",
                "booking_ref": resolved_booking_ref,
            }, None
        if abs(rec_rupees - exp_rupees) > Decimal("0.001"):
            return False, {
                "success": False,
                "status": "AMOUNT_MISMATCH",
                "reason": (
                    f"ICICI amount {rec_rupees} does not match expected "
                    f"booking amount {exp_rupees}."
                ),
                "booking_ref": resolved_booking_ref,
                "expected_amount": float(exp_rupees),
                "received_amount": float(rec_rupees),
            }, None
        logger.info(
            f"[PaymentService] PAYMENT_AMOUNT_CHECK (ICICI): "
            f"expected_amount={float(exp_rupees)}, received_amount={float(rec_rupees)}, "
            f"result=EXACT_MATCH"
        )
    else:
        # Razorpay path (amounts in paise)
        if raw_payload and isinstance(raw_payload, dict):
            pl = raw_payload.get("payload", {})
            pay_ent = pl.get("payment", {}).get("entity", {}) if isinstance(pl.get("payment"), dict) else {}
            ord_ent = pl.get("order", {}).get("entity", {}) if isinstance(pl.get("order"), dict) else {}
            plink_ent = pl.get("payment_link", {}).get("entity", {}) if isinstance(pl.get("payment_link"), dict) else {}

            if received_amount_raw is None:
                if pay_ent.get("amount") is not None:
                    received_amount_raw = pay_ent.get("amount")
                elif ord_ent.get("amount_paid") is not None:
                    received_amount_raw = ord_ent.get("amount_paid")
                elif ord_ent.get("amount") is not None:
                    received_amount_raw = ord_ent.get("amount")
                elif plink_ent.get("amount_paid") is not None:
                    received_amount_raw = plink_ent.get("amount_paid")
                elif plink_ent.get("amount") is not None:
                    received_amount_raw = plink_ent.get("amount")

            if not received_currency_raw:
                received_currency_raw = pay_ent.get("currency") or ord_ent.get("currency") or plink_ent.get("currency")

        # For VERIFY_ENDPOINT, if amount or currency is not in payload, fetch from Razorpay API or transaction
        if event_name == "VERIFY_ENDPOINT" and (received_amount_raw is None or not received_currency_raw):
            if order_id and not str(order_id).startswith("order_sim_"):
                from app.providers.razorpay_provider import razorpay_provider
                fetch_res = razorpay_provider.fetch_order(str(order_id))
                if fetch_res.get("success") and isinstance(fetch_res.get("order"), dict):
                    order_data = fetch_res.get("order")
                    if received_amount_raw is None and order_data.get("amount") is not None:
                        received_amount_raw = order_data.get("amount")
                    if not received_currency_raw and order_data.get("currency"):
                        received_currency_raw = order_data.get("currency")
            if (received_amount_raw is None or not received_currency_raw) and not str(order_id or "").startswith("order_sim_"):
                from app.providers.razorpay_provider import razorpay_provider as _rzp
                if _rzp.is_configured():
                    logger.error(
                        f"[PaymentService] PAYMENT_AMOUNT_CHECK: gateway order for '{order_id}' "
                        f"could not be confirmed; rejecting verification for booking '{resolved_booking_ref}'."
                    )
                    return False, {
                        "success": False,
                        "status": "PAYMENT_DATA_INCOMPLETE",
                        "reason": "Gateway order could not be confirmed for verification.",
                        "booking_ref": resolved_booking_ref,
                    }, None
                if received_amount_raw is None and transaction and transaction.amount is not None:
                    received_amount_raw = round(float(transaction.amount) * 100)
                if not received_currency_raw and transaction and transaction.currency:
                    received_currency_raw = transaction.currency

        if event_name == "VERIFY_ENDPOINT" or raw_payload is None:
            if received_amount_raw is None and transaction and transaction.amount is not None:
                received_amount_raw = round(float(transaction.amount) * 100)
            if not received_currency_raw and transaction and transaction.currency:
                received_currency_raw = transaction.currency

        if received_amount_raw is None or received_currency_raw is None or str(received_currency_raw).strip() == "":
            logger.warning(
                f"[PaymentService] PAYMENT_AMOUNT_CHECK: Incomplete payment data for event '{event_name}' (booking '{resolved_booking_ref}'). "
                f"expected_amount={expected_amt_val}, received_amount={received_amount_raw}, "
                f"expected_currency={expected_curr_val}, received_currency={received_currency_raw}, "
                f"result=PAYMENT_DATA_INCOMPLETE"
            )
            return False, {
                "success": False,
                "status": "PAYMENT_DATA_INCOMPLETE",
                "reason": "Payment data missing required amount or currency data.",
                "booking_ref": resolved_booking_ref
            }, None

        rec_curr_norm = str(received_currency_raw).strip().upper()

        if exp_curr_norm != rec_curr_norm:
            logger.error(
                f"[PaymentService] PAYMENT_AMOUNT_CHECK: "
                f"expected_amount={expected_amt_val}, received_amount={received_amount_raw}, "
                f"expected_currency={exp_curr_norm}, received_currency={rec_curr_norm}, "
                f"result=CURRENCY_MISMATCH"
            )
            return False, {
                "success": False,
                "status": "CURRENCY_MISMATCH",
                "reason": f"Payment currency '{rec_curr_norm}' does not match expected booking currency '{exp_curr_norm}'.",
                "booking_ref": resolved_booking_ref,
                "expected_currency": exp_curr_norm,
                "received_currency": rec_curr_norm
            }, None

        try:
            rec_paise = Decimal(str(received_amount_raw))
            rec_rupees = rec_paise / Decimal("100")
            exp_rupees = Decimal(str(expected_amt_val))
        except Exception as num_err:
            logger.error(f"[PaymentService] PAYMENT_AMOUNT_CHECK: Numeric parsing error: {num_err}")
            return False, {
                "success": False,
                "status": "PAYMENT_DATA_INCOMPLETE",
                "reason": "Invalid numeric format in payment amount.",
                "booking_ref": resolved_booking_ref
            }, None

        if abs(rec_rupees - exp_rupees) > Decimal("0.001"):
            logger.error(
                f"[PaymentService] PAYMENT_AMOUNT_CHECK: "
                f"expected_amount={float(exp_rupees)}, received_amount={float(rec_rupees)}, "
                f"expected_currency={exp_curr_norm}, received_currency={rec_curr_norm}, "
                f"result=AMOUNT_MISMATCH"
            )
            return False, {
                "success": False,
                "status": "AMOUNT_MISMATCH",
                "reason": f"Payment amount {rec_rupees} {rec_curr_norm} does not match expected booking amount {exp_rupees} {exp_curr_norm}.",
                "booking_ref": resolved_booking_ref,
                "expected_amount": float(exp_rupees),
                "received_amount": float(rec_rupees)
            }, None

        logger.info(
            f"[PaymentService] PAYMENT_AMOUNT_CHECK: "
            f"expected_amount={float(exp_rupees)}, received_amount={float(rec_rupees)}, "
            f"expected_currency={exp_curr_norm}, received_currency={rec_curr_norm}, "
            f"result=EXACT_MATCH"
        )

    if transaction is None:
        transaction = PaymentTransaction(
            transaction_ref=f"PAY-REC-{uuid.uuid4().hex[:8].upper()}",
            entity_type="AIRPORT_BOOKING",
            entity_id=str(resolved_booking_ref),
            customer_id=(
                str(booking.user_id)
                if booking and booking.user_id
                else (
                    airport_booking.customer_id
                    if airport_booking and airport_booking.customer_id
                    else None
                )
            ),
            amount=float(exp_rupees),
            currency=exp_curr_norm,
            payment_method=PaymentMethod.CREDIT_CARD,
            status=PaymentStatus.PENDING,
            gateway_provider=gateway_provider,
            gateway_payment_id=order_id or payment_id or payment_link_id,
            gateway_response=raw_payload,
        )
        db.add(transaction)
        db.flush()

    return True, None, transaction


def dispatch_payment_fulfillment_side_effects(db: Session, booking, invoice_id_to_fulfill: Optional[str]) -> None:
    """Trigger PDF generation and notifications after commit. Never blocks webhook."""
    if invoice_id_to_fulfill:
        try:
            from app.services.pdf_service import schedule_invoice_fulfillment
            schedule_invoice_fulfillment(invoice_id_to_fulfill)
        except Exception as pdf_err:
            logger.error("[PaymentService] Invoice fulfillment scheduling failed: %s", type(pdf_err).__name__)
            if booking:
                try:
                    meta = booking.metadata_json or {}
                    NotificationService.notify_booking_confirmed(db, {
                        "booking_ref": booking.booking_ref,
                        "passenger_name": booking.passenger_name,
                        "passenger_email": booking.passenger_email,
                        "passenger_phone": booking.passenger_phone,
                        "flight_num": booking.flight_num,
                        "origin_code": booking.origin_code,
                        "dest_code": booking.dest_code,
                        "airport_code": meta.get("service_airport") or booking.origin_code or booking.dest_code,
                        "journey_type": meta.get("journey_type") or booking.service_type,
                        "service_type": booking.service_type,
                        "service_name": meta.get("package") or booking.service_type,
                        "departure_time": booking.departure_time.isoformat() if booking.departure_time else None,
                        "terminal": meta.get("terminal"),
                        "total_amount": float(booking.total_amount) if booking.total_amount is not None else 0.0,
                        "currency": booking.currency,
                        "invoice_attached": False,
                    })
                except Exception as notif_err:
                    logger.error(f"[PaymentService] Confirmation notification error: {notif_err}")
    elif booking:
        try:
            meta = booking.metadata_json or {}
            NotificationService.notify_booking_confirmed(db, {
                "booking_ref": booking.booking_ref,
                "passenger_name": booking.passenger_name,
                "passenger_email": booking.passenger_email,
                "passenger_phone": booking.passenger_phone,
                "flight_num": booking.flight_num,
                "origin_code": booking.origin_code,
                "dest_code": booking.dest_code,
                "airport_code": meta.get("service_airport") or booking.origin_code or booking.dest_code,
                "journey_type": meta.get("journey_type") or booking.service_type,
                "service_type": booking.service_type,
                "service_name": meta.get("package") or booking.service_type,
                "departure_time": booking.departure_time.isoformat() if booking.departure_time else None,
                "terminal": meta.get("terminal"),
                "total_amount": float(booking.total_amount) if booking.total_amount is not None else 0.0,
                "currency": booking.currency,
            })
        except Exception as notif_err:
            logger.error(f"[PaymentService] Confirmation notification error: {notif_err}")


def sync_whatsapp_conversation_on_payment(db: Session, resolved_booking_ref: Optional[str], booking) -> None:
    """Update WhatsApp Conversation State if relevant."""
    try:
        from app.models.whatsapp_models import WhatsAppConversation
        conv = None
        if resolved_booking_ref:
            conv = db.scalar(
                select(WhatsAppConversation).where(WhatsAppConversation.booking_ref == resolved_booking_ref)
            )
        if not conv and booking is not None:
            conv = db.scalar(
                select(WhatsAppConversation).where(WhatsAppConversation.booking_id == booking.id)
            )
        if conv:
            conv.payment_status = "SUCCESSFUL"
            conv.current_state = "COMPLETED"
            conv.updated_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as wa_err:
        logger.debug(f"[PaymentService] WhatsApp session sync notice: {wa_err}")


def handle_payment_authorized(
    db: Session,
    booking,
    transaction: Optional[PaymentTransaction],
    resolved_booking_ref: Optional[str],
    order_id: Optional[str],
    payment_id: Optional[str],
    channel: Optional[str],
    raw_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Funds pre-authorized but NOT yet captured. Booking stays PENDING."""
    if transaction and transaction.status == PaymentStatus.PENDING:
        PaymentStateMachine.validate_transition(transaction.status, PaymentStatus.PROCESSING)
        transaction.status = PaymentStatus.PROCESSING
        if payment_id:
            transaction.gateway_payment_id = payment_id
        if raw_payload:
            transaction.gateway_response = raw_payload
        transaction.updated_at = datetime.now(timezone.utc)

    if booking:
        b_meta = dict(booking.metadata_json or {})
        b_meta["payment_status"] = "AUTHORIZED"
        b_meta["order_id"] = order_id
        b_meta["payment_id"] = payment_id
        b_meta["authorized_at"] = datetime.now(timezone.utc).isoformat()
        b_meta["channel"] = channel or b_meta.get("channel") or "Web Checkout"
        booking.metadata_json = b_meta
        booking.updated_at = datetime.now(timezone.utc)

    try:
        TimelineService.add_entry(
            db,
            entity_type="AIRPORT_BOOKING",
            entity_id=resolved_booking_ref or "PAYMENT",
            event_type="PAYMENT_AUTHORIZED",
            title=f"Payment Pre-Authorized ({resolved_booking_ref})",
            details={
                "order_id": order_id,
                "payment_id": payment_id,
                "status": "AUTHORIZED",
                "note": "Awaiting capture before booking confirmation",
            },
        )
    except Exception:
        pass

    db.commit()
    return {
        "success": True,
        "status": "AUTHORIZED",
        "booking_ref": resolved_booking_ref,
        "payment_id": payment_id,
    }


def handle_payment_link_expired_cancelled(
    db: Session,
    booking,
    transaction: Optional[PaymentTransaction],
    resolved_booking_ref: Optional[str],
    event_name: str,
    raw_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Payment Link expired/cancelled – booking stays PENDING and recoverable."""
    from app.services.payments.whatsapp_links import mark_payment_link_terminal

    terminal_status = (
        PaymentStatus.EXPIRED if "expired" in event_name else PaymentStatus.CANCELLED
    )
    link_status_str = "expired" if "expired" in event_name else "cancelled"
    if transaction:
        mark_payment_link_terminal(transaction, terminal_status, link_status_str)
        if raw_payload:
            existing = dict(transaction.gateway_response or {})
            existing["link_status"] = link_status_str
            transaction.gateway_response = existing

    try:
        from app.models.whatsapp_models import WhatsAppConversation
        conv = None
        if resolved_booking_ref:
            conv = db.scalar(
                select(WhatsAppConversation).where(WhatsAppConversation.booking_ref == resolved_booking_ref)
            )
        if not conv and booking is not None:
            conv = db.scalar(
                select(WhatsAppConversation).where(WhatsAppConversation.booking_id == booking.id)
            )
        if conv:
            conv.payment_status = terminal_status.value
            conv.current_state = "WAITING_PAYMENT"
            conv.updated_at = datetime.now(timezone.utc)
    except Exception as wa_err:
        logger.debug("[PaymentService] WhatsApp expiry sync notice: %s", wa_err)
    db.commit()
    return {
        "success": True,
        "status": terminal_status.value,
        "booking_ref": resolved_booking_ref,
    }


def handle_payment_failed(
    db: Session,
    booking,
    transaction: Optional[PaymentTransaction],
    resolved_booking_ref: Optional[str],
    order_id: Optional[str],
    payment_id: Optional[str],
    event_name: str,
    raw_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Payment failed event. Keep booking PENDING to allow retry."""
    from app.models.schema import BookingStatus

    if transaction and transaction.status != PaymentStatus.SUCCESSFUL:
        transaction.status = PaymentStatus.FAILED
        if raw_payload:
            transaction.gateway_response = raw_payload
        transaction.updated_at = datetime.now(timezone.utc)

    if booking and booking.status == BookingStatus.PENDING:
        logger.info(f"[PaymentService] Payment failed for booking '{resolved_booking_ref}'; keeping booking PENDING for retry.")

    try:
        AdminService.log_audit_action(
            db,
            actor_email=booking.passenger_email if booking else "system@shafsky.com",
            action="PAYMENT_FAILED",
            resource_type="PAYMENT",
            resource_id=str(transaction.id) if transaction else (str(booking.id) if booking else ""),
            details={"order_id": order_id, "payment_id": payment_id, "event": event_name}
        )
    except Exception:
        pass

    db.commit()
    return {
        "success": True,
        "status": "FAILED",
        "booking_ref": resolved_booking_ref
    }


def handle_payment_refund(
    db: Session,
    booking,
    transaction: Optional[PaymentTransaction],
    resolved_booking_ref: Optional[str],
    order_id: Optional[str],
    payment_id: Optional[str],
    event_name: str,
    raw_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Refund events: refund.created, refund.processed, payment.refunded, etc."""
    from app.models.schema import BookingStatus

    if not transaction:
        return {
            "success": False,
            "status": "REFUND_TRANSACTION_NOT_FOUND",
            "reason": "Refund event could not be matched to a payment transaction.",
            "booking_ref": resolved_booking_ref,
        }
    if transaction.status not in {
        PaymentStatus.SUCCESSFUL,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
    }:
        return {
            "success": False,
            "status": "INVALID_REFUND_STATE",
            "reason": (
                f"Cannot apply a refund to a transaction in "
                f"'{transaction.status.value}' state."
            ),
            "booking_ref": resolved_booking_ref,
        }

    refund_amount_raw = None
    amount_refunded_raw = None
    payment_amount_raw = None
    refund_status_str = None

    if raw_payload and isinstance(raw_payload, dict):
        pl = raw_payload.get("payload", {})
        ref_ent = pl.get("refund", {}).get("entity", {}) if isinstance(pl.get("refund"), dict) else {}
        pay_ent = pl.get("payment", {}).get("entity", {}) if isinstance(pl.get("payment"), dict) else {}

        refund_amount_raw = ref_ent.get("amount")
        amount_refunded_raw = pay_ent.get("amount_refunded")
        payment_amount_raw = pay_ent.get("amount")
        refund_status_str = pay_ent.get("refund_status") or ref_ent.get("status")

    is_full_refund = None
    target_amount = float(booking.total_amount) if booking else (float(transaction.amount) if transaction else None)

    if amount_refunded_raw is not None and payment_amount_raw is not None:
        is_full_refund = float(amount_refunded_raw) >= float(payment_amount_raw)
    elif refund_status_str in ("partial", "PARTIAL"):
        is_full_refund = False
    elif refund_status_str in ("full", "FULL"):
        is_full_refund = True
    elif refund_amount_raw is not None and target_amount is not None:
        refund_rupees = float(refund_amount_raw) / 100.0
        is_full_refund = round(refund_rupees, 2) >= round(target_amount, 2)

    if is_full_refund is None:
        return {
            "success": False,
            "status": "REFUND_DATA_INCOMPLETE",
            "reason": "Refund event did not contain enough amount or status data.",
            "booking_ref": resolved_booking_ref,
        }

    transaction.status = PaymentStatus.REFUNDED if is_full_refund else PaymentStatus.PARTIALLY_REFUNDED
    transaction.updated_at = datetime.now(timezone.utc)
    for inv in transaction.invoices:
        inv.status = InvoiceStatus.CANCELLED if is_full_refund else InvoiceStatus.PARTIALLY_PAID

    if booking:
        if is_full_refund:
            booking.status = BookingStatus.CANCELLED
            booking.updated_at = datetime.now(timezone.utc)
            logger.info(f"[PaymentService] Full refund processed for booking '{resolved_booking_ref}'. Booking status set to CANCELLED.")
        else:
            logger.info(f"[PaymentService] Partial refund processed for booking '{resolved_booking_ref}'. Booking remains '{booking.status.value}'.")

    try:
        TimelineService.add_entry(
            db,
            entity_type=transaction.entity_type if transaction else "AIRPORT_BOOKING",
            entity_id=resolved_booking_ref or "PAYMENT",
            event_type="PAYMENT_REFUNDED",
            title=f"{'Full' if is_full_refund else 'Partial'} Refund Processed ({resolved_booking_ref})",
            details={
                "event": event_name,
                "is_full_refund": is_full_refund,
                "payment_id": payment_id,
                "order_id": order_id
            }
        )
    except Exception as time_err:
        logger.warning(f"[PaymentService] Timeline logging notice: {time_err}")

    db.commit()

    return {
        "success": True,
        "status": "REFUNDED" if is_full_refund else "PARTIALLY_REFUNDED",
        "is_full_refund": is_full_refund,
        "booking_ref": resolved_booking_ref
    }
