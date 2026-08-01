"""
SQLAlchemy ORM Models for Air Ticketing Domain Foundation.
Defines AirTicketBooking and AirTicketPassenger tables.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from sqlalchemy import String, DateTime, Enum, ForeignKey, Numeric, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class AirTicketStatus(str, PyEnum):
    NEW_BOOKING = "NEW_BOOKING"
    UNDER_REVIEW = "UNDER_REVIEW"
    WAITING_FOR_CUSTOMER = "WAITING_FOR_CUSTOMER"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_VERIFIED = "PAYMENT_VERIFIED"
    TICKET_ISSUED = "TICKET_ISSUED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class AirTicketPassengerType(str, PyEnum):
    ADULT = "ADULT"
    CHILD = "CHILD"
    INFANT = "INFANT"


class AirTicketBooking(Base):
    __tablename__ = "air_ticket_bookings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_ref: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    pnr_code: Mapped[str] = mapped_column(String(20), index=True, nullable=True)

    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    contact_name: Mapped[str] = mapped_column(String(120), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(40), nullable=False)

    airline_name: Mapped[str] = mapped_column(String(100), nullable=False)
    flight_number: Mapped[str] = mapped_column(String(20), nullable=False)
    cabin_class: Mapped[str] = mapped_column(String(50), default="ECONOMY", nullable=False)
    origin_iata: Mapped[str] = mapped_column(String(10), nullable=False)
    destination_iata: Mapped[str] = mapped_column(String(10), nullable=False)

    departure_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    arrival_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    passenger_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    base_fare: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0, nullable=False)
    taxes_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0, nullable=False)
    total_fare: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)

    status: Mapped[AirTicketStatus] = mapped_column(
        Enum(AirTicketStatus, native_enum=False),
        default=AirTicketStatus.NEW_BOOKING,
        nullable=False,
        index=True
    )
    notes: Mapped[str] = mapped_column(Text, nullable=True)

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

    passengers: Mapped[list["AirTicketPassenger"]] = relationship(
        "AirTicketPassenger",
        back_populates="ticket_booking",
        cascade="all, delete-orphan"
    )


class AirTicketPassenger(Base):
    __tablename__ = "air_ticket_passengers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("air_ticket_bookings.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    passenger_type: Mapped[AirTicketPassengerType] = mapped_column(
        Enum(AirTicketPassengerType, native_enum=False),
        default=AirTicketPassengerType.ADULT,
        nullable=False
    )
    title: Mapped[str] = mapped_column(String(10), default="MR", nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    dob: Mapped[str] = mapped_column(String(20), nullable=True)
    gender: Mapped[str] = mapped_column(String(10), nullable=True)
    nationality: Mapped[str] = mapped_column(String(50), nullable=True)
    passport_number: Mapped[str] = mapped_column(String(50), nullable=True)
    e_ticket_number: Mapped[str] = mapped_column(String(50), nullable=True)
    seat_number: Mapped[str] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    ticket_booking: Mapped[AirTicketBooking] = relationship(
        "AirTicketBooking",
        back_populates="passengers"
    )
