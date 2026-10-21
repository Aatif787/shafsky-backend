"""
Invoicing and Refunds Service.
Encapsulates tax invoice row creation, internal webhook routing,
and gateway refund processing for Razorpay and ICICI.
"""

import uuid
import logging
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.payment import (
    PaymentTransaction, Invoice, Refund, PaymentStatus, InvoiceStatus, PaymentStateMachine
)
from app.schemas.payment import RefundRequest, WebhookPayload
from app.providers.base import PaymentProvider
from app.services.timeline_service import TimelineService
from app.services.admin_service import AdminService

logger = logging.getLogger(__name__)


def process_webhook(
    cls,
    db: Session,
    payload: WebhookPayload,
    provider: Optional[PaymentProvider] = None
) -> PaymentTransaction:
    """
    Internal webhook handler. Signature verification must already have passed.
    Success events go exclusively through handle_verified_payment (never mark SUCCESSFUL here alone).
    """
    _ = provider

    transaction = db.scalar(
        select(PaymentTransaction).where(PaymentTransaction.transaction_ref == payload.transaction_ref)
    )
    if not transaction:
        raise ValueError(f"Transaction with reference '{payload.transaction_ref}' not found.")

    if transaction.status == PaymentStatus.SUCCESSFUL:
        logger.info(
            "Idempotent webhook delivery received for transaction %s; skipping duplicate processing.",
            payload.transaction_ref,
        )
        return transaction

    if payload.event_type in ["payment.succeeded", "PAYMENT_SUCCESS", "payment.captured"]:
        if transaction.status in [PaymentStatus.FAILED, PaymentStatus.REFUNDED, PaymentStatus.EXPIRED]:
            raise ValueError(
                f"Invalid payment state transition from '{transaction.status.value}' to 'SUCCESSFUL'."
            )
        # Pending rows usually store the order_id; the webhook supplies the payment_id.
        existing_gw = (transaction.gateway_payment_id or "").strip()
        order_id = existing_gw if existing_gw.startswith("order_") else None
        payment_id = (payload.gateway_payment_id or "").strip() or None
        if payment_id and payment_id.startswith("order_") and not order_id:
            order_id = payment_id
            payment_id = None
        result = cls.handle_verified_payment(
            db,
            event_name="PAYMENT_SUCCESS",
            gateway_provider=(payload.provider or transaction.gateway_provider or "RAZORPAY").upper(),
            order_id=order_id,
            payment_id=payment_id,
            booking_ref=transaction.entity_id,
            signature=payload.signature,
            raw_payload=payload.data if isinstance(payload.data, dict) else None,
            amount=float(transaction.amount) if transaction.amount is not None else None,
            currency=transaction.currency,
        )
        if not result.get("success"):
            raise ValueError(result.get("reason") or "Payment confirmation failed.")
        refreshed = db.scalar(
            select(PaymentTransaction).where(PaymentTransaction.transaction_ref == payload.transaction_ref)
        )
        return refreshed or transaction

    if payload.event_type in ["payment.failed", "PAYMENT_FAILED"]:
        PaymentStateMachine.validate_transition(transaction.status, PaymentStatus.FAILED)
        transaction.status = PaymentStatus.FAILED
        transaction.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(transaction)
        return transaction

    raise ValueError(f"Unsupported webhook event_type '{payload.event_type}'.")


def generate_invoice(
    cls,
    db: Session,
    transaction: PaymentTransaction,
    customer_name: str,
    customer_email: str
) -> Optional[Invoice]:
    """Generates a tax invoice for a transaction with duplicate prevention."""
    if transaction.status != PaymentStatus.SUCCESSFUL:
        logger.warning(
            "[PaymentService] generate_invoice called for non-SUCCESSFUL transaction %s (status=%s); skipping.",
            transaction.transaction_ref,
            transaction.status.value if hasattr(transaction.status, "value") else transaction.status,
        )
        return None

    existing_invoice = db.scalar(select(Invoice).where(Invoice.transaction_id == transaction.id))
    if existing_invoice:
        return existing_invoice

    tax_rate = 0.18  # 18% GST/Tax standard
    subtotal = round(transaction.amount / (1 + tax_rate), 2)
    tax_amt = round(transaction.amount - subtotal, 2)
    is_paid = transaction.status == PaymentStatus.SUCCESSFUL

    for _ in range(5):
        invoice_num = f"INV-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        invoice = Invoice(
            invoice_number=invoice_num,
            transaction_id=transaction.id,
            customer_name=customer_name,
            customer_email=customer_email,
            subtotal_amount=subtotal,
            tax_amount=tax_amt,
            total_amount=transaction.amount,
            currency=transaction.currency,
            status=InvoiceStatus.PAID if is_paid else InvoiceStatus.ISSUED,
            paid_at=datetime.now(timezone.utc) if is_paid else None
        )
        try:
            with db.begin_nested():
                db.add(invoice)
                db.flush()
            return invoice
        except IntegrityError:
            logger.warning("[PaymentService] Invoice number collision; retrying allocation")
            continue

    logger.error("[PaymentService] Unable to allocate a unique invoice number without aborting payment")
    return None


def process_refund(
    cls,
    db: Session,
    payload: RefundRequest,
    actor_email: str = "admin@shafsky.com",
    provider: Optional[PaymentProvider] = None
) -> Refund:
    """
    Processes a production refund against a successful payment transaction.
    Routes to Razorpay or ICICI based on the transaction gateway_provider.
    Never simulates ICICI refunds.
    """
    from app.providers.razorpay_provider import razorpay_provider
    from app.providers.icici_provider import icici_provider, generate_merchant_txn_no
    from app.models.schema import Booking, BookingStatus

    _ = provider

    try:
        tx_uuid = uuid.UUID(payload.transaction_id)
    except Exception:
        raise ValueError("Invalid transaction UUID format.")

    # Row lock: serialize concurrent refunds so the balance check below cannot
    # race another in-flight refund (with_for_update is a no-op on SQLite).
    transaction = db.scalar(
        select(PaymentTransaction).where(PaymentTransaction.id == tx_uuid).with_for_update()
    )
    if not transaction:
        raise ValueError(f"Transaction with ID '{payload.transaction_id}' not found.")

    if transaction.status not in (PaymentStatus.SUCCESSFUL, PaymentStatus.PARTIALLY_REFUNDED):
        raise ValueError(
            f"Cannot refund transaction in '{transaction.status.value if hasattr(transaction.status, 'value') else transaction.status}' status. "
            "Only SUCCESSFUL or PARTIALLY_REFUNDED transactions can be refunded."
        )

    if payload.amount <= 0:
        raise ValueError("Refund amount must be greater than zero.")

    existing_refunds_total = sum(
        r.amount for r in transaction.refunds
        if r.status in (PaymentStatus.REFUNDED, PaymentStatus.SUCCESSFUL, PaymentStatus.PROCESSING)
    )
    available_for_refund = round(transaction.amount - existing_refunds_total, 2)

    if round(payload.amount, 2) > available_for_refund:
        raise ValueError(
            f"Refund amount (\u20b9{payload.amount}) exceeds available refundable balance (\u20b9{available_for_refund}). "
            f"Total paid: \u20b9{transaction.amount}, already refunded: \u20b9{existing_refunds_total}."
        )

    new_total_refunded = existing_refunds_total + payload.amount
    is_full_refund = round(new_total_refunded, 2) >= round(transaction.amount, 2)
    new_tx_status = PaymentStatus.REFUNDED if is_full_refund else PaymentStatus.PARTIALLY_REFUNDED
    PaymentStateMachine.validate_transition(transaction.status, new_tx_status)

    ref_id = f"REF-{uuid.uuid4().hex[:8].upper()}"
    gateway = (transaction.gateway_provider or "").strip().upper()
    reason = payload.reason or "Admin authorized refund"

    if gateway == "ICICI":
        if not icici_provider.is_configured():
            raise ValueError("ICICI payment gateway is not configured for refunds.")
        gw_resp = transaction.gateway_response if isinstance(transaction.gateway_response, dict) else {}
        original_txn = (
            str(gw_resp.get("txnID") or "")
            or str((gw_resp.get("last_status") or {}).get("txnID") or "")
            or str((gw_resp.get("icici_status") or {}).get("txnID") or "")
            or (transaction.gateway_payment_id or "")
        ).strip()
        if not original_txn:
            raise ValueError("ICICI original transaction id missing; cannot refund.")
        refund_merchant_txn = generate_merchant_txn_no("RF")
        gateway_res = icici_provider.refund(
            merchant_txn_no=refund_merchant_txn,
            original_txn_no=original_txn,
            amount=payload.amount,
            extra={"addlParam1": reason[:50]},
        )
        if not gateway_res.get("success"):
            raise ValueError(gateway_res.get("error") or "ICICI refund execution failed.")
        if gateway_res.get("simulated"):
            raise ValueError("ICICI refund simulation is not allowed.")
        gateway_refund_id = (
            str((gateway_res.get("data") or {}).get("txnID") or "")
            or str((gateway_res.get("raw") or {}).get("txnID") or "")
            or refund_merchant_txn
        )
    elif gateway == "RAZORPAY":
        gateway_payment_id = transaction.gateway_payment_id or str(transaction.id)
        if gateway_payment_id.startswith(("SF", "RF")) and not gateway_payment_id.startswith("pay_"):
            raise ValueError(
                f"Transaction gateway is RAZORPAY but payment id '{gateway_payment_id}' looks non-Razorpay."
            )
        gateway_res = razorpay_provider.create_refund(
            payment_id=gateway_payment_id,
            amount=payload.amount,
            reason=reason,
        )
        if not gateway_res.get("success"):
            raise ValueError(gateway_res.get("error", "Razorpay refund execution failed."))
        gateway_refund_id = gateway_res.get("refund_id")
    else:
        raise ValueError(f"Unsupported payment gateway for refunds: '{gateway or 'UNKNOWN'}'.")

    refund = Refund(
        refund_ref=ref_id,
        transaction_id=transaction.id,
        amount=payload.amount,
        currency=transaction.currency,
        reason=reason,
        status=PaymentStatus.REFUNDED,
        gateway_refund_id=gateway_refund_id,
        processed_at=datetime.now(timezone.utc)
    )
    db.add(refund)

    transaction.status = new_tx_status
    transaction.updated_at = datetime.now(timezone.utc)

    for inv in transaction.invoices:
        inv.status = InvoiceStatus.CANCELLED if is_full_refund else InvoiceStatus.PARTIALLY_PAID

    if is_full_refund:
        booking = None
        if transaction.entity_id:
            booking = db.scalar(select(Booking).where(Booking.booking_ref == transaction.entity_id))
            if not booking:
                try:
                    b_uuid = uuid.UUID(transaction.entity_id)
                    booking = db.scalar(select(Booking).where(Booking.id == b_uuid))
                except (ValueError, TypeError):
                    pass
        if not booking and transaction.transaction_ref:
            booking = db.scalar(select(Booking).where(Booking.booking_ref == transaction.transaction_ref))

        if booking:
            booking.status = BookingStatus.CANCELLED
            booking.updated_at = datetime.now(timezone.utc)
            logger.info(
                f"[PaymentService] Full refund processed for booking '{booking.booking_ref}'. "
                "Booking status set to CANCELLED."
            )

    TimelineService.add_entry(
        db,
        entity_type=transaction.entity_type,
        entity_id=transaction.entity_id,
        event_type="PAYMENT_REFUNDED",
        title=f"Refund Issued ({ref_id})",
        details={
            "refundAmount": payload.amount,
            "refundRef": ref_id,
            "isFullRefund": is_full_refund,
            "gatewayRefundId": gateway_refund_id,
            "gateway": gateway,
        }
    )

    AdminService.log_audit_action(
        db,
        actor_email=actor_email,
        action="PAYMENT_REFUNDED",
        resource_type="PAYMENT",
        resource_id=str(transaction.id),
        details={
            "refund_ref": ref_id,
            "amount": payload.amount,
            "gateway_refund_id": gateway_refund_id,
            "is_full_refund": is_full_refund,
            "gateway": gateway,
        }
    )

    db.commit()
    db.refresh(refund)
    return refund
