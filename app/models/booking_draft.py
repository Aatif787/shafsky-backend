"""Persistent booking drafts (incomplete bookings / cart).

A draft is deliberately separate from `bookings`: early-step data must not create
a bookable/paid record, and removing a draft must never touch a real booking.
Guest access is authorised by a random token whose SHA-256 is stored here; the
draft id alone is never sufficient.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


DRAFT_STATUSES = (
    "draft",
    "in_progress",
    "pending_payment",
    "completed",
    "expired",
    "cancelled",
)

OPEN_DRAFT_STATUSES = ("draft", "in_progress", "pending_payment")


class BookingDraft(Base):
    __tablename__ = "booking_drafts"
    __table_args__ = (
        Index("ix_booking_drafts_user_status", "user_id", "status", "updated_at"),
        Index("ix_booking_drafts_guest_status", "guest_id", "status", "updated_at"),
        Index("ix_booking_drafts_client_owner", "client_key", "user_id"),
        Index("ix_booking_drafts_client_guest", "client_key", "guest_id"),
        Index("ix_booking_drafts_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    draft_ref: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True, nullable=True)
    guest_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    client_key: Mapped[str] = mapped_column(String(80), nullable=False)

    service_key: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    service_name: Mapped[str] = mapped_column(String(150), nullable=False)
    current_step: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    step_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    completed_steps: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    form_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    resume_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False, index=True)
    booking_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
