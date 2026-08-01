"""
Workflow Administration Service — Phase B.4 Administration & Operations.

Provides operational management, dashboarding, multi-field search,
metrics aggregation, unified timeline synthesis, failure monitoring,
retry execution, and health diagnostics.
"""

import uuid
import time
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, func, or_

from app.models.schema import (
    WorkflowInstance,
    WorkflowDefinition,
    WorkflowHistory,
    WorkflowAuditLog,
)
from app.models.system_events import WorkflowEventRecord
from app.workflow.engine import WorkflowEngine
from app.core.redis import check_redis_health, get_redis_client

logger = logging.getLogger("shafsky.workflow.admin")


class WorkflowAdminService:

    @classmethod
    def get_active_workflows(
        cls,
        db: Session,
        service_type: Optional[str] = None,
        state: Optional[str] = None,
        assigned_staff: Optional[str] = None,
        airport: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "created_at",
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """
        Dashboard query listing active workflows with filters for service, state,
        assigned staff, and airport code with pagination and sorting.
        """
        query = db.query(WorkflowInstance).filter(WorkflowInstance.is_completed == False)

        if service_type:
            query = query.filter(func.upper(WorkflowInstance.service_type) == service_type.strip().upper())

        if state:
            query = query.filter(func.upper(WorkflowInstance.current_state) == state.strip().upper())

        rows = query.all()

        # In-memory filtering for context attributes (assigned_staff, airport)
        filtered = []
        for instance in rows:
            ctx = instance.context_data or {}
            if assigned_staff:
                staff_ref = str(ctx.get("assigned_staff_id") or ctx.get("staff_id") or ctx.get("assigned_to") or "")
                if assigned_staff.lower() not in staff_ref.lower():
                    continue

            if airport:
                apt_ref = str(ctx.get("airport_code") or ctx.get("airport") or ctx.get("iata_code") or "")
                if airport.upper() not in apt_ref.upper():
                    continue

            filtered.append(instance)

        total = len(filtered)

        # Sorting
        reverse = sort_order.lower() == "desc"
        filtered.sort(
            key=lambda item: getattr(item, sort_by, item.created_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=reverse
        )

        paged = filtered[offset: offset + limit]

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "data": paged
        }

    @classmethod
    def search_workflows(
        cls,
        db: Session,
        query_str: str,
        service_type: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Multi-field search across Workflow ID, Entity ID, and context keys:
        booking_id, passenger_name, flight_number, awb, visa_reference, hotel_confirmation, pnr.
        """
        pattern = f"%{query_str.strip()}%"
        q = db.query(WorkflowInstance)

        if service_type:
            q = q.filter(func.upper(WorkflowInstance.service_type) == service_type.strip().upper())

        if state:
            q = q.filter(func.upper(WorkflowInstance.current_state) == state.strip().upper())

        all_instances = q.all()
        matched = []

        q_lower = query_str.strip().lower()

        for inst in all_instances:
            # Match UUID or Entity ID
            if q_lower in str(inst.id).lower() or q_lower in str(inst.entity_id).lower():
                matched.append(inst)
                continue

            # Match context fields
            ctx = inst.context_data or {}
            ctx_str = str(ctx).lower()

            search_keys = [
                "booking_id", "passenger_name", "flight_number", "awb",
                "visa_reference", "hotel_confirmation", "pnr", "passenger",
                "flight", "pnr_code", "awb_number"
            ]

            match_found = False
            for k, v in ctx.items():
                if k.lower() in search_keys and q_lower in str(v).lower():
                    match_found = True
                    break

            if not match_found and q_lower in ctx_str:
                match_found = True

            if match_found:
                matched.append(inst)

        total = len(matched)
        paged = matched[offset: offset + limit]

        return {
            "query": query_str,
            "total": total,
            "limit": limit,
            "offset": offset,
            "results": paged
        }

    @classmethod
    def get_workflow_metrics(cls, db: Session) -> Dict[str, Any]:
        """
        Aggregates system-wide workflow engine operational metrics.
        """
        all_instances = db.query(WorkflowInstance).all()
        total_workflows = len(all_instances)

        active = 0
        completed = 0
        cancelled = 0
        frozen = 0
        completion_times = []
        services_breakdown: Dict[str, int] = {}

        for inst in all_instances:
            st = inst.service_type
            services_breakdown[st] = services_breakdown.get(st, 0) + 1

            if getattr(inst, "is_frozen", False):
                frozen += 1

            if inst.is_completed:
                if inst.current_state == "CANCELLED":
                    cancelled += 1
                else:
                    completed += 1

                if inst.created_at and inst.updated_at:
                    delta = (inst.updated_at - inst.created_at).total_seconds() / 60.0
                    completion_times.append(delta)
            else:
                active += 1

        avg_completion = round(sum(completion_times) / len(completion_times), 2) if completion_times else 0.0

        # Total transitions
        total_transitions = db.query(WorkflowHistory).count()
        avg_transitions = round(total_transitions / total_workflows, 2) if total_workflows > 0 else 0.0

        # SLA breaches count (instances with TRANSITION_REJECTED or context sla_breached)
        sla_breaches = db.query(WorkflowAuditLog).filter(WorkflowAuditLog.event_type == "SLA_BREACHED").count()

        return {
            "total_workflows": total_workflows,
            "active_workflows": active,
            "completed_workflows": completed,
            "cancelled_workflows": cancelled,
            "frozen_workflows": frozen,
            "avg_completion_time_minutes": avg_completion,
            "sla_breaches_count": sla_breaches,
            "total_transitions": total_transitions,
            "avg_transitions_per_workflow": avg_transitions,
            "services_breakdown": services_breakdown
        }

    @classmethod
    def get_unified_timeline(cls, db: Session, instance_id: uuid.UUID) -> Dict[str, Any]:
        """
        Synthesizes chronological unified timeline merging WorkflowHistory,
        WorkflowAuditLog, and WorkflowEventRecord for an instance.
        """
        instance = db.query(WorkflowInstance).filter(WorkflowInstance.id == instance_id).first()
        if not instance:
            raise ValueError(f"Workflow instance '{instance_id}' not found.")

        timeline_items = []

        # 1. History
        histories = db.query(WorkflowHistory).filter(WorkflowHistory.instance_id == instance_id).all()
        for h in histories:
            timeline_items.append({
                "source": "HISTORY",
                "event_type": f"TRANSITION_{h.action}",
                "title": f"Transition: {h.from_state} -> {h.to_state} via {h.action}",
                "timestamp": h.timestamp,
                "actor_id": h.actor_id,
                "actor_role": h.actor_role,
                "from_state": h.from_state,
                "to_state": h.to_state,
                "action": h.action,
                "details": h.payload or {}
            })

        # 2. Audit Logs
        audits = db.query(WorkflowAuditLog).filter(WorkflowAuditLog.instance_id == instance_id).all()
        for a in audits:
            timeline_items.append({
                "source": "AUDIT",
                "event_type": a.event_type,
                "title": f"Audit: {a.event_type}",
                "timestamp": a.created_at,
                "actor_id": a.actor_id,
                "actor_role": a.details.get("actor_role") if isinstance(a.details, dict) else None,
                "from_state": a.details.get("from_state") if isinstance(a.details, dict) else None,
                "to_state": a.details.get("to_state") if isinstance(a.details, dict) else None,
                "action": a.details.get("action") if isinstance(a.details, dict) else None,
                "details": a.details or {}
            })

        # 3. Event Bus Records
        events = db.query(WorkflowEventRecord).filter(WorkflowEventRecord.workflow_instance_id == instance_id).all()
        for e in events:
            timeline_items.append({
                "source": "EVENT_BUS",
                "event_type": e.event_type,
                "title": f"Event Published: {e.event_type} (seq #{e.sequence_number})",
                "timestamp": e.created_at,
                "actor_id": e.actor_id,
                "actor_role": e.actor_role,
                "from_state": e.previous_state,
                "to_state": e.current_state,
                "action": e.action,
                "details": e.payload or {}
            })

        # Sort chronologically
        timeline_items.sort(key=lambda item: item["timestamp"] or datetime.min.replace(tzinfo=timezone.utc))

        return {
            "instance_id": instance_id,
            "total": len(timeline_items),
            "timeline": timeline_items
        }

    @classmethod
    def get_failed_workflows(
        cls,
        db: Session,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Lists failed workflows and rejected transitions with details and error descriptions.
        """
        # Query audit logs for rejected transitions or guard failures
        rejected_audits = db.query(WorkflowAuditLog).filter(
            WorkflowAuditLog.event_type.in_(["TRANSITION_REJECTED", "GUARD_FAILURE", "WORKFLOW_ERROR"])
        ).order_by(desc(WorkflowAuditLog.created_at)).all()

        failed_items = []
        for audit in rejected_audits:
            instance = db.query(WorkflowInstance).filter(WorkflowInstance.id == audit.instance_id).first()
            if not instance:
                continue

            details = audit.details or {}
            err_msg = details.get("error") or details.get("reason") or "Transition rejected"

            failed_items.append({
                "instance_id": instance.id,
                "service_type": instance.service_type,
                "entity_id": instance.entity_id,
                "current_state": instance.current_state,
                "failed_event_type": audit.event_type,
                "error_message": err_msg,
                "actor_id": audit.actor_id,
                "timestamp": audit.created_at,
                "details": details
            })

        total = len(failed_items)
        paged = failed_items[offset: offset + limit]

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "data": paged
        }

    @classmethod
    def retry_workflow(
        cls,
        db: Session,
        instance_id: uuid.UUID,
        actor_id: str,
        reason: Optional[str] = None
    ) -> WorkflowInstance:
        """
        Retries a failed transition for an instance if a rejected action exists.
        Records WORKFLOW_RETRY_EXECUTED audit log.
        """
        instance = db.query(WorkflowInstance).filter(WorkflowInstance.id == instance_id).first()
        if not instance:
            raise ValueError(f"Workflow instance '{instance_id}' not found.")

        # Find latest rejected transition audit log
        last_rejected = db.query(WorkflowAuditLog).filter(
            WorkflowAuditLog.instance_id == instance_id,
            WorkflowAuditLog.event_type == "TRANSITION_REJECTED"
        ).order_by(desc(WorkflowAuditLog.created_at)).first()

        if getattr(instance, "is_frozen", False):
            instance.is_frozen = False  # Auto-unfreeze on admin retry

        cls.write_audit_log(
            db,
            instance_id=instance.id,
            event_type="WORKFLOW_RETRY_EXECUTED",
            actor_id=actor_id,
            details={
                "reason": reason or "Admin manual retry",
                "previous_failed_event": last_rejected.id if last_rejected else None
            }
        )

        db.commit()
        db.refresh(instance)
        logger.info(f"Admin retry executed for workflow instance {instance_id} by {actor_id}")
        return instance

    @classmethod
    def get_workflow_system_health(cls, db: Session) -> Dict[str, Any]:
        """
        Runs diagnostics across Workflow Engine, Redis Connection, DB pool, and Event Bus.
        """
        now = datetime.now(timezone.utc)

        # 1. Workflow Engine Status
        start_t = time.perf_counter()
        active_count = db.query(WorkflowInstance).filter(WorkflowInstance.is_completed == False).count()
        def_count = db.query(WorkflowDefinition).filter(WorkflowDefinition.is_active == True).count()
        engine_latency = round((time.perf_counter() - start_t) * 1000, 2)

        engine_health = {
            "status": "healthy",
            "latency_ms": engine_latency,
            "details": {"active_instances": active_count, "active_definitions": def_count}
        }

        # 2. DB Status
        start_t = time.perf_counter()
        try:
            from sqlalchemy import text
            db.execute(text("SELECT 1"))
            db_latency = round((time.perf_counter() - start_t) * 1000, 2)
            db_health = {"status": "healthy", "latency_ms": db_latency, "details": {"connected": True}}
        except Exception as err:
            db_health = {"status": "unhealthy", "latency_ms": None, "details": {"error": str(err)}}

        # 3. Redis Status
        redis_diag = check_redis_health()
        redis_health = {
            "status": redis_diag.get("status", "unhealthy"),
            "latency_ms": redis_diag.get("latency_ms"),
            "details": {"host": redis_diag.get("host"), "port": redis_diag.get("port")}
        }

        # 4. Event Bus Status
        event_count = db.query(WorkflowEventRecord).count()
        event_bus_health = {
            "status": "healthy" if redis_diag.get("status") == "healthy" else "degraded",
            "latency_ms": redis_diag.get("latency_ms"),
            "details": {"total_events_published": event_count, "pubsub_active": redis_diag.get("connected", False)}
        }

        # Overall Status
        statuses = [engine_health["status"], db_health["status"], redis_health["status"], event_bus_health["status"]]
        if "unhealthy" in statuses:
            overall = "UNHEALTHY"
        elif "degraded" in statuses:
            overall = "DEGRADED"
        else:
            overall = "HEALTHY"

        return {
            "status": overall,
            "timestamp": now,
            "workflow_engine": engine_health,
            "redis": redis_health,
            "database": db_health,
            "event_bus": event_bus_health
        }
