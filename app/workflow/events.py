"""
Workflow Event Bus Facade.

Delegates workflow lifecycle events to WorkflowEventService for persistence,
Redis Pub/Sub broadcasting, correlation ID propagation, and event replay support.
"""

import uuid
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.services.event_service import WorkflowEventService
from app.models.system_events import WorkflowEventRecord


def publish_workflow_event(
    db: Session,
    event_type: str,
    instance_id: str,
    service_type: str,
    payload: Dict[str, Any],
    published_by: Optional[str] = "workflow_engine",
    previous_state: Optional[str] = None,
    current_state: Optional[str] = None,
    action: Optional[str] = None,
    version: int = 1,
    entity_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    correlation_id: Optional[str] = None
) -> WorkflowEventRecord:
    """
    Facade wrapper publishing events through WorkflowEventService.
    """
    inst_uuid = uuid.UUID(instance_id) if isinstance(instance_id, str) else instance_id
    ent_id = entity_id or payload.get("entity_id") or "UNKNOWN_ENTITY"
    curr_state = current_state or payload.get("to_state") or payload.get("initial_state") or "UNKNOWN_STATE"
    prev_state = previous_state or payload.get("from_state") or "NONE"
    act = action or payload.get("action")

    return WorkflowEventService.publish_workflow_event(
        db=db,
        event_type=event_type,
        workflow_instance_id=inst_uuid,
        workflow_definition_version=version,
        service_type=service_type,
        entity_id=ent_id,
        current_state=curr_state,
        previous_state=prev_state,
        action=act,
        actor_id=published_by,
        actor_role=actor_role or "SYSTEM",
        correlation_id=correlation_id,
        payload=payload,
        metadata={"published_by": published_by}
    )
