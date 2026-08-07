"""
Journey Detection Engine — Database Models (Phase 1).

Tables:
- SupportedAirport: airports where Shafsky operates
- Service: catalog of airport concierge services
- AirportService: mapping of which services are available at which airports for which journey types
"""

import uuid
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import (
    String, Boolean, DateTime, Integer, Numeric, Text, JSON, ForeignKey, Index, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class SupportedAirport(Base):
    __tablename__ = "supported_airports"
    __table_args__ = (
        Index("ix_supported_airports_iata", "iata_code", unique=True),
        Index("ix_supported_airports_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    airport_name: Mapped[str] = mapped_column(String(255), nullable=False)
    iata_code: Mapped[str] = mapped_column(String(3), unique=True, nullable=False)
    icao_code: Mapped[str] = mapped_column(String(4), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    is_supported: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    airport_services = relationship("AirportService", back_populates="airport", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<SupportedAirport {self.iata_code} ({self.airport_name})>"


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        Index("ix_services_slug", "slug", unique=True),
        Index("ix_services_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    icon: Mapped[str] = mapped_column(String(50), nullable=True, default="Sparkles")
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    airport_services = relationship("AirportService", back_populates="service", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Service {self.slug} ({self.name})>"


class AirportService(Base):
    __tablename__ = "airport_services"
    __table_args__ = (
        UniqueConstraint("airport_id", "service_id", "journey_type", "flight_type", "terminal", name="uq_airport_service_journey_flight_terminal"),
        Index("ix_airport_services_airport", "airport_id"),
        Index("ix_airport_services_service", "service_id"),
        Index("ix_airport_services_journey_type", "journey_type"),
        Index("ix_airport_services_flight_type", "flight_type"),
        Index("ix_airport_services_terminal", "terminal"),
        Index("ix_airport_services_available", "is_available"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    airport_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("supported_airports.id", ondelete="CASCADE"),
        nullable=False,
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
    )
    journey_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ARRIVAL"
    )  # ARRIVAL, DEPARTURE, TRANSIT
    flight_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="DOMESTIC"
    )  # DOMESTIC, INTERNATIONAL, ALL, DOMESTIC_DOMESTIC, etc.
    terminal: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, default=None
    )  # e.g., "Terminal 1 & 2", "Terminal 3", or None
    short_description: Mapped[str] = mapped_column(String(255), nullable=True)
    features: Mapped[dict] = mapped_column(JSON, default=list, nullable=False)
    additional_benefits: Mapped[dict] = mapped_column(JSON, default=list, nullable=True)
    min_booking_notice_hours: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=2499.00)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    airport = relationship("SupportedAirport", back_populates="airport_services")
    service = relationship("Service", back_populates="airport_services")

    def __repr__(self) -> str:
        return f"<AirportService airport={self.airport_id} service={self.service_id} type={self.journey_type}>"
