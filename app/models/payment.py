"""
SQLAlchemy ORM Models for Payment & Invoicing Foundation.
"""

import uuid
from enum import Enum as PyEnum
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import String, Float, DateTime, ForeignKey, Enum, JSON, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class PaymentStatus(str, PyEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESSFUL = "SUCCESSFUL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REFUND_PENDING = "REFUND_PENDING"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"


class PaymentStateMachine:
    """
    Authoritative state machine governing valid PaymentStatus transitions.
    Rejects illegal mutations (e.g. SUCCESSFUL -> PENDING, REFUNDED -> SUCCESSFUL).
    """

    ALLOWED_TRANSITIONS = {
        PaymentStatus.PENDING: {
            PaymentStatus.PROCESSING,
            PaymentStatus.SUCCESSFUL,
            PaymentStatus.FAILED,
            PaymentStatus.CANCELLED,
            PaymentStatus.EXPIRED,
        },
        PaymentStatus.PROCESSING: {
            PaymentStatus.SUCCESSFUL,
            PaymentStatus.FAILED,
            PaymentStatus.CANCELLED,
            PaymentStatus.EXPIRED,
        },
        PaymentStatus.SUCCESSFUL: {
            PaymentStatus.REFUND_PENDING,
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.REFUNDED,
        },
        PaymentStatus.REFUND_PENDING: {
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.REFUNDED,
            PaymentStatus.SUCCESSFUL,  # If refund request was cancelled/rejected
        },
        PaymentStatus.PARTIALLY_REFUNDED: {
            PaymentStatus.REFUNDED,
            PaymentStatus.PARTIALLY_REFUNDED,
        },
        PaymentStatus.FAILED: set(),  # Terminal for this transaction attempt
        PaymentStatus.CANCELLED: set(),  # Terminal
        PaymentStatus.EXPIRED: set(),  # Terminal
        PaymentStatus.REFUNDED: set(),  # Terminal
    }

    @classmethod
    def can_transition(cls, current: PaymentStatus, target: PaymentStatus) -> bool:
        if current == target:
            return True
        allowed = cls.ALLOWED_TRANSITIONS.get(current, set())
        return target in allowed

    @classmethod
    def validate_transition(cls, current: PaymentStatus, target: PaymentStatus) -> None:
        if not cls.can_transition(current, target):
            raise ValueError(
                f"Illegal payment status transition from '{current.value if hasattr(current, 'value') else current}' to '{target.value if hasattr(target, 'value') else target}'."
            )


class PaymentMethod(str, PyEnum):
    CREDIT_CARD = "CREDIT_CARD"
    DEBIT_CARD = "DEBIT_CARD"
    NET_BANKING = "NET_BANKING"
    UPI = "UPI"
    BANK_TRANSFER = "BANK_TRANSFER"
    WALLET = "WALLET"


class InvoiceStatus(str, PyEnum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    PAID = "PAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    CANCELLED = "CANCELLED"
    OVERDUE = "OVERDUE"


class PaymentTransaction(Base):
    """Stores payment transactions for bookings and services."""

    __tablename__ = "payment_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_ref: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)  # AIRPORT_BOOKING, TICKET_BOOKING
    entity_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    customer_id: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)
    
    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, native_enum=False),
        default=PaymentMethod.CREDIT_CARD,
        nullable=False
    )
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False),
        default=PaymentStatus.PENDING,
        nullable=False,
        index=True
    )
    
    gateway_provider: Mapped[str] = mapped_column(String(50), default="MOCK_PAYMENT", nullable=False)
    gateway_payment_id: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    gateway_signature: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    gateway_response: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    invoices: Mapped[List["Invoice"]] = relationship("Invoice", back_populates="transaction", cascade="all, delete-orphan")
    refunds: Mapped[List["Refund"]] = relationship("Refund", back_populates="transaction", cascade="all, delete-orphan")



class Invoice(Base):
    """Stores tax invoices generated for completed or pending transactions."""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_transactions.id", ondelete="CASCADE"), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_tax_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    subtotal_amount: Mapped[float] = mapped_column(Float, nullable=False)
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)
    
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, native_enum=False),
        default=InvoiceStatus.ISSUED,
        nullable=False
    )
    
    pdf_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    transaction: Mapped["PaymentTransaction"] = relationship("PaymentTransaction", back_populates="invoices")


class Refund(Base):
    """Stores refund records for payment transactions."""

    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    refund_ref: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_transactions.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False),
        default=PaymentStatus.PROCESSING,
        nullable=False
    )
    
    gateway_refund_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    transaction: Mapped["PaymentTransaction"] = relationship("PaymentTransaction", back_populates="refunds")


class PaymentWebhookEvent(Base):
    """
    Persistent log of received payment gateway webhooks enforcing event idempotency.
    Guarantees duplicate webhook deliveries are safely recognized and ignored across restarts.
    """
    __tablename__ = "payment_webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    gateway_provider: Mapped[str] = mapped_column(String(50), default="RAZORPAY", nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="PROCESSED", nullable=False)  # PROCESSED, DUPLICATE_IGNORED, FAILED
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

