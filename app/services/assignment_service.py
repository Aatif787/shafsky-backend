"""
Assignment Service — Phase B.5 Shared Domain Services.

Staff assignment lifecycle: assign, reassign, release, complete.
Workload tracking and immutable assignment history.
"""

import uuid
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.shared_domain import Assignment, AssignmentHistory

logger = logging.getLogger("shafsky.services.assignment")


class AssignmentService:

    @classmethod
    def assign(
        cls,
        db: Session,
        entity_type: str,
        entity_id: str,
        staff_id: uuid.UUID,
        assigned_by: Optional[str] = None,
        role_type: str = "GENERAL",
        notes: Optional[str] = None,
    ) -> Assignment:
        """Creates a new assignment and records history."""
        assignment = Assignment(
            entity_type=entity_type.strip().upper(),
            entity_id=entity_id,
            staff_id=staff_id,
            assigned_by=assigned_by or "SYSTEM",
            role_type=role_type.strip().upper(),
            status="ASSIGNED",
            notes=notes,
        )
        db.add(assignment)
        db.flush()

        cls._record_history(
            db,
            assignment_id=assignment.id,
            action="ASSIGN",
            from_status=None,
            to_status="ASSIGNED",
            actor_id=assigned_by or "SYSTEM",
            metadata={"staff_id": str(staff_id), "role_type": role_type},
        )

        db.commit()
        db.refresh(assignment)
        logger.info(f"Assignment {assignment.id} created for {entity_type}:{entity_id} → staff {staff_id}")
        return assignment

    @classmethod
    def reassign(
        cls,
        db: Session,
        assignment_id: uuid.UUID,
        new_staff_id: uuid.UUID,
        reassigned_by: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Assignment:
        """Releases old assignment and creates new one for the same entity."""
        old = db.query(Assignment).filter(Assignment.id == assignment_id).first()
        if not old:
            raise ValueError(f"Assignment '{assignment_id}' not found.")
        if old.status in ("COMPLETED", "RELEASED", "REASSIGNED"):
            raise ValueError(f"Cannot reassign from status '{old.status}'.")

        previous_staff = old.staff_id
        old.status = "REASSIGNED"
        cls._record_history(
            db,
            assignment_id=old.id,
            action="REASSIGN",
            from_status="ASSIGNED",
            to_status="REASSIGNED",
            actor_id=reassigned_by or "SYSTEM",
            reason=reason,
            metadata={"previous_staff": str(previous_staff), "new_staff": str(new_staff_id)},
        )

        new_assignment = Assignment(
            entity_type=old.entity_type,
            entity_id=old.entity_id,
            staff_id=new_staff_id,
            assigned_by=reassigned_by or "SYSTEM",
            role_type=old.role_type,
            status="ASSIGNED",
            notes=f"Reassigned from {previous_staff}. Reason: {reason or 'N/A'}",
        )
        db.add(new_assignment)
        db.flush()

        cls._record_history(
            db,
            assignment_id=new_assignment.id,
            action="ASSIGN",
            from_status=None,
            to_status="ASSIGNED",
            actor_id=reassigned_by or "SYSTEM",
            metadata={"reassigned_from": str(assignment_id)},
        )

        db.commit()
        db.refresh(new_assignment)
        logger.info(f"Reassigned {old.entity_type}:{old.entity_id} from staff {previous_staff} → {new_staff_id}")
        return new_assignment

    @classmethod
    def release(
        cls,
        db: Session,
        assignment_id: uuid.UUID,
        released_by: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Assignment:
        """Releases an active assignment."""
        assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
        if not assignment:
            raise ValueError(f"Assignment '{assignment_id}' not found.")
        if assignment.status in ("COMPLETED", "RELEASED"):
            raise ValueError(f"Cannot release from status '{assignment.status}'.")

        old_status = assignment.status
        assignment.status = "RELEASED"

        cls._record_history(
            db,
            assignment_id=assignment.id,
            action="RELEASE",
            from_status=old_status,
            to_status="RELEASED",
            actor_id=released_by or "SYSTEM",
            reason=reason,
        )

        db.commit()
        db.refresh(assignment)
        logger.info(f"Assignment {assignment_id} released by {released_by}")
        return assignment

    @classmethod
    def complete(
        cls,
        db: Session,
        assignment_id: uuid.UUID,
        completed_by: Optional[str] = None,
    ) -> Assignment:
        """Marks an assignment as completed."""
        assignment = db.query(Assignment).filter(Assignment.id == assignment_id).first()
        if not assignment:
            raise ValueError(f"Assignment '{assignment_id}' not found.")
        if assignment.status == "COMPLETED":
            raise ValueError("Assignment already completed.")

        old_status = assignment.status
        assignment.status = "COMPLETED"

        cls._record_history(
            db,
            assignment_id=assignment.id,
            action="COMPLETE",
            from_status=old_status,
            to_status="COMPLETED",
            actor_id=completed_by or "SYSTEM",
        )

        db.commit()
        db.refresh(assignment)
        logger.info(f"Assignment {assignment_id} completed by {completed_by}")
        return assignment

    @classmethod
    def get_workload(cls, db: Session, staff_id: uuid.UUID) -> Dict[str, Any]:
        """Returns active assignment count and list for a staff member."""
        active = (
            db.query(Assignment)
            .filter(Assignment.staff_id == staff_id, Assignment.status.in_(["ASSIGNED", "ACTIVE"]))
            .all()
        )
        return {
            "staff_id": staff_id,
            "active_count": len(active),
            "assignments": active,
        }

    @classmethod
    def get_entity_assignments(
        cls, db: Session, entity_type: str, entity_id: str
    ) -> List[Assignment]:
        """Returns all assignments for an entity."""
        return (
            db.query(Assignment)
            .filter(
                Assignment.entity_type == entity_type.strip().upper(),
                Assignment.entity_id == entity_id,
            )
            .order_by(Assignment.created_at.desc())
            .all()
        )

    @classmethod
    def get_history(cls, db: Session, assignment_id: uuid.UUID) -> List[AssignmentHistory]:
        """Returns immutable assignment history log."""
        return (
            db.query(AssignmentHistory)
            .filter(AssignmentHistory.assignment_id == assignment_id)
            .order_by(AssignmentHistory.created_at.asc())
            .all()
        )

    @classmethod
    def _record_history(
        cls,
        db: Session,
        assignment_id: uuid.UUID,
        action: str,
        from_status: Optional[str],
        to_status: str,
        actor_id: Optional[str] = None,
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AssignmentHistory:
        """Records an immutable assignment history row."""
        history = AssignmentHistory(
            assignment_id=assignment_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            actor_id=actor_id,
            reason=reason,
            metadata_json=metadata or {},
        )
        db.add(history)
        db.flush()
        return history
