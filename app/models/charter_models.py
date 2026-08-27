import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from enum import Enum as PyEnum
from sqlalchemy import (
    String, Boolean, DateTime, Enum, Numeric, JSON, Integer, Text, Index
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class CharterRequestStatus(str, PyEnum):
    REQUESTED = "REQUESTED"
    CONTACTED = "CONTACTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    AIRCRAFT_SEARCH = "AIRCRAFT_SEARCH"
    OPTIONS_PREPARED = "OPTIONS_PREPARED"
    QUOTE_PREPARED = "QUOTE_PREPARED"
    QUOTE_SENT = "QUOTE_SENT"
    CUSTOMER_REVIEW = "CUSTOMER_REVIEW"
    CONFIRMED = "CONFIRMED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class PrivateCharterRequest(Base):
    __tablename__ = "private_charter_requests"
    __table_args__ = (
        Index("ix_charter_req_ref", "request_reference", unique=True),
        Index("ix_charter_req_email", "email"),
        Index("ix_charter_req_status", "status"),
        Index("ix_charter_req_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_reference: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    
    # Customer Details
    customer_name: Mapped[str] = mapped_column(String, nullable=False)
    country_code: Mapped[str] = mapped_column(String, default="+91", nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, index=True, nullable=False)
    company: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    preferred_contact_method: Mapped[str] = mapped_column(String, default="PHONE_WHATSAPP", nullable=False)

    # Journey Details
    trip_type: Mapped[str] = mapped_column(String, default="ONE_WAY", nullable=False)  # ONE_WAY, ROUND_TRIP, MULTI_CITY
    origin: Mapped[str] = mapped_column(String, nullable=False)
    destination: Mapped[str] = mapped_column(String, nullable=False)
    departure_date: Mapped[str] = mapped_column(String, nullable=False)
    departure_time: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    return_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    return_time: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    itinerary: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)

    # Passengers & Aircraft Preference
    passengers: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    aircraft_preference: Mapped[str] = mapped_column(String, default="NO_PREFERENCE", nullable=False)

    # Travel Requirements
    travel_requirements: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    special_requests: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Operational Lifecycle & Management
    status: Mapped[CharterRequestStatus] = mapped_column(
        Enum(CharterRequestStatus), default=CharterRequestStatus.REQUESTED, nullable=False
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
