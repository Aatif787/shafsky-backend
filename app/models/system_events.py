"""
System and Workflow Event Models for Persistent Event Sourcing and Auditability.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, JSON, BigInteger, ForeignKey, Index, Identity
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class WorkflowEventRecord(Base):
    __tablename__ = "workflow_event_records"
    __table_args__ = (
        Index("ix_wf_events_instance_seq", "workflow_instance_id", "sequence_number"),
        Index("ix_wf_events_service_seq", "service_type", "sequence_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sequence_number: Mapped[int] = mapped_column(BigInteger, Identity(start=1, cycle=False), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    workflow_instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="CASCADE"), index=True, nullable=False)
    workflow_definition_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    service_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=True)
    actor_role: Mapped[str] = mapped_column(String, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String, index=True, nullable=True)
    previous_state: Mapped[str] = mapped_column(String, nullable=True)
    current_state: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=True)
    event_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
