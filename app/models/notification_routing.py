"""Database foundation for Super Admin WhatsApp notification routing.

Tables only. Dispatch still reads WHATSAPP_BOOKING_NOTIFY_PHONE_1..5 while
notification_settings.routing_mode stays "env".
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.database import Base

# One global settings row. The trailing "a" is required: SQLite gives a column
# declared as UUID numeric affinity, and an all-digit id would be stored as a float.
NOTIFICATION_SETTINGS_ROW_ID = uuid.UUID("00000000-0000-4000-8000-00000000000a")
ROUTING_MODE_ENV = "env"
ROUTING_MODE_DATABASE = "database"


class NotificationRecipient(Base):
    """A WhatsApp operations recipient. Phone is stored as digits only (E.164 without +)."""

    __tablename__ = "notification_recipients"
    __table_args__ = (
        UniqueConstraint("phone_number", name="uq_notification_recipients_phone_number"),
        Index("ix_notification_recipients_is_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    routes: Mapped[List["NotificationServiceRoute"]] = relationship(
        back_populates="recipient",
    )

    @validates("phone_number")
    def _normalize_phone(self, _key: str, value: str) -> str:
        from app.services.whatsapp_department_formatting import WhatsAppDepartmentFormattingMixin

        normalized = WhatsAppDepartmentFormattingMixin.normalize_phone_number(value)
        if not normalized:
            raise ValueError("phone_number must be a valid international number.")
        return normalized


class NotificationServiceRoute(Base):
    """Maps one backend service_category value to one recipient.

    Deleting a recipient is restricted while routes exist. Delivery history in
    notification_records is not referenced and is never cascade-deleted.
    """

    __tablename__ = "notification_service_routes"
    __table_args__ = (
        UniqueConstraint(
            "service_key",
            "recipient_id",
            name="uq_notification_service_routes_service_recipient",
        ),
        Index("ix_notification_service_routes_service_key", "service_key"),
        Index("ix_notification_service_routes_recipient_id", "recipient_id"),
        Index(
            "ix_notification_service_routes_service_active",
            "service_key",
            "is_active",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_key: Mapped[str] = mapped_column(String(128), nullable=False)
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notification_recipients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    recipient: Mapped[NotificationRecipient] = relationship(back_populates="routes")


class NotificationSettings(Base):
    """Single global routing switch. Default mode is env so existing phones stay in use."""

    __tablename__ = "notification_settings"
    __table_args__ = (
        CheckConstraint(
            "routing_mode IN ('env', 'database')",
            name="ck_notification_settings_routing_mode",
        ),
        CheckConstraint(
            "replace(cast(id as text), '-', '') = '0000000000004000800000000000000a'",
            name="ck_notification_settings_singleton",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=lambda: NOTIFICATION_SETTINGS_ROW_ID,
    )
    routing_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ROUTING_MODE_ENV,
        server_default=ROUTING_MODE_ENV,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
