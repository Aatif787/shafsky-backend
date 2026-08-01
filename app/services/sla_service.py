"""
SLA Service — Phase B.5 Shared Domain Services.

SLA definitions, deadline calculation, breach detection,
escalation triggers, and resolution tracking.
"""

import uuid
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.models.shared_domain import SLADefinition, SLAInstance

logger = logging.getLogger("shafsky.services.sla")


class SLAService:

    @classmethod
    def create_definition(
        cls,
        db: Session,
        service_type: str,
        priority: str = "NORMAL",
        response_time_minutes: int = 60,
        resolution_time_minutes: int = 480,
        escalation_rules: Optional[Dict[str, Any]] = None,
    ) -> SLADefinition:
        """Creates or updates an SLA definition for a service type + priority combination."""
        st = service_type.strip().upper()
        pr = priority.strip().upper()

        existing = (
            db.query(SLADefinition)
            .filter(SLADefinition.service_type == st, SLADefinition.priority == pr)
            .first()
        )

        if existing:
            existing.response_time_minutes = response_time_minutes
            existing.resolution_time_minutes = resolution_time_minutes
            existing.escalation_rules = escalation_rules or {}
            existing.is_active = True
            db.commit()
            db.refresh(existing)
            logger.info(f"SLA definition updated for {st}/{pr}")
            return existing

        sla_def = SLADefinition(
            service_type=st,
            priority=pr,
            response_time_minutes=response_time_minutes,
            resolution_time_minutes=resolution_time_minutes,
            escalation_rules=escalation_rules or {},
        )
        db.add(sla_def)
        db.commit()
        db.refresh(sla_def)
        logger.info(f"SLA definition created for {st}/{pr}: response={response_time_minutes}m, resolution={resolution_time_minutes}m")
        return sla_def

    @classmethod
    def start_sla(
        cls,
        db: Session,
        entity_type: str,
        entity_id: str,
        service_type: str,
        priority: str = "NORMAL",
        started_by: Optional[str] = None,
    ) -> SLAInstance:
        """Starts SLA tracking for an entity using matching SLA definition."""
        st = service_type.strip().upper()
        pr = priority.strip().upper()

        sla_def = (
            db.query(SLADefinition)
            .filter(
                SLADefinition.service_type == st,
                SLADefinition.priority == pr,
                SLADefinition.is_active == True,
            )
            .first()
        )

        if not sla_def:
            raise ValueError(f"No active SLA definition found for {st}/{pr}.")

        now = datetime.now(timezone.utc)
        deadline = now + timedelta(minutes=sla_def.resolution_time_minutes)

        instance = SLAInstance(
            sla_definition_id=sla_def.id,
            entity_type=entity_type.strip().upper(),
            entity_id=entity_id,
            status="ACTIVE",
            started_at=now,
            deadline_at=deadline,
            started_by=started_by,
        )
        db.add(instance)
        db.commit()
        db.refresh(instance)
        logger.info(f"SLA started for {entity_type}:{entity_id} — deadline: {deadline.isoformat()}")
        return instance

    @classmethod
    def check_breach(cls, db: Session, sla_instance_id: uuid.UUID) -> SLAInstance:
        """Checks if an SLA instance has breached its deadline."""
        instance = db.query(SLAInstance).filter(SLAInstance.id == sla_instance_id).first()
        if not instance:
            raise ValueError(f"SLA instance '{sla_instance_id}' not found.")

        if instance.status in ("RESOLVED", "BREACHED"):
            return instance

        now = datetime.now(timezone.utc)
        if now > instance.deadline_at:
            instance.status = "BREACHED"
            instance.breached_at = now
            db.commit()
            db.refresh(instance)
            logger.warning(f"SLA instance {sla_instance_id} BREACHED at {now.isoformat()}")

        return instance

    @classmethod
    def escalate(
        cls,
        db: Session,
        sla_instance_id: uuid.UUID,
        escalated_by: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> SLAInstance:
        """Marks an SLA instance as escalated."""
        instance = db.query(SLAInstance).filter(SLAInstance.id == sla_instance_id).first()
        if not instance:
            raise ValueError(f"SLA instance '{sla_instance_id}' not found.")

        instance.escalated_at = datetime.now(timezone.utc)
        instance.escalated_by = escalated_by
        instance.escalation_reason = reason
        if instance.status == "ACTIVE":
            instance.status = "ESCALATED"

        db.commit()
        db.refresh(instance)
        logger.info(f"SLA instance {sla_instance_id} escalated by {escalated_by}")
        return instance

    @classmethod
    def resolve_sla(
        cls,
        db: Session,
        sla_instance_id: uuid.UUID,
        resolved_by: Optional[str] = None,
    ) -> SLAInstance:
        """Resolves an active SLA instance."""
        instance = db.query(SLAInstance).filter(SLAInstance.id == sla_instance_id).first()
        if not instance:
            raise ValueError(f"SLA instance '{sla_instance_id}' not found.")

        now = datetime.now(timezone.utc)
        instance.status = "RESOLVED"
        instance.resolved_at = now
        instance.resolved_by = resolved_by

        # Check if it was resolved after deadline (late resolution)
        if now > instance.deadline_at and not instance.breached_at:
            instance.breached_at = instance.deadline_at

        db.commit()
        db.refresh(instance)
        logger.info(f"SLA instance {sla_instance_id} resolved by {resolved_by}")
        return instance

    @classmethod
    def get_overdue(
        cls, db: Session, service_type: Optional[str] = None
    ) -> List[SLAInstance]:
        """Returns all SLA instances that have breached or are past deadline."""
        now = datetime.now(timezone.utc)
        query = db.query(SLAInstance).filter(
            SLAInstance.status.in_(["ACTIVE", "ESCALATED"]),
            SLAInstance.deadline_at < now,
        )

        if service_type:
            # Join to definition to filter by service_type
            query = query.join(SLADefinition).filter(
                SLADefinition.service_type == service_type.strip().upper()
            )

        return query.order_by(SLAInstance.deadline_at.asc()).all()

    @classmethod
    def get_entity_sla(
        cls, db: Session, entity_type: str, entity_id: str
    ) -> Optional[SLAInstance]:
        """Returns the most recent SLA instance for an entity."""
        return (
            db.query(SLAInstance)
            .filter(
                SLAInstance.entity_type == entity_type.strip().upper(),
                SLAInstance.entity_id == entity_id,
            )
            .order_by(SLAInstance.created_at.desc())
            .first()
        )
