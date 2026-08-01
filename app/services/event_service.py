"""
Workflow Event Service for Event Sourcing, Redis Pub/Sub, and Event Replay.

Standardized Event Payload Specification:
- event_id (UUID)
- event_type (WORKFLOW_CREATED, TRANSITION_STARTED, TRANSITION_COMPLETED, TRANSITION_REJECTED, WORKFLOW_COMPLETED, WORKFLOW_CANCELLED)
- workflow_instance_id (UUID)
- workflow_definition_version (int)
- service_type (str)
- entity_id (str)
- actor_id (str)
- actor_role (str)
- correlation_id (str)
- previous_state (str)
- current_state (str)
- action (str)
- timestamp (ISO UTC)
- metadata (dict)
"""

import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models.system_events import WorkflowEventRecord
from app.models.schema import SystemEvent
from app.core.redis import get_redis_client

logger = logging.getLogger("shafsky.services.event_service")


class WorkflowEventService:
    @classmethod
    def publish_workflow_event(
        cls,
        db: Session,
        event_type: str,
        workflow_instance_id: uuid.UUID,
        workflow_definition_version: int,
        service_type: str,
        entity_id: str,
        current_state: str,
        previous_state: Optional[str] = None,
        action: Optional[str] = None,
        actor_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        correlation_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> WorkflowEventRecord:
        """
        Persists an immutable workflow event record in PostgreSQL and broadcasts over Redis Pub/Sub.
        """
        now = datetime.now(timezone.utc)
        event_id = uuid.uuid4()
        corr_id = correlation_id or f"corr_{uuid.uuid4().hex[:12]}"
        payload_dict = payload or {}
        meta_dict = metadata or {}

        # 1. Check for Duplicate Prevention (Deduplication within same transaction)
        existing_dup = db.query(WorkflowEventRecord).filter(
            WorkflowEventRecord.workflow_instance_id == workflow_instance_id,
            WorkflowEventRecord.event_type == event_type,
            WorkflowEventRecord.correlation_id == corr_id,
            WorkflowEventRecord.action == action
        ).first()

        if existing_dup:
            logger.info(f"Duplicate event suppressed for instance {workflow_instance_id} (type={event_type})")
            return existing_dup

        # Calculate next sequence number
        max_seq = db.query(func.max(WorkflowEventRecord.sequence_number)).scalar() or 0
        next_seq = max_seq + 1

        # 2. Build Standardized Specification Payload
        spec_payload = {
            "event_id": str(event_id),
            "event_type": event_type,
            "workflow_instance_id": str(workflow_instance_id),
            "workflow_definition_version": workflow_definition_version,
            "service_type": service_type,
            "entity_id": entity_id,
            "actor_id": actor_id or "SYSTEM",
            "actor_role": actor_role or "SYSTEM",
            "correlation_id": corr_id,
            "previous_state": previous_state,
            "current_state": current_state,
            "action": action,
            "timestamp": now.isoformat(),
            "metadata": meta_dict
        }

        # 3. Persist WorkflowEventRecord
        rec = WorkflowEventRecord(
            id=event_id,
            event_type=event_type,
            workflow_instance_id=workflow_instance_id,
            workflow_definition_version=workflow_definition_version,
            service_type=service_type,
            entity_id=entity_id,
            actor_id=actor_id or "SYSTEM",
            actor_role=actor_role or "SYSTEM",
            correlation_id=corr_id,
            previous_state=previous_state,
            current_state=current_state,
            action=action,
            event_version=1,
            payload=payload_dict,
            metadata_json=spec_payload,
            created_at=now
        )
        db.add(rec)

        # 4. Persist SystemEvent row
        sys_event = SystemEvent(
            event_type=event_type,
            payload=spec_payload,
            published_by=actor_id or "workflow_event_service"
        )
        db.add(sys_event)
        db.flush()

        # 5. Broadcast to Redis Pub/Sub channel
        channel = f"workflow:events:{service_type}:{workflow_instance_id}"
        client = get_redis_client()
        if client is not None:
            try:
                client.publish(channel, json.dumps(spec_payload, default=str))
                logger.debug(f"Broadcasted '{event_type}' event to Redis channel '{channel}'")
            except Exception as err:
                logger.warning(f"Redis event broadcast failed for channel '{channel}': {err}")

        logger.info(f"Emitted workflow event '{event_type}' for instance {workflow_instance_id} (corr_id={corr_id})")
        return rec

    @classmethod
    def get_instance_events(cls, db: Session, instance_id: uuid.UUID) -> List[WorkflowEventRecord]:
        """Retrieves all events for a workflow instance ordered by sequence."""
        return db.query(WorkflowEventRecord).filter(
            WorkflowEventRecord.workflow_instance_id == instance_id
        ).order_by(WorkflowEventRecord.sequence_number.asc()).all()

    @classmethod
    def replay_events(
        cls,
        db: Session,
        instance_id: Optional[uuid.UUID] = None,
        service_type: Optional[str] = None,
        from_sequence: int = 0
    ) -> List[WorkflowEventRecord]:
        """
        Replays historical workflow events starting from sequence_number in strict creation order.
        """
        query = db.query(WorkflowEventRecord).filter(WorkflowEventRecord.sequence_number >= from_sequence)

        if instance_id:
            query = query.filter(WorkflowEventRecord.workflow_instance_id == instance_id)
        if service_type:
            query = query.filter(WorkflowEventRecord.service_type == service_type)

        return query.order_by(WorkflowEventRecord.sequence_number.asc()).all()
