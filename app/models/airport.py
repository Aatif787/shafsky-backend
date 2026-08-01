"""
Airport Meet & Assist Database Models — Phase C.1.

Models:
- AirportBooking
- AirportPassenger
- AirportFlightDetail
- AirportServiceAddon
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String, Boolean, DateTime, Integer, Numeric, Text, ForeignKey, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class AirportBooking(Base):
    __tablename__ = "airport_bookings"
    __table_args__ = (
        Index("ix_airport_bookings_customer", "customer_id"),
        Index("ix_airport_bookings_status", "status"),
        Index("ix_airport_bookings_ref", "booking_reference", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_reference: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    service_package: Mapped[str] = mapped_column(String, nullable=False, default="STANDARD_MEET_GREET")
    status: Mapped[str] = mapped_column(String, nullable=False, default="DRAFT", index=True)
    total_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0.00)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    special_instructions: Mapped[str] = mapped_column(Text, nullable=True)
    workflow_instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    passengers = relationship("AirportPassenger", back_populates="booking", cascade="all, delete-orphan")
    flight_details = relationship("AirportFlightDetail", back_populates="booking", cascade="all, delete-orphan")
    addons = relationship("AirportServiceAddon", back_populates="booking", cascade="all, delete-orphan")


class AirportPassenger(Base):
    __tablename__ = "airport_passengers"
    __table_args__ = (
        Index("ix_airport_passengers_booking", "booking_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("airport_bookings.id", ondelete="CASCADE"),
        nullable=False
    )
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=True)
    dob: Mapped[str] = mapped_column(String(20), nullable=True)
    nationality: Mapped[str] = mapped_column(String(100), nullable=True)
    passport_number: Mapped[str] = mapped_column(String(50), nullable=True)
    contact_email: Mapped[str] = mapped_column(String, nullable=True)
    contact_phone: Mapped[str] = mapped_column(String, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    booking = relationship("AirportBooking", back_populates="passengers")


class AirportFlightDetail(Base):
    __tablename__ = "airport_flight_details"
    __table_args__ = (
        Index("ix_airport_flights_booking", "booking_id"),
        Index("ix_airport_flights_number", "flight_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("airport_bookings.id", ondelete="CASCADE"),
        nullable=False
    )
    airline: Mapped[str] = mapped_column(String, nullable=False)
    flight_number: Mapped[str] = mapped_column(String, index=True, nullable=False)
    departure_airport: Mapped[str] = mapped_column(String(5), nullable=False)
    arrival_airport: Mapped[str] = mapped_column(String(5), nullable=False)
    terminal: Mapped[str] = mapped_column(String(20), nullable=True)
    scheduled_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    flight_type: Mapped[str] = mapped_column(String(20), default="ARRIVAL", nullable=False)  # ARRIVAL, DEPARTURE, TRANSIT

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    booking = relationship("AirportBooking", back_populates="flight_details")


class AirportServiceAddon(Base):
    __tablename__ = "airport_service_addons"
    __table_args__ = (
        Index("ix_airport_addons_booking", "booking_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("airport_bookings.id", ondelete="CASCADE"),
        nullable=False
    )
    service_code: Mapped[str] = mapped_column(String, nullable=False)  # MEET_GREET, FAST_TRACK, BUGGY, LOUNGE, PORTER, VIP_ASSIST
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0.00)
    total_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0.00)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    booking = relationship("AirportBooking", back_populates="addons")
