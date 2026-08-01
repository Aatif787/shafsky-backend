"""
Enterprise Generic Workflow Engine (Phase B.1 Core).

Version-pinned definitions, state execution, guard condition evaluation,
role authorization, terminal state protection, immutable history tracking,
and audit trail integration.
"""

import uuid
import logging
from typing import Dict, Any, Optional, Tuple, List
from sqlalchemy.orm import Session
from app.models.schema import (
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowHistory,
    WorkflowAuditLog
)
from app.workflow.definitions import seed_default_workflows
from app.workflow.guards import evaluate_guards
from app.workflow.events import publish_workflow_event

logger = logging.getLogger("shafsky.workflow.engine")


from sqlalchemy import func

class WorkflowEngine:
    @classmethod
    def get_active_definition(
        cls,
        db: Session,
        service_type: str,
        version: Optional[int] = None
    ) -> WorkflowDefinition:
        """
        Retrieves active or version-pinned WorkflowDefinition for the specified service type.
        """
        service_type_clean = service_type.strip().upper()
        query = db.query(WorkflowDefinition).filter(func.upper(WorkflowDefinition.service_type) == service_type_clean)

        if version is not None:
            wf_def = query.filter(WorkflowDefinition.version == version).first()
        else:
            wf_def = query.filter(WorkflowDefinition.is_active == True).first()

        if not wf_def:
            from app.workflow.definitions import DEFAULT_WORKFLOW_DEFINITIONS
            if service_type_clean in DEFAULT_WORKFLOW_DEFINITIONS:
                logger.info(f"No active definition found for '{service_type_clean}'. Seeding defaults.")
                seed_default_workflows(db)
                if version is not None:
                    wf_def = query.filter(WorkflowDefinition.version == version).first()
                else:
                    wf_def = query.filter(WorkflowDefinition.is_active == True).first()

        if not wf_def:
            raise ValueError(f"Workflow definition for '{service_type_clean}' (version={version}) not found.")

        return wf_def

    @classmethod
    def create_instance(
        cls,
        db: Session,
        service_type: str,
        entity_id: str,
        actor_id: Optional[str] = None,
        initial_context: Optional[Dict[str, Any]] = None,
        version: Optional[int] = None
    ) -> WorkflowInstance:
        """
        Initializes a new version-pinned workflow instance for an entity.
        """
        wf_def = cls.get_active_definition(db, service_type, version=version)
        context = initial_context or {}

        instance = WorkflowInstance(
            workflow_definition_id=wf_def.id,
            service_type=wf_def.service_type,
            entity_id=entity_id,
            current_state=wf_def.initial_state,
            context_data=context,
            is_completed=False
        )
        db.add(instance)
        db.flush()

        # Record History & Audit
        cls.record_history(
            db,
            instance_id=instance.id,
            from_state="NONE",
            to_state=wf_def.initial_state,
            action="INITIALIZE",
            actor_id=actor_id or "SYSTEM",
            actor_role="SYSTEM",
            payload=context,
            metadata={"workflow_version": wf_def.version}
        )

        cls.write_audit_log(
            db,
            instance_id=instance.id,
            event_type="WORKFLOW_INITIALIZED",
            actor_id=actor_id or "SYSTEM",
            details={
                "service_type": wf_def.service_type,
                "entity_id": entity_id,
                "initial_state": wf_def.initial_state,
                "version": wf_def.version
            }
        )

        db.commit()
        db.refresh(instance)

        # Publish WORKFLOW_CREATED event
        publish_workflow_event(
            db,
            event_type="WORKFLOW_CREATED",
            instance_id=str(instance.id),
            service_type=wf_def.service_type,
            payload={
                "entity_id": entity_id,
                "initial_state": wf_def.initial_state,
                "version": wf_def.version
            },
            published_by=actor_id or "workflow_engine",
            previous_state="NONE",
            current_state=wf_def.initial_state,
            action="INITIALIZE",
            version=wf_def.version,
            entity_id=entity_id
        )

        logger.info(f"Initialized workflow instance {instance.id} for {wf_def.service_type}:{entity_id} (v{wf_def.version})")
        return instance

    @classmethod
    def evaluate_guards(cls, guards_config: Dict[str, Any], payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Evaluates declarative transition guards against transition payload."""
        return evaluate_guards(guards_config, payload)

    @classmethod
    def validate_transition(
        cls,
        definition: WorkflowDefinition,
        current_state: str,
        action: str,
        actor_role: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str, str]:
        """
        Validates if a transition action is allowed from current_state by actor_role.
        Returns Tuple[is_valid, target_state, error_message].
        """
        states_cfg = definition.states_config or {}
        curr_cfg = states_cfg.get(current_state)

        if not curr_cfg:
            return False, "", f"Current state '{current_state}' is not defined in workflow configuration."

        # Terminal state protection
        if curr_cfg.get("terminal"):
            return False, "", f"Cannot transition from terminal state '{current_state}'."

        allowed_actions = curr_cfg.get("allowed_actions", {})
        action_cfg = allowed_actions.get(action.upper())

        if not action_cfg:
            valid_actions = list(allowed_actions.keys())
            return False, "", f"Invalid action '{action}' from state '{current_state}'. Allowed actions: {valid_actions}"

        target_state = action_cfg.get("target")
        if not target_state:
            return False, "", f"Target state for action '{action}' is unconfigured."

        # Role Authorization Check
        required_roles = action_cfg.get("roles", [])
        if required_roles and actor_role:
            role_clean = actor_role.upper()
            if role_clean not in ["ADMIN", "SUPER_ADMIN"] and role_clean not in [r.upper() for r in required_roles]:
                return False, "", f"Role '{actor_role}' is not authorized to execute action '{action}'. Required roles: {required_roles}"

        # Guard Rule Evaluation Check
        guards_config = action_cfg.get("guards", {})
        if guards_config:
            is_passed, guard_errors = cls.evaluate_guards(guards_config, payload or {})
            if not is_passed:
                return False, "", f"Guard conditions failed: {'; '.join(guard_errors)}"

        return True, target_state, ""

    @classmethod
    def execute_transition(
        cls,
        db: Session,
        instance_id: uuid.UUID,
        action: str,
        actor_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None
    ) -> WorkflowInstance:
        """
        Executes a state transition for the given workflow instance.
        """
        instance = db.query(WorkflowInstance).filter(WorkflowInstance.id == instance_id).first()
        if not instance:
            raise ValueError(f"Workflow instance '{instance_id}' not found.")

        # Always use the instance's version-pinned definition
        definition = instance.definition or db.query(WorkflowDefinition).filter(
            WorkflowDefinition.id == instance.workflow_definition_id
        ).first()

        old_state = instance.current_state
        action_clean = action.strip().upper()
        payload_dict = payload or {}

        # Frozen Check
        if getattr(instance, "is_frozen", False):
            role_clean = (actor_role or "").upper()
            if role_clean not in ["ADMIN", "SUPER_ADMIN"]:
                err_msg = f"Workflow instance '{instance_id}' is currently frozen. Contact administrator."
                logger.warning(f"Transition rejected for frozen instance {instance_id}")
                publish_workflow_event(
                    db,
                    event_type="TRANSITION_REJECTED",
                    instance_id=str(instance.id),
                    service_type=instance.service_type,
                    payload={"error": err_msg, **payload_dict},
                    published_by=actor_id or "workflow_engine",
                    previous_state=old_state,
                    current_state=old_state,
                    action=action_clean,
                    version=definition.version,
                    entity_id=instance.entity_id,
                    actor_role=actor_role,
                    correlation_id=correlation_id
                )
                raise ValueError(err_msg)

        # Publish TRANSITION_STARTED
        publish_workflow_event(
            db,
            event_type="TRANSITION_STARTED",
            instance_id=str(instance.id),
            service_type=instance.service_type,
            payload=payload_dict,
            published_by=actor_id or "workflow_engine",
            previous_state=old_state,
            current_state=old_state,
            action=action_clean,
            version=definition.version,
            entity_id=instance.entity_id,
            actor_role=actor_role,
            correlation_id=correlation_id
        )

        # Validate transition rules
        is_valid, target_state, err_msg = cls.validate_transition(
            definition,
            old_state,
            action_clean,
            actor_role,
            payload_dict
        )

        if not is_valid:
            logger.warning(f"Transition rejected for instance {instance_id}: {err_msg}")
            publish_workflow_event(
                db,
                event_type="TRANSITION_REJECTED",
                instance_id=str(instance.id),
                service_type=instance.service_type,
                payload={"error": err_msg, **payload_dict},
                published_by=actor_id or "workflow_engine",
                previous_state=old_state,
                current_state=old_state,
                action=action_clean,
                version=definition.version,
                entity_id=instance.entity_id,
                actor_role=actor_role,
                correlation_id=correlation_id
            )
            raise ValueError(err_msg)

        # Update instance state
        instance.current_state = target_state
        updated_context = dict(instance.context_data or {})
        updated_context.update(payload_dict)
        instance.context_data = updated_context

        # Check if target state is terminal
        target_cfg = (definition.states_config or {}).get(target_state, {})
        if target_cfg.get("terminal"):
            instance.is_completed = True

        # Record History
        cls.record_history(
            db,
            instance_id=instance.id,
            from_state=old_state,
            to_state=target_state,
            action=action_clean,
            actor_id=actor_id or "SYSTEM",
            actor_role=actor_role or "SYSTEM",
            payload=payload_dict,
            metadata={"is_terminal": instance.is_completed}
        )

        # Record Audit Log
        cls.write_audit_log(
            db,
            instance_id=instance.id,
            event_type="TRANSITION_EXECUTED",
            actor_id=actor_id or "SYSTEM",
            details={
                "from_state": old_state,
                "to_state": target_state,
                "action": action_clean,
                "actor_role": actor_role
            }
        )

        db.commit()
        db.refresh(instance)

        # Determine completion/cancel event type
        if instance.is_completed:
            final_event_type = "WORKFLOW_CANCELLED" if "CANCEL" in action_clean or "REJECT" in action_clean else "WORKFLOW_COMPLETED"
        else:
            final_event_type = "TRANSITION_COMPLETED"

        # Publish Event
        publish_workflow_event(
            db,
            event_type=final_event_type,
            instance_id=str(instance.id),
            service_type=instance.service_type,
            payload={
                "entity_id": instance.entity_id,
                "from_state": old_state,
                "to_state": target_state,
                "action": action_clean,
                "is_completed": instance.is_completed
            },
            published_by=actor_id or "workflow_engine",
            previous_state=old_state,
            current_state=target_state,
            action=action_clean,
            version=definition.version,
            entity_id=instance.entity_id,
            actor_role=actor_role,
            correlation_id=correlation_id
        )

        logger.info(f"Transition executed for instance {instance.id}: {old_state} -> {target_state} via {action_clean}")
        return instance

    @classmethod
    def record_history(
        cls,
        db: Session,
        instance_id: uuid.UUID,
        from_state: str,
        to_state: str,
        action: str,
        actor_id: str,
        actor_role: str,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None
    ) -> WorkflowHistory:
        """Records an immutable workflow history row."""
        history = WorkflowHistory(
            instance_id=instance_id,
            from_state=from_state,
            to_state=to_state,
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            payload=payload,
            transition_metadata=metadata or {}
        )
        db.add(history)
        db.flush()
        return history

    @classmethod
    def write_audit_log(
        cls,
        db: Session,
        instance_id: uuid.UUID,
        event_type: str,
        actor_id: str,
        details: Dict[str, Any]
    ) -> WorkflowAuditLog:
        """Writes a workflow audit log row."""
        audit = WorkflowAuditLog(
            instance_id=instance_id,
            event_type=event_type,
            actor_id=actor_id,
            details=details
        )
        db.add(audit)
        db.flush()
        return audit

    @classmethod
    def get_history(cls, db: Session, instance_id: uuid.UUID) -> Dict[str, Any]:
        """Retrieves instance status, transition history, and audit trail."""
        instance = db.query(WorkflowInstance).filter(WorkflowInstance.id == instance_id).first()
        if not instance:
            raise ValueError(f"Workflow instance '{instance_id}' not found.")

        history_records = db.query(WorkflowHistory).filter(
            WorkflowHistory.instance_id == instance_id
        ).order_by(WorkflowHistory.timestamp.asc()).all()

        audit_records = db.query(WorkflowAuditLog).filter(
            WorkflowAuditLog.instance_id == instance_id
        ).order_by(WorkflowAuditLog.created_at.asc()).all()

        return {
            "instance": instance,
            "history": history_records,
            "audit_logs": audit_records
        }

    # ─────────────────────────────────────────────
    # Administrative & Operations Methods (Phase B.4)
    # ─────────────────────────────────────────────

    @classmethod
    def freeze_instance(
        cls,
        db: Session,
        instance_id: uuid.UUID,
        actor_id: str,
        reason: Optional[str] = None
    ) -> WorkflowInstance:
        """Freezes a workflow instance preventing further non-admin transitions."""
        instance = db.query(WorkflowInstance).filter(WorkflowInstance.id == instance_id).first()
        if not instance:
            raise ValueError(f"Workflow instance '{instance_id}' not found.")

        instance.is_frozen = True
        cls.write_audit_log(
            db,
            instance_id=instance.id,
            event_type="WORKFLOW_FROZEN",
            actor_id=actor_id,
            details={"reason": reason or "Administrative freeze"}
        )
        db.commit()
        db.refresh(instance)
        logger.info(f"Workflow instance {instance_id} frozen by {actor_id}")
        return instance

    @classmethod
    def resume_instance(
        cls,
        db: Session,
        instance_id: uuid.UUID,
        actor_id: str
    ) -> WorkflowInstance:
        """Resumes a frozen workflow instance."""
        instance = db.query(WorkflowInstance).filter(WorkflowInstance.id == instance_id).first()
        if not instance:
            raise ValueError(f"Workflow instance '{instance_id}' not found.")

        instance.is_frozen = False
        cls.write_audit_log(
            db,
            instance_id=instance.id,
            event_type="WORKFLOW_RESUMED",
            actor_id=actor_id,
            details={"action": "Administrative resume"}
        )
        db.commit()
        db.refresh(instance)
        logger.info(f"Workflow instance {instance_id} resumed by {actor_id}")
        return instance

    @classmethod
    def cancel_instance(
        cls,
        db: Session,
        instance_id: uuid.UUID,
        actor_id: str,
        reason: Optional[str] = None
    ) -> WorkflowInstance:
        """Cancels a workflow instance, setting state to CANCELLED and marking completed."""
        instance = db.query(WorkflowInstance).filter(WorkflowInstance.id == instance_id).first()
        if not instance:
            raise ValueError(f"Workflow instance '{instance_id}' not found.")

        old_state = instance.current_state
        instance.current_state = "CANCELLED"
        instance.is_completed = True

        cls.record_history(
            db,
            instance_id=instance.id,
            from_state=old_state,
            to_state="CANCELLED",
            action="ADMIN_CANCEL",
            actor_id=actor_id,
            actor_role="ADMIN",
            payload={"reason": reason},
            metadata={"admin_override": True}
        )

        cls.write_audit_log(
            db,
            instance_id=instance.id,
            event_type="WORKFLOW_CANCELLED",
            actor_id=actor_id,
            details={"from_state": old_state, "reason": reason or "Admin cancellation"}
        )

        db.commit()
        db.refresh(instance)

        publish_workflow_event(
            db,
            event_type="WORKFLOW_CANCELLED",
            instance_id=str(instance.id),
            service_type=instance.service_type,
            payload={"reason": reason, "entity_id": instance.entity_id},
            published_by=actor_id,
            previous_state=old_state,
            current_state="CANCELLED",
            action="ADMIN_CANCEL",
            version=instance.definition.version if instance.definition else 1,
            entity_id=instance.entity_id
        )

        logger.info(f"Workflow instance {instance_id} cancelled by {actor_id}")
        return instance

    @classmethod
    def force_transition(
        cls,
        db: Session,
        instance_id: uuid.UUID,
        target_state: str,
        actor_id: str,
        actor_role: str = "SUPER_ADMIN",
        reason: Optional[str] = None
    ) -> WorkflowInstance:
        """
        Forces a state transition bypassing standard guards and role rules (Admin override).
        Mandatory audit log recorded.
        """
        instance = db.query(WorkflowInstance).filter(WorkflowInstance.id == instance_id).first()
        if not instance:
            raise ValueError(f"Workflow instance '{instance_id}' not found.")

        old_state = instance.current_state
        target_clean = target_state.strip().upper()

        definition = instance.definition or db.query(WorkflowDefinition).filter(
            WorkflowDefinition.id == instance.workflow_definition_id
        ).first()

        instance.current_state = target_clean

        # Check if forced target state is terminal
        target_cfg = (definition.states_config or {}).get(target_clean, {}) if definition else {}
        if target_cfg.get("terminal"):
            instance.is_completed = True

        cls.record_history(
            db,
            instance_id=instance.id,
            from_state=old_state,
            to_state=target_clean,
            action="FORCE_TRANSITION",
            actor_id=actor_id,
            actor_role=actor_role,
            payload={"reason": reason},
            metadata={"admin_override": True, "bypassed_guards": True}
        )

        cls.write_audit_log(
            db,
            instance_id=instance.id,
            event_type="WORKFLOW_FORCE_TRANSITION",
            actor_id=actor_id,
            details={
                "from_state": old_state,
                "to_state": target_clean,
                "reason": reason or "Admin forced transition",
                "actor_role": actor_role
            }
        )

        db.commit()
        db.refresh(instance)

        publish_workflow_event(
            db,
            event_type="TRANSITION_COMPLETED",
            instance_id=str(instance.id),
            service_type=instance.service_type,
            payload={"reason": reason, "entity_id": instance.entity_id, "forced": True},
            published_by=actor_id,
            previous_state=old_state,
            current_state=target_clean,
            action="FORCE_TRANSITION",
            version=definition.version if definition else 1,
            entity_id=instance.entity_id,
            actor_role=actor_role
        )

        logger.info(f"Workflow instance {instance_id} FORCE transitioned to {target_clean} by {actor_id}")
        return instance

