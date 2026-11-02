"""Admin Notification model for enterprise real-time operations alerts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AdminNotification(Base):
    """
    Persistent notification records for admin portal alerts.
    Guarantees idempotency via unique constraint on (booking_ref, event_type).
    """
    __tablename__ = "admin_notifications"
    __table_args__ = (
        UniqueConstraint("booking_ref", "event_type", name="uq_admin_notifications_ref_event"),
        Index("ix_admin_notifications_created_at", "created_at"),
        Index("ix_admin_notifications_is_read", "is_read"),
        Index("ix_admin_notifications_booking_ref", "booking_ref"),
        Index("ix_admin_notifications_unread_created", "is_read", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    booking_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    service_category: Mapped[str] = mapped_column(String(128), nullable=False)
    service_type: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="PENDING", nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), default="BOOKING_CREATED", nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str] = mapped_column(String(255), default="/admin/operations", nullable=False)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, default=dict, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    read_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "booking_id": str(self.booking_id) if self.booking_id else None,
            "booking_ref": self.booking_ref,
            "service_category": self.service_category,
            "service_type": self.service_type,
            "customer_name": self.customer_name,
            "customer_phone": self.customer_phone,
            "status": self.status,
            "event_type": self.event_type,
            "title": self.title,
            "message": self.message,
            "link": self.link,
            "metadata_json": self.metadata_json or {},
            "is_read": self.is_read,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "read_by_user_id": str(self.read_by_user_id) if self.read_by_user_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

