"""
Payment Verification Service.
Canonical, idempotent processor for all verified payment events (Razorpay, ICICI, WhatsApp).
"""

import os
import uuid
import hmac
import hashlib
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, text

from app.models.payment import (
    PaymentTransaction, Invoice, PaymentStatus, PaymentMethod, PaymentStateMachine
)
from app.schemas.payment import WebhookPayload
from app.services.timeline_service import TimelineService
from app.services.admin_service import AdminService
from app.config import settings

from app.services.payments.initiation import (
    extract_ids_from_razorpay_payload,
    collect_tx_gateway_ids,
    merge_gateway_response,
)
from app.services.payments.verification_helpers import (
    validate_amount_and_currency,
    dispatch_payment_fulfillment_side_effects,
    sync_whatsapp_conversation_on_payment,
    handle_payment_authorized,
    handle_payment_link_expired_cancelled,
    handle_payment_failed,
    handle_payment_refund,
)

logger = logging.getLogger(__name__)


def verify_internal_webhook_signature(payload: WebhookPayload, signature: Optional[str]) -> bool:
    secret = (os.getenv("PAYMENT_WEBHOOK_SECRET") or os.getenv("RAZORPAY_WEBHOOK_SECRET") or "").strip()
    if not secret or not signature:
        return False
    canonical = f"{payload.transaction_ref}:{payload.event_type}:{payload.gateway_payment_id}"
    computed = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


def confirmed_booking_payment_result(
    cls,
    db: Session,
    *,
    booking,
    transaction: Optional[PaymentTransaction],
    payment_id: Optional[str],
    order_id: Optional[str],
    payment_link_id: Optional[str],
    resolved_booking_ref: Optional[str],
    gateway_provider: str,
    signature: Optional[str],
    raw_payload: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return a duplicate/replay result when the booking is already confirmed."""
    existing_successful_tx = db.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.entity_id == resolved_booking_ref,
            PaymentTransaction.status == PaymentStatus.SUCCESSFUL,
            PaymentTransaction.is_duplicate.isnot(True)
        ).order_by(PaymentTransaction.created_at.asc())
    )
    if not existing_successful_tx:
        existing_successful_tx = db.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.transaction_ref == resolved_booking_ref,
                PaymentTransaction.status == PaymentStatus.SUCCESSFUL,
                PaymentTransaction.is_duplicate.isnot(True)
            ).order_by(PaymentTransaction.created_at.asc())
        )
    if not existing_successful_tx and transaction and transaction.status == PaymentStatus.SUCCESSFUL:
        existing_successful_tx = transaction

    existing_pid = existing_successful_tx.gateway_payment_id if existing_successful_tx else None
    is_same_payment = (
        (payment_id and existing_pid and payment_id == existing_pid) or
        (order_id and existing_pid and order_id == existing_pid) or
        (payment_link_id and existing_pid and payment_link_id == existing_pid)
    )
    if not is_same_payment and transaction and existing_successful_tx:
        if transaction.id == existing_successful_tx.id:
            stored_ids = collect_tx_gateway_ids(existing_successful_tx)
            incoming_ids = {i for i in (payment_id, order_id, payment_link_id) if i}
            if not incoming_ids or (incoming_ids & stored_ids):
                is_same_payment = True
    if not is_same_payment and order_id and existing_successful_tx:
        gw_resp = existing_successful_tx.gateway_response or {}
        original_order_id = None
        if isinstance(gw_resp, dict):
            original_order_id = (
                gw_resp.get("order_id") or
                gw_resp.get("payload", {}).get("payment", {}).get("entity", {}).get("order_id") or
                gw_resp.get("payload", {}).get("order", {}).get("entity", {}).get("id")
            )
        if original_order_id and order_id == original_order_id:
            is_same_payment = True
    if not is_same_payment and existing_successful_tx:
        stored_ids = collect_tx_gateway_ids(existing_successful_tx)
        incoming_ids = {i for i in (payment_id, order_id, payment_link_id) if i}
        if incoming_ids and (incoming_ids & stored_ids):
            is_same_payment = True

    if not is_same_payment and (payment_id or order_id):
        logger.warning(
            f"[PaymentService] DOUBLE PAYMENT DETECTED for booking '{resolved_booking_ref}'. "
            f"Existing payment: '{existing_pid}', New payment: '{payment_id}'."
        )
        dup_ref = f"PAY-DUP-{uuid.uuid4().hex[:6].upper()}"
        dup_tx = PaymentTransaction(
            transaction_ref=dup_ref,
            entity_type="AIRPORT_BOOKING",
            entity_id=resolved_booking_ref or str(booking.id if booking else ""),
            customer_id=str(booking.user_id) if booking and booking.user_id else None,
            amount=float(booking.total_amount) if booking else (transaction.amount if transaction else 0.0),
            currency=booking.currency if booking else (transaction.currency if transaction else "INR"),
            payment_method=PaymentMethod.CREDIT_CARD,
            status=PaymentStatus.SUCCESSFUL,
            gateway_provider=gateway_provider,
            gateway_payment_id=payment_id or order_id,
            gateway_signature=signature,
            gateway_response=raw_payload,
            is_duplicate=True,
            notes=f"DUPLICATE_PAYMENT_DETECTED: Booking was already confirmed by payment '{existing_pid}'."
        )
        db.add(dup_tx)
        db.commit()
        try:
            AdminService.log_audit_action(
                db,
                actor_email=booking.passenger_email if booking else "system@shafsky.com",
                action="DUPLICATE_PAYMENT_DETECTED",
                resource_type="PAYMENT",
                resource_id=str(dup_tx.id),
                details={
                    "booking_ref": resolved_booking_ref,
                    "original_payment_id": existing_pid,
                    "duplicate_payment_id": payment_id,
                    "duplicate_tx_ref": dup_ref
                }
            )
        except Exception:
            pass
        return {
            "success": True,
            "status": "DUPLICATE_PAYMENT_FLAGGED",
            "booking_ref": resolved_booking_ref,
            "payment_id": payment_id,
            "duplicate_tx_ref": dup_ref
        }

    logger.info(f"[PaymentService] Booking '{resolved_booking_ref}' is already confirmed and paid. Idempotent return.")
    try:
        tx_for_invoice = existing_successful_tx or transaction
        if tx_for_invoice:
            retry_inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx_for_invoice.id))
            if retry_inv and not retry_inv.pdf_url:
                from app.services.pdf_service import schedule_invoice_fulfillment
                schedule_invoice_fulfillment(str(retry_inv.id))
    except Exception as pdf_retry_err:
        logger.error(
            "[PaymentService] Invoice PDF retry scheduling failed: %s",
            type(pdf_retry_err).__name__,
        )
    return {
        "success": True,
        "status": "CONFIRMED",
        "already_confirmed": True,
        "booking_ref": resolved_booking_ref,
        "payment_id": payment_id or (transaction.gateway_payment_id if transaction else None)
    }


def handle_verified_payment(
    cls,
    db: Session,
    *,
    event_name: str,
    gateway_provider: str = "RAZORPAY",
    order_id: Optional[str] = None,
    payment_id: Optional[str] = None,
    booking_ref: Optional[str] = None,
    signature: Optional[str] = None,
    raw_payload: Optional[Dict[str, Any]] = None,
    channel: Optional[str] = None,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Canonical, idempotent processor for all verified payment events.
    Handles payment.captured, order.paid, payment_link.paid, payment.failed,
    and payment.refunded events consistently across Web and WhatsApp.
    """
    from app.models.schema import Booking, BookingStatus

    extracted_ids = extract_ids_from_razorpay_payload(raw_payload)
    payment_link_id = extracted_ids.get("payment_link_id")
    if not order_id:
        order_id = extracted_ids.get("order_id")
    if not payment_id:
        payment_id = extracted_ids.get("payment_id")
    if not booking_ref:
        booking_ref = extracted_ids.get("booking_ref")
    if not channel:
        channel = extracted_ids.get("channel") or channel

    # Serialize distinct Razorpay events targeting the same booking
    lock_key = booking_ref or order_id or payment_id or payment_link_id
    if lock_key and db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:payment_lock_key))"),
            {"payment_lock_key": f"payment:{lock_key}"},
        )

    logger.info(
        f"[PaymentService] Processing event '{event_name}' (order: {order_id}, payment: {payment_id}, plink: {payment_link_id}, ref: {booking_ref})"
    )

    # 1. Resolve PaymentTransaction
    transaction = None
    if order_id or payment_id or booking_ref or payment_link_id:
        gateway_ids = {
            value for value in (order_id, payment_id, payment_link_id) if value
        }
        if gateway_ids:
            gateway_lookup = select(PaymentTransaction).where(
                PaymentTransaction.gateway_payment_id.in_(gateway_ids)
            )
            if booking_ref:
                gateway_lookup = gateway_lookup.where(
                    PaymentTransaction.entity_id == booking_ref
                )
            transaction = db.scalar(
                gateway_lookup.order_by(PaymentTransaction.created_at.desc())
            )
        if not transaction and booking_ref:
            booking_transactions = list(db.scalars(
                select(PaymentTransaction)
                .where(
                    or_(
                        PaymentTransaction.transaction_ref == booking_ref,
                        PaymentTransaction.entity_id == booking_ref,
                    )
                )
                .order_by(PaymentTransaction.created_at.desc())
            ).all())
            if gateway_ids:
                transaction = next(
                    (
                        candidate
                        for candidate in booking_transactions
                        if gateway_ids & collect_tx_gateway_ids(candidate)
                    ),
                    None,
                )
                if not transaction:
                    bind_pending_fallback = True
                    if event_name == "VERIFY_ENDPOINT" and order_id and not str(order_id).startswith("order_sim_"):
                        from app.providers.razorpay_provider import razorpay_provider as _rzp
                        if _rzp.is_configured():
                            _bind_res = _rzp.fetch_order(str(order_id))
                            if _bind_res.get("success") and isinstance(_bind_res.get("order"), dict):
                                _receipt = str((_bind_res.get("order") or {}).get("receipt") or "")
                                if _receipt and booking_ref and _receipt != booking_ref:
                                    logger.error(
                                        f"[PaymentService] Cross-booking guard: order '{order_id}' "
                                        f"receipt '{_receipt}' does not belong to booking '{booking_ref}'."
                                    )
                                    return {
                                        "success": False,
                                        "status": "REF_MISMATCH",
                                        "reason": "Booking reference mismatch with order records.",
                                        "booking_ref": booking_ref,
                                    }
                            else:
                                bind_pending_fallback = not settings.is_production
                    if bind_pending_fallback:
                        transaction = next(
                            (
                                candidate
                                for candidate in booking_transactions
                                if candidate.status in (
                                    PaymentStatus.PENDING,
                                    PaymentStatus.PROCESSING,
                                )
                            ),
                            None,
                        )
            else:
                transaction = booking_transactions[0] if booking_transactions else None
        if booking_ref and transaction and transaction.status != PaymentStatus.SUCCESSFUL:
            successful = db.scalar(
                select(PaymentTransaction).where(
                    PaymentTransaction.entity_id == booking_ref,
                    PaymentTransaction.status == PaymentStatus.SUCCESSFUL,
                    PaymentTransaction.is_duplicate.isnot(True),
                ).order_by(PaymentTransaction.created_at.asc())
            )
            if successful:
                stored = collect_tx_gateway_ids(successful)
                incoming = {i for i in (order_id, payment_id, payment_link_id) if i}
                if incoming & stored:
                    transaction = successful

    # 2. Resolve Booking
    booking = None
    airport_booking = None
    if booking_ref:
        booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
        if not booking:
            try:
                from app.models.airport import AirportBooking
                airport_booking = db.scalar(select(AirportBooking).where(AirportBooking.booking_reference == booking_ref))
            except Exception:
                pass
    
    if not booking and not airport_booking and transaction and transaction.entity_id:
        try:
            b_uuid = uuid.UUID(transaction.entity_id)
            booking = db.scalar(select(Booking).where(or_(Booking.id == b_uuid, Booking.booking_ref == transaction.entity_id)))
            if not booking:
                try:
                    from app.models.airport import AirportBooking
                    airport_booking = db.scalar(select(AirportBooking).where(or_(AirportBooking.id == b_uuid, AirportBooking.booking_reference == transaction.entity_id)))
                except Exception:
                    pass
        except ValueError:
            booking = db.scalar(select(Booking).where(Booking.booking_ref == transaction.entity_id))
            if not booking:
                try:
                    from app.models.airport import AirportBooking
                    airport_booking = db.scalar(select(AirportBooking).where(AirportBooking.booking_reference == transaction.entity_id))
                except Exception:
                    pass

    if not transaction and not booking and not airport_booking:
        logger.warning(f"[PaymentService] Neither transaction nor booking found for order '{order_id}', ref '{booking_ref}'")
        return {
            "success": False,
            "status": "NOT_FOUND",
            "reason": f"No transaction or booking found matching order '{order_id}', payment '{payment_id}', ref '{booking_ref}'"
        }
    if event_name in ["payment.failed", "PAYMENT_FAILED"] and not transaction:
        return {
            "success": False,
            "status": "NOT_FOUND",
            "reason": "Failed payment event could not be matched to a transaction.",
            "booking_ref": booking_ref,
        }

    # Cross-safety check: Ensure payment for Booking A cannot confirm Booking B
    resolved_booking_ref = (
        booking.booking_ref if booking
        else (airport_booking.booking_reference if airport_booking
              else (transaction.entity_id if transaction else booking_ref))
    )
    if booking_ref and resolved_booking_ref and booking_ref != resolved_booking_ref:
        logger.error(f"[PaymentService] Safety mismatch: provided ref '{booking_ref}' != resolved ref '{resolved_booking_ref}'")
        return {
            "success": False,
            "status": "REF_MISMATCH",
            "reason": "Booking reference mismatch with order records."
        }

    # 3. Handle Successful Payment Events
    if event_name in [
        "payment.captured", "order.paid", "payment_link.paid",
        "VERIFY_ENDPOINT", "PAYMENT_SUCCESS", "payment.succeeded",
        "ICICI_STATUS_SUC",
    ]:
        if booking and booking.status == BookingStatus.CONFIRMED:
            confirmed_result = cls._confirmed_booking_payment_result(
                db,
                booking=booking,
                transaction=transaction,
                payment_id=payment_id,
                order_id=order_id,
                payment_link_id=payment_link_id,
                resolved_booking_ref=resolved_booking_ref,
                gateway_provider=gateway_provider,
                signature=signature,
                raw_payload=raw_payload,
            )
            if confirmed_result is not None:
                return confirmed_result

        # Validate amount and currency strictly
        if event_name in [
            "payment.captured", "order.paid", "payment_link.paid", "VERIFY_ENDPOINT", "ICICI_STATUS_SUC",
        ]:
            amt_ok, amt_err, transaction = validate_amount_and_currency(
                db,
                event_name=event_name,
                raw_payload=raw_payload,
                booking=booking,
                airport_booking=airport_booking,
                transaction=transaction,
                resolved_booking_ref=resolved_booking_ref,
                amount=amount,
                currency=currency,
                order_id=order_id,
                payment_id=payment_id,
                payment_link_id=payment_link_id,
                gateway_provider=gateway_provider,
            )
            if not amt_ok:
                return amt_err

        # Double Payment & Overpayment Protection Guard
        if booking and booking.status == BookingStatus.CONFIRMED:
            confirmed_result = cls._confirmed_booking_payment_result(
                db,
                booking=booking,
                transaction=transaction,
                payment_id=payment_id,
                order_id=order_id,
                payment_link_id=payment_link_id,
                resolved_booking_ref=resolved_booking_ref,
                gateway_provider=gateway_provider,
                signature=signature,
                raw_payload=raw_payload,
            )
            if confirmed_result is not None:
                return confirmed_result

        # Update PaymentTransaction
        if transaction:
            if transaction.status in (PaymentStatus.FAILED, PaymentStatus.EXPIRED, PaymentStatus.CANCELLED):
                logger.warning(
                    "[PaymentService] Recovering transaction %s from %s to SUCCESSFUL via verified payment.",
                    transaction.transaction_ref,
                    transaction.status,
                )
            else:
                PaymentStateMachine.validate_transition(transaction.status, PaymentStatus.SUCCESSFUL)
            transaction.status = PaymentStatus.SUCCESSFUL
            if payment_id:
                transaction.gateway_payment_id = payment_id
            if signature:
                transaction.gateway_signature = signature
            transaction.gateway_response = merge_gateway_response(
                transaction.gateway_response,
                raw_payload=raw_payload,
                extra={
                    "payment_link_id": payment_link_id,
                    "order_id": order_id,
                    "payment_id": payment_id,
                    "booking_ref": resolved_booking_ref,
                    "channel": channel or extracted_ids.get("channel"),
                    "link_status": "paid",
                },
            )
            transaction.updated_at = datetime.now(timezone.utc)

        # Update Booking
        if booking:
            booking.status = BookingStatus.CONFIRMED
            booking.updated_at = datetime.now(timezone.utc)
            b_meta = dict(booking.metadata_json or {})
            b_meta["payment_status"] = "PAID"
            b_meta["payment_gateway"] = gateway_provider
            b_meta["order_id"] = order_id
            b_meta["payment_id"] = payment_id
            if gateway_provider == "RAZORPAY":
                b_meta["razorpay_order_id"] = order_id
                b_meta["razorpay_payment_id"] = payment_id
            elif gateway_provider == "ICICI":
                b_meta["icici_order_id"] = order_id
                b_meta["icici_payment_id"] = payment_id
            if transaction:
                b_meta["transaction_id"] = transaction.transaction_ref
            b_meta["paid_at"] = datetime.now(timezone.utc).isoformat()
            b_meta["channel"] = channel or b_meta.get("channel") or "Web Checkout"
            booking.metadata_json = b_meta

            if transaction and booking.booking_ref:
                if (transaction.entity_id or "") != booking.booking_ref:
                    logger.info(
                        "[PaymentService] Normalizing payment entity_id to booking_ref=%s",
                        booking.booking_ref,
                    )
                transaction.entity_id = booking.booking_ref
                transaction.entity_type = transaction.entity_type or "AIRPORT_BOOKING"
        elif airport_booking:
            airport_booking.status = "CONFIRMED"
            airport_booking.updated_at = datetime.now(timezone.utc)
            if transaction and airport_booking.booking_reference:
                transaction.entity_id = airport_booking.booking_reference
                transaction.entity_type = transaction.entity_type or "AIRPORT_BOOKING"

        # Auto-generate Tax Invoice DB row
        invoice_id_to_fulfill: Optional[str] = None
        if transaction and booking:
            try:
                inv = cls.generate_invoice(
                    db,
                    transaction=transaction,
                    customer_name=booking.passenger_name or "Guest",
                    customer_email=booking.passenger_email or "",
                )
                if inv and getattr(inv, "id", None):
                    invoice_id_to_fulfill = str(inv.id)
            except Exception as inv_err:
                logger.error(f"[PaymentService] Tax Invoice row creation failed: {inv_err}")

        # Audit Log
        try:
            AdminService.log_audit_action(
                db,
                actor_email=booking.passenger_email if booking else "system@shafsky.com",
                action="PAYMENT_VERIFIED",
                resource_type="PAYMENT",
                resource_id=str(transaction.id) if transaction else (str(booking.id) if booking else ""),
                details={
                    "order_id": order_id,
                    "payment_id": payment_id,
                    "booking_ref": resolved_booking_ref,
                    "event": event_name,
                    "amount": float(booking.total_amount) if booking else (transaction.amount if transaction else 0.0)
                }
            )
        except Exception as audit_err:
            logger.warning(f"[PaymentService] Audit logging notice: {audit_err}")

        # Timeline Entry
        try:
            TimelineService.add_entry(
                db,
                entity_type="AIRPORT_BOOKING",
                entity_id=resolved_booking_ref or "PAYMENT",
                event_type="PAYMENT_SUCCESSFUL",
                title=f"Payment Successful ({resolved_booking_ref})",
                details={
                    "amount": float(booking.total_amount) if booking else (transaction.amount if transaction else 0.0),
                    "payment_id": payment_id,
                    "order_id": order_id,
                    "event": event_name
                }
            )
        except Exception as time_err:
            logger.warning(f"[PaymentService] Timeline logging notice: {time_err}")

        # Commit DB changes before triggering external notifications
        try:
            db.commit()
        except Exception:
            logger.exception("[PaymentService] Payment confirmation commit failed")
            try:
                db.rollback()
            except Exception:
                pass
            return {
                "success": False,
                "status": "COMMIT_FAILED",
                "reason": "Could not persist payment confirmation.",
                "booking_ref": resolved_booking_ref,
            }
        if transaction:
            db.refresh(transaction)
        if booking:
            db.refresh(booking)

        dispatch_payment_fulfillment_side_effects(db, booking, invoice_id_to_fulfill)
        sync_whatsapp_conversation_on_payment(db, resolved_booking_ref, booking)

        return {
            "success": True,
            "status": "CONFIRMED",
            "booking_ref": resolved_booking_ref,
            "payment_id": payment_id
        }

    # 3b. Handle payment.authorized
    elif event_name in ["payment.authorized"]:
        return handle_payment_authorized(
            db, booking, transaction, resolved_booking_ref, order_id, payment_id, channel, raw_payload
        )

    # 3c. Payment Link expired / cancelled
    elif event_name in ["payment_link.expired", "payment_link.cancelled"]:
        return handle_payment_link_expired_cancelled(
            db, booking, transaction, resolved_booking_ref, event_name, raw_payload
        )

    # 4. Handle Failed Payment Events
    elif event_name in ["payment.failed", "PAYMENT_FAILED"]:
        return handle_payment_failed(
            db, booking, transaction, resolved_booking_ref, order_id, payment_id, event_name, raw_payload
        )

    # 5. Handle Refund Events
    elif event_name in ["refund.created", "refund.processed", "payment.refunded", "PAYMENT_REFUNDED"]:
        return handle_payment_refund(
            db, booking, transaction, resolved_booking_ref, order_id, payment_id, event_name, raw_payload
        )

    return {
        "success": True,
        "status": "IGNORED",
        "event": event_name,
        "booking_ref": resolved_booking_ref
    }
