"""
Timeline Service — Phase B.5 Shared Domain Services.

Unified chronological activity feed per entity.
Supports comments, attachment cross-references, and workflow event merging.
"""

import uuid
import logging
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc
from app.models.shared_domain import TimelineEntry

logger = logging.getLogger("shafsky.services.timeline")


class TimelineService:

    @classmethod
    def add_entry(
        cls,
        db: Session,
        entity_type: str,
        entity_id: str,
        event_type: str,
        title: str,
        details: Optional[Dict[str, Any]] = None,
        actor_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
    ) -> TimelineEntry:
        """Creates a timeline entry for an entity."""
        entry = TimelineEntry(
            entity_type=entity_type.strip().upper(),
            entity_id=entity_id,
            event_type=event_type.strip().upper(),
            title=title,
            details=details or {},
            actor_id=actor_id,
            actor_role=actor_role,
            reference_type=reference_type,
            reference_id=reference_id,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        logger.info(f"Timeline entry '{event_type}' added for {entity_type}:{entity_id}")
        return entry

    @classmethod
    def get_timeline(
        cls,
        db: Session,
        entity_type: str,
        entity_id: str,
        limit: int = 50,
        offset: int = 0,
        sort: str = "desc",
    ) -> Dict[str, Any]:
        """Returns paginated chronological timeline for an entity."""
        et = entity_type.strip().upper()
        query = db.query(TimelineEntry).filter(
            TimelineEntry.entity_type == et,
            TimelineEntry.entity_id == entity_id,
        )
        total = query.count()

        order = desc(TimelineEntry.created_at) if sort.lower() == "desc" else asc(TimelineEntry.created_at)
        entries = query.order_by(order).offset(offset).limit(limit).all()

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "data": entries,
        }

    @classmethod
    def add_comment(
        cls,
        db: Session,
        entity_type: str,
        entity_id: str,
        actor_id: str,
        content: str,
        actor_role: Optional[str] = None,
    ) -> TimelineEntry:
        """Adds a comment as a timeline entry."""
        return cls.add_entry(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type="COMMENT",
            title="Comment added",
            details={"content": content},
            actor_id=actor_id,
            actor_role=actor_role,
        )

    @classmethod
    def add_attachment_entry(
        cls,
        db: Session,
        entity_type: str,
        entity_id: str,
        attachment_id: str,
        actor_id: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> TimelineEntry:
        """Adds an attachment upload reference as a timeline entry."""
        return cls.add_entry(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type="ATTACHMENT_UPLOADED",
            title=f"File uploaded: {filename or 'unknown'}",
            details={"attachment_id": attachment_id, "filename": filename},
            actor_id=actor_id,
            reference_type="ATTACHMENT",
            reference_id=attachment_id,
        )

    @classmethod
    def merge_workflow_events(
        cls,
        db: Session,
        entity_type: str,
        entity_id: str,
        workflow_instance_id: uuid.UUID,
    ) -> int:
        """Imports workflow events into the entity timeline. Returns count of entries created."""
        from app.models.system_events import WorkflowEventRecord

        events = (
            db.query(WorkflowEventRecord)
            .filter(WorkflowEventRecord.workflow_instance_id == workflow_instance_id)
            .order_by(WorkflowEventRecord.sequence_number.asc())
            .all()
        )

        count = 0
        for evt in events:
            # Avoid duplicates by checking reference_id
            existing = db.query(TimelineEntry).filter(
                TimelineEntry.entity_type == entity_type.strip().upper(),
                TimelineEntry.entity_id == entity_id,
                TimelineEntry.reference_type == "WORKFLOW_EVENT",
                TimelineEntry.reference_id == str(evt.id),
            ).first()

            if existing:
                continue

            entry = TimelineEntry(
                entity_type=entity_type.strip().upper(),
                entity_id=entity_id,
                event_type=f"WORKFLOW_{evt.event_type}",
                title=f"Workflow: {evt.event_type}",
                details={
                    "previous_state": evt.previous_state,
                    "current_state": evt.current_state,
                    "action": evt.action,
                    "service_type": evt.service_type,
                },
                actor_id=evt.actor_id,
                actor_role=evt.actor_role,
                reference_type="WORKFLOW_EVENT",
                reference_id=str(evt.id),
            )
            db.add(entry)
            count += 1

        if count > 0:
            db.commit()
            logger.info(f"Merged {count} workflow events into timeline for {entity_type}:{entity_id}")

        return count
