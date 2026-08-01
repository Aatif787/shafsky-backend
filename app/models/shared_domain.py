"""
Shared Domain Models for Phase B.5 — Reusable Services.

Polymorphic entity strategy: all models reference entities via
entity_type (str) + entity_id (UUID) — zero FK to business tables.

Models:
- Assignment / AssignmentHistory
- TimelineEntry
- Note / NoteRevision
- Attachment
- SLADefinition / SLAInstance
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String, Boolean, DateTime, Integer, BigInteger, Text, JSON, Index,
    Numeric, ForeignKey
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


# ─────────────────────────────────────────────
# Assignment Service Models
# ─────────────────────────────────────────────

class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        Index("ix_assignments_entity", "entity_type", "entity_id"),
        Index("ix_assignments_staff_status", "staff_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    staff_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)
    assigned_by: Mapped[str] = mapped_column(String, nullable=True)
    role_type: Mapped[str] = mapped_column(String, nullable=False, default="GENERAL")
    status: Mapped[str] = mapped_column(String, default="ASSIGNED", nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    history = relationship("AssignmentHistory", back_populates="assignment", cascade="all, delete-orphan")


class AssignmentHistory(Base):
    __tablename__ = "assignment_history"
    __table_args__ = (
        Index("ix_assignment_history_assignment", "assignment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assignments.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    from_status: Mapped[str] = mapped_column(String, nullable=True)
    to_status: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    assignment = relationship("Assignment", back_populates="history")


# ─────────────────────────────────────────────
# Timeline Service Model
# ─────────────────────────────────────────────

class TimelineEntry(Base):
    __tablename__ = "timeline_entries"
    __table_args__ = (
        Index("ix_timeline_entity", "entity_type", "entity_id"),
        Index("ix_timeline_entity_ts", "entity_type", "entity_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=True)
    actor_role: Mapped[str] = mapped_column(String, nullable=True)
    reference_type: Mapped[str] = mapped_column(String, nullable=True)
    reference_id: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc)
    )


# ─────────────────────────────────────────────
# Notes Service Models
# ─────────────────────────────────────────────

class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        Index("ix_notes_entity", "entity_type", "entity_id"),
        Index("ix_notes_visibility", "entity_type", "entity_id", "visibility"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(String, default="INTERNAL", nullable=False)
    author_id: Mapped[str] = mapped_column(String, nullable=True)
    mentions: Mapped[dict] = mapped_column(JSON, default=list, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_by: Mapped[str] = mapped_column(String, nullable=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    revisions = relationship("NoteRevision", back_populates="note", cascade="all, delete-orphan")


class NoteRevision(Base):
    __tablename__ = "note_revisions"
    __table_args__ = (
        Index("ix_note_revisions_note", "note_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notes.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    edited_by: Mapped[str] = mapped_column(String, nullable=True)
    revision_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    note = relationship("Note", back_populates="revisions")


# ─────────────────────────────────────────────
# Attachment Service Model
# ─────────────────────────────────────────────

class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_entity", "entity_type", "entity_id"),
        Index("ix_attachments_category", "entity_type", "category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String, default="GENERAL", nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String, nullable=True)
    access_level: Mapped[str] = mapped_column(String, default="STAFF", nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_by: Mapped[str] = mapped_column(String, nullable=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc)
    )


# ─────────────────────────────────────────────
# SLA Service Models
# ─────────────────────────────────────────────

class SLADefinition(Base):
    __tablename__ = "sla_definitions"
    __table_args__ = (
        Index("ix_sla_def_service_priority", "service_type", "priority", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    priority: Mapped[str] = mapped_column(String, default="NORMAL", nullable=False)
    response_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    resolution_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=480)
    escalation_rules: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SLAInstance(Base):
    __tablename__ = "sla_instances"
    __table_args__ = (
        Index("ix_sla_inst_entity", "entity_type", "entity_id"),
        Index("ix_sla_inst_status", "status"),
        Index("ix_sla_inst_deadline", "deadline_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sla_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sla_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String, default="ACTIVE", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    breached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    started_by: Mapped[str] = mapped_column(String, nullable=True)
    resolved_by: Mapped[str] = mapped_column(String, nullable=True)
    escalated_by: Mapped[str] = mapped_column(String, nullable=True)
    escalation_reason: Mapped[str] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    definition = relationship("SLADefinition")
