"""
Operations & Communication Engine — Database Models (Phase 6).

Tables:
- OperationsQueue: Tracks confirmed bookings through the 7-stage operations workflow.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String, Boolean, DateTime, Integer, Text, JSON, Index
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class OperationsQueue(Base):
    __tablename__ = "operations_queue"
    __table_args__ = (
        Index("ix_operations_queue_ref", "booking_reference", unique=True),
        Index("ix_operations_queue_status", "status"),
        Index("ix_operations_queue_airport", "airport_code"),
        Index("ix_operations_queue_staff", "assigned_staff_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_reference: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    airport_code: Mapped[str] = mapped_column(String(3), nullable=False)
    journey_type: Mapped[str] = mapped_column(String(20), nullable=False, default="ARRIVAL")
    service_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    service_time: Mapped[str] = mapped_column(String(5), nullable=False, default="12:00")  # HH:MM

    # Workflow states: NEW, ASSIGNED, IN_PROGRESS, CUSTOMER_CONTACTED, READY, COMPLETED, CANCELLED
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="NEW")

    # Duty officer assignment
    assigned_staff_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    assigned_staff_name: Mapped[str] = mapped_column(String(150), nullable=True)

    # Customer details
    customer_name: Mapped[str] = mapped_column(String(150), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(50), nullable=False)
    customer_email: Mapped[str] = mapped_column(String(150), nullable=False)
    guest_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    flight_number: Mapped[str] = mapped_column(String(30), nullable=True)

    # Selected service items snapshot (JSON)
    selected_services: Mapped[dict] = mapped_column(JSON, default=list, nullable=False)
    special_requests: Mapped[str] = mapped_column(Text, nullable=True)

    # Notification audit tracking
    email_notification_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    whatsapp_notification_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<OperationsQueue {self.booking_reference} ({self.status})>"
