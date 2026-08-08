"""
SQLAlchemy Database Models for WhatsApp Conversation State Engine & Webhook Idempotency.
"""

import uuid
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import (
    String, Boolean, DateTime, Integer, Numeric, Text, JSON, ForeignKey, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base, engine

try:
    Base.metadata.create_all(bind=engine)
except Exception as _e:
    pass


class WhatsAppConversation(Base):
    """
    Persistent state machine tracking individual customer WhatsApp booking sessions.
    """
    __tablename__ = "whatsapp_conversations"
    __table_args__ = (
        Index("ix_whatsapp_conversations_phone", "phone_number", unique=True),
        Index("ix_whatsapp_conversations_state", "current_state"),
        Index("ix_whatsapp_conversations_ref", "booking_ref"),
        Index("ix_whatsapp_conversations_rzp_order", "razorpay_order_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    
    # State tracking: START, CATEGORY_SELECTION, SERVICE_SELECTION, AIRPORT_SELECTION, AIRPORT_CONFIRMATION,
    # FLIGHT_INPUT, FLIGHT_CONFIRMATION, DATE_SELECTION, PASSENGER_COUNT, CUSTOMER_NAME, CUSTOMER_EMAIL,
    # CUSTOMER_PHONE, ADDITIONAL_REQUIREMENTS, BOOKING_REVIEW, PENDING_PAYMENT, PAYMENT_PROCESSING,
    # PAYMENT_SUCCESS, PAYMENT_FAILED, BOOKING_CONFIRMED, CANCELLED
    current_state: Mapped[str] = mapped_column(String(50), nullable=False, default="START")
    
    # Service Selection
    selected_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    selected_service_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    selected_service_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    requires_airport: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_flight: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_date: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_passenger_count: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Airport Resolution
    selected_airport_iata: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    selected_airport_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    selected_airport_city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    selected_airport_country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Flight Details
    flight_num: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    flight_details_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Booking Requirements
    booking_date: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    passenger_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    customer_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    customer_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    customer_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    additional_requirements: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Pricing & Order
    total_amount: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    booking_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    booking_ref: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Razorpay Integration
    razorpay_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    razorpay_payment_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    razorpay_payment_link_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    razorpay_payment_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payment_status: Mapped[str] = mapped_column(String(50), nullable=False, default="UNPAID")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    messages = relationship("WhatsAppMessage", back_populates="conversation", cascade="all, delete-orphan")


class WhatsAppMessage(Base):
    """
    Log of inbound and outbound WhatsApp messages for auditing and conversational context.
    """
    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        Index("ix_whatsapp_messages_conv", "conversation_id"),
        Index("ix_whatsapp_messages_meta_id", "message_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("whatsapp_conversations.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    direction: Mapped[str] = mapped_column(String(20), nullable=False, default="INBOUND")  # INBOUND, OUTBOUND
    message_type: Mapped[str] = mapped_column(String(50), nullable=False, default="text")  # text, button_reply, list_reply
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    conversation = relationship("WhatsAppConversation", back_populates="messages")


class WhatsAppWebhookEvent(Base):
    """
    Persistent log of received webhooks enforcing event idempotency.
    """
    __tablename__ = "whatsapp_webhook_events"
    __table_args__ = (
        Index("ix_whatsapp_webhook_events_eid", "event_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, default="message")
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

try:
    Base.metadata.create_all(bind=engine)
except Exception:
    pass
