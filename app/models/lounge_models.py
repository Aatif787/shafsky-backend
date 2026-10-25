import uuid
from typing import Optional
from datetime import datetime, timezone
from enum import Enum as PyEnum
from sqlalchemy import (
    String, DateTime, Enum, Integer, Text, Index
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class LoungeEnquiryStatus(str, PyEnum):
    PENDING = "PENDING"
    CONTACTED = "CONTACTED"
    IN_REVIEW = "IN_REVIEW"
    QUOTE_SENT = "QUOTE_SENT"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class LoungeEnquiry(Base):
    __tablename__ = "lounge_enquiries"
    __table_args__ = (
        Index("ix_lounge_enq_ref", "enquiry_reference", unique=True),
        Index("ix_lounge_enq_email", "email_address"),
        Index("ix_lounge_enq_status", "status"),
        Index("ix_lounge_enq_airport", "airport"),
        Index("ix_lounge_enq_created_at", "created_at"),
    )

    # Unique Enquiry ID (UUID Primary Key)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Human-readable Unique Enquiry Reference (e.g. SHAF-LNG-XXXXXX)
    enquiry_reference: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)

    # Service Details
    airport: Mapped[str] = mapped_column(String, index=True, nullable=False)
    lounge_category: Mapped[str] = mapped_column(String, default="Domestic", nullable=False)
    lounge_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    travel_date: Mapped[str] = mapped_column(String, nullable=False)
    preferred_time: Mapped[str] = mapped_column(String, nullable=False)
    number_of_passengers: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    flight_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Customer / Passenger Details
    full_name: Mapped[str] = mapped_column(String, index=True, nullable=False)
    mobile_number: Mapped[str] = mapped_column(String, nullable=False)
    email_address: Mapped[str] = mapped_column(String, index=True, nullable=False)
    whatsapp_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    additional_requirements: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Lifecycle & Operational Management
    status: Mapped[LoungeEnquiryStatus] = mapped_column(
        Enum(LoungeEnquiryStatus), default=LoungeEnquiryStatus.PENDING, nullable=False
    )
    assigned_staff_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    assigned_staff_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    internal_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    client_ip: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
