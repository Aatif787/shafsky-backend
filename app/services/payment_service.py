"""
Payment & Invoicing Service Layer.
Encapsulates transaction initiation, invoice generation, webhook processing,
refund handling, timeline tracking, and audit logging.
"""

import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.payment import PaymentTransaction, Invoice, Refund, PaymentStatus, InvoiceStatus, PaymentMethod
from app.schemas.payment import PaymentInitiateRequest, RefundRequest, WebhookPayload
from app.providers.base import PaymentProvider, MockPaymentProvider
from app.services.timeline_service import TimelineService
from app.services.admin_service import AdminService


class PaymentService:
    """Core payment domain service."""

    @classmethod
    def initiate_payment(
        cls,
        db: Session,
        payload: PaymentInitiateRequest,
        provider: Optional[PaymentProvider] = None
    ) -> PaymentTransaction:
        """Initiates a payment transaction and registers intent with provider."""
        provider = provider or MockPaymentProvider()
        ref = f"PAY-{uuid.uuid4().hex[:8].upper()}"

        # Register intent with payment provider
        intent = provider.create_payment_intent(
            amount=payload.amount,
            currency=payload.currency,
            reference_id=ref,
            metadata=payload.metadata or {}
        )

        transaction = PaymentTransaction(
            transaction_ref=ref,
            entity_type=payload.entity_type.strip().upper(),
            entity_id=str(payload.entity_id),
            customer_id=payload.customer_id,
            amount=payload.amount,
            currency=payload.currency,
            payment_method=payload.payment_method,
            status=PaymentStatus.PENDING,
            gateway_provider="MOCK_PAYMENT",
            gateway_payment_id=intent.get("payment_id"),
            gateway_response=intent
        )
        db.add(transaction)
        db.flush()

        # Log timeline event
        TimelineService.add_entry(
            db,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            event_type="PAYMENT_INITIATED",
            title=f"Payment Initiated ({ref})",
            details={
                "amount": payload.amount,
                "currency": payload.currency,
                "transactionRef": ref,
                "gatewayPaymentId": intent.get("payment_id")
            }
        )

        # Audit log
        AdminService.log_audit_action(
            db,
            actor_email=payload.customer_email,
            action="PAYMENT_INITIATED",
            resource_type="PAYMENT",
            resource_id=str(transaction.id),
            details={"transactionRef": ref, "amount": payload.amount}
        )

        db.commit()
        db.refresh(transaction)
        return transaction

    @classmethod
    def process_webhook(
        cls,
        db: Session,
        payload: WebhookPayload,
        provider: Optional[PaymentProvider] = None
    ) -> PaymentTransaction:
        """Handles incoming payment gateway webhooks and updates transaction state."""
        provider = provider or MockPaymentProvider()
        
        transaction = db.scalar(
            select(PaymentTransaction).where(PaymentTransaction.transaction_ref == payload.transaction_ref)
        )
        if not transaction:
            raise ValueError(f"Transaction with reference '{payload.transaction_ref}' not found.")

        # Update status based on event
        if payload.event_type in ["payment.succeeded", "PAYMENT_SUCCESS"]:
            transaction.status = PaymentStatus.SUCCESSFUL
            transaction.gateway_payment_id = payload.gateway_payment_id
            
            # Auto-generate Invoice
            cls.generate_invoice(
                db,
                transaction=transaction,
                customer_name="Valued Customer",
                customer_email="customer@shafsky.com"
            )

            # Timeline event
            TimelineService.add_entry(
                db,
                entity_type=transaction.entity_type,
                entity_id=transaction.entity_id,
                event_type="PAYMENT_VERIFIED",
                title=f"Payment Verified ({transaction.transaction_ref})",
                details={"amount": transaction.amount, "currency": transaction.currency}
            )

        elif payload.event_type in ["payment.failed", "PAYMENT_FAILED"]:
            transaction.status = PaymentStatus.FAILED

        transaction.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(transaction)
        return transaction

    @classmethod
    def generate_invoice(
        cls,
        db: Session,
        transaction: PaymentTransaction,
        customer_name: str,
        customer_email: str
    ) -> Invoice:
        """Generates a tax invoice for a transaction."""
        invoice_num = f"INV-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        
        tax_rate = 0.18  # 18% GST/Tax standard
        subtotal = round(transaction.amount / (1 + tax_rate), 2)
        tax_amt = round(transaction.amount - subtotal, 2)

        invoice = Invoice(
            invoice_number=invoice_num,
            transaction_id=transaction.id,
            customer_name=customer_name,
            customer_email=customer_email,
            subtotal_amount=subtotal,
            tax_amount=tax_amt,
            total_amount=transaction.amount,
            currency=transaction.currency,
            status=InvoiceStatus.PAID if transaction.status == PaymentStatus.SUCCESSFUL else InvoiceStatus.ISSUED,
            paid_at=datetime.now(timezone.utc) if transaction.status == PaymentStatus.SUCCESSFUL else None
        )
        db.add(invoice)
        db.flush()
        return invoice

    @classmethod
    def process_refund(
        cls,
        db: Session,
        payload: RefundRequest,
        provider: Optional[PaymentProvider] = None
    ) -> Refund:
        """Processes a refund against a successful payment transaction."""
        provider = provider or MockPaymentProvider()

        try:
            tx_uuid = uuid.UUID(payload.transaction_id)
        except Exception:
            raise ValueError("Invalid transaction UUID format.")

        transaction = db.scalar(select(PaymentTransaction).where(PaymentTransaction.id == tx_uuid))
        if not transaction:
            raise ValueError(f"Transaction with ID '{payload.transaction_id}' not found.")

        ref_id = f"REF-{uuid.uuid4().hex[:8].upper()}"

        # Execute refund with provider
        gateway_res = provider.process_refund(
            transaction_id=transaction.gateway_payment_id or str(transaction.id),
            amount=payload.amount,
            reason=payload.reason or "Customer request"
        )

        refund = Refund(
            refund_ref=ref_id,
            transaction_id=transaction.id,
            amount=payload.amount,
            currency=transaction.currency,
            reason=payload.reason,
            status=PaymentStatus.REFUNDED,
            gateway_refund_id=gateway_res.get("refund_id")
        )
        db.add(refund)

        # Update parent transaction status
        transaction.status = PaymentStatus.REFUNDED
        transaction.updated_at = datetime.now(timezone.utc)

        # Timeline event
        TimelineService.add_entry(
            db,
            entity_type=transaction.entity_type,
            entity_id=transaction.entity_id,
            event_type="PAYMENT_REFUNDED",
            title=f"Refund Issued ({ref_id})",
            details={"refundAmount": payload.amount, "refundRef": ref_id}
        )

        db.commit()
        db.refresh(refund)
        return refund
