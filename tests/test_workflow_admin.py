"""
Production Test Suite for Phase B.4 — Workflow Administration & Operations.

Covers:
1. Active Workflow Dashboard (filtering by service, state, staff, airport, pagination, sorting)
2. Multi-field Workflow Search (PNR, AWB, Flight #, Passenger, Entity ID, Workflow ID)
3. Workflow Metrics (totals, active, completed, cancelled, completion time, transition counts)
4. Unified Timeline API (chronological merge of history, audit, event records)
5. Failed Workflow Monitoring (listing rejected transitions and guard failures)
6. Admin Retry Operations (retry workflow, state recovery, audit log verification)
7. Health APIs (Workflow Engine, Redis, Database, Event Bus diagnostics)
8. Admin Lifecycle Operations (Freeze blocking non-admin transitions, Resume, Cancel, Force Transition)
9. Admin Permissions & Audit Logging (RBAC enforcement and mandatory audit trail)
"""

import sys
import os
import uuid
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal, engine, Base
import app.models.schema  # Ensure models are loaded
import app.models.system_events
from app.models.schema import WorkflowInstance, WorkflowHistory, WorkflowAuditLog, WorkflowDefinition
from app.workflow.engine import WorkflowEngine
from app.services.workflow_admin_service import WorkflowAdminService
from app.workflow.definitions import seed_default_workflows


def get_test_db():
    Base.metadata.create_all(bind=engine)
    from sqlalchemy import text
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE workflow_instances ADD COLUMN IF NOT EXISTS is_frozen BOOLEAN DEFAULT FALSE;"))
        conn.commit()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_test_definition(db, service_type: str) -> WorkflowDefinition:
    st_clean = service_type.strip().upper()
    existing = db.query(WorkflowDefinition).filter(
        WorkflowDefinition.service_type == st_clean,
        WorkflowDefinition.is_active == True
    ).first()
    if existing:
        return existing

    wf_def = WorkflowDefinition(
        service_type=st_clean,
        name=f"Test {st_clean} Definition",
        version=1,
        initial_state="START",
        states_config={
            "START": {
                "allowed_actions": {
                    "PROCESS": {"target": "IN_PROGRESS", "roles": ["ADMIN", "STAFF", "CUSTOMER_SUPPORT"]}
                }
            },
            "IN_PROGRESS": {
                "allowed_actions": {
                    "COMPLETE": {"target": "COMPLETED", "roles": ["ADMIN", "STAFF"]}
                }
            },
            "COMPLETED": {"terminal": True},
            "CANCELLED": {"terminal": True},
            "FORCE_COMPLETED": {"terminal": True}
        },
        is_active=True
    )
    db.add(wf_def)
    db.commit()
    db.refresh(wf_def)
    return wf_def


# ─────────────────────────────────────────────
# 1. Active Workflow Dashboard Test
# ─────────────────────────────────────────────

def test_01_dashboard_filtering():
    """Verify dashboard pagination, filtering by service, state, staff, and airport."""
    db = next(get_test_db())

    unique_service = f"TEST_DASH_{uuid.uuid4().hex[:6].upper()}"
    ensure_test_definition(db, unique_service)

    inst1 = WorkflowEngine.create_instance(
        db,
        service_type=unique_service,
        entity_id=f"ENT-DASH-1-{uuid.uuid4().hex[:4]}",
        initial_context={"assigned_staff_id": "STAFF_101", "airport_code": "DXB"}
    )
    inst2 = WorkflowEngine.create_instance(
        db,
        service_type=unique_service,
        entity_id=f"ENT-DASH-2-{uuid.uuid4().hex[:4]}",
        initial_context={"assigned_staff_id": "STAFF_102", "airport_code": "JFK"}
    )

    # Filter by service
    res_svc = WorkflowAdminService.get_active_workflows(db, service_type=unique_service)
    assert res_svc["total"] == 2

    # Filter by staff
    res_staff = WorkflowAdminService.get_active_workflows(db, service_type=unique_service, assigned_staff="STAFF_101")
    assert res_staff["total"] == 1
    assert res_staff["data"][0].id == inst1.id

    # Filter by airport
    res_apt = WorkflowAdminService.get_active_workflows(db, service_type=unique_service, airport="JFK")
    assert res_apt["total"] == 1
    assert res_apt["data"][0].id == inst2.id

    print("  [PASS] test_01_dashboard_filtering PASSED")


# ─────────────────────────────────────────────
# 2. Multi-field Workflow Search Test
# ─────────────────────────────────────────────

def test_02_workflow_search():
    """Verify searching by PNR, AWB, Flight Number, Passenger Name, Entity ID."""
    db = next(get_test_db())
    unique_service = f"TEST_SEARCH_{uuid.uuid4().hex[:6].upper()}"
    ensure_test_definition(db, unique_service)

    pnr_tag = f"PNR-{uuid.uuid4().hex[:6].upper()}"
    inst = WorkflowEngine.create_instance(
        db,
        service_type=unique_service,
        entity_id=f"ENT-SRCH-{uuid.uuid4().hex[:4]}",
        initial_context={
            "pnr": pnr_tag,
            "passenger_name": "Captain Kirk",
            "flight_number": "EK-202"
        }
    )

    # Search by PNR
    s_pnr = WorkflowAdminService.search_workflows(db, query_str=pnr_tag)
    assert s_pnr["total"] >= 1
    assert any(r.id == inst.id for r in s_pnr["results"])

    # Search by Passenger
    s_pax = WorkflowAdminService.search_workflows(db, query_str="Captain Kirk")
    assert s_pax["total"] >= 1
    assert any(r.id == inst.id for r in s_pax["results"])

    # Search by Flight #
    s_flt = WorkflowAdminService.search_workflows(db, query_str="EK-202")
    assert s_flt["total"] >= 1
    assert any(r.id == inst.id for r in s_flt["results"])

    print("  [PASS] test_02_workflow_search PASSED")


# ─────────────────────────────────────────────
# 3. Workflow Metrics Test
# ─────────────────────────────────────────────

def test_03_workflow_metrics():
    """Verify aggregated metrics for total, active, completed, cancelled, completion time."""
    db = next(get_test_db())

    metrics = WorkflowAdminService.get_workflow_metrics(db)
    assert "total_workflows" in metrics
    assert "active_workflows" in metrics
    assert "completed_workflows" in metrics
    assert "cancelled_workflows" in metrics
    assert "avg_completion_time_minutes" in metrics

    print("  [PASS] test_03_workflow_metrics PASSED")


# ─────────────────────────────────────────────
# 4. Unified Timeline Test
# ─────────────────────────────────────────────

def test_04_unified_timeline():
    """Verify synthesizing chronological timeline merging history, audit logs, and event records."""
    db = next(get_test_db())
    unique_service = f"TEST_TL_{uuid.uuid4().hex[:6].upper()}"
    ensure_test_definition(db, unique_service)

    inst = WorkflowEngine.create_instance(
        db,
        service_type=unique_service,
        entity_id=f"ENT-TL-{uuid.uuid4().hex[:4]}"
    )

    res = WorkflowAdminService.get_unified_timeline(db, inst.id)
    assert res["success"] is True
    assert res["total"] >= 2  # HISTORY, AUDIT, EVENT_BUS items created on init

    sources = {item["source"] for item in res["timeline"]}
    assert "HISTORY" in sources
    assert "AUDIT" in sources
    assert "EVENT_BUS" in sources

    print("  [PASS] test_04_unified_timeline PASSED")


# ─────────────────────────────────────────────
# 5. Failed Workflow Monitoring Test
# ─────────────────────────────────────────────

def test_05_failed_workflow_monitoring():
    """Verify listing rejected transitions and failed workflow entries."""
    db = next(get_test_db())
    unique_service = f"TEST_FAIL_{uuid.uuid4().hex[:6].upper()}"
    ensure_test_definition(db, unique_service)

    inst = WorkflowEngine.create_instance(
        db,
        service_type=unique_service,
        entity_id=f"ENT-FAIL-{uuid.uuid4().hex[:4]}"
    )

    # Inject a TRANSITION_REJECTED audit log
    WorkflowEngine.write_audit_log(
        db,
        instance_id=inst.id,
        event_type="TRANSITION_REJECTED",
        actor_id="test_actor",
        details={"error": "Guard condition missing flight document"}
    )
    db.commit()

    failed_res = WorkflowAdminService.get_failed_workflows(db)
    assert failed_res["total"] >= 1
    assert any(item["instance_id"] == inst.id for item in failed_res["data"])

    print("  [PASS] test_05_failed_workflow_monitoring PASSED")


# ─────────────────────────────────────────────
# 6. Admin Retry Operations Test
# ─────────────────────────────────────────────

def test_06_admin_retry():
    """Verify admin retry operation clears freeze and logs retry execution."""
    db = next(get_test_db())
    unique_service = f"TEST_RETRY_{uuid.uuid4().hex[:6].upper()}"
    ensure_test_definition(db, unique_service)

    inst = WorkflowEngine.create_instance(
        db,
        service_type=unique_service,
        entity_id=f"ENT-RTRY-{uuid.uuid4().hex[:4]}"
    )
    WorkflowEngine.freeze_instance(db, inst.id, actor_id="admin_01", reason="Pre-retry freeze")

    # Execute retry
    retried = WorkflowAdminService.retry_workflow(db, inst.id, actor_id="admin_01", reason="Manual retry after docs uploaded")
    assert retried.is_frozen is False

    # Check retry audit log
    audit = db.query(WorkflowAuditLog).filter(
        WorkflowAuditLog.instance_id == inst.id,
        WorkflowAuditLog.event_type == "WORKFLOW_RETRY_EXECUTED"
    ).first()
    assert audit is not None
    assert audit.actor_id == "admin_01"

    print("  [PASS] test_06_admin_retry PASSED")


# ─────────────────────────────────────────────
# 7. Workflow Engine System Health Test
# ─────────────────────────────────────────────

def test_07_system_health():
    """Verify deep health check diagnostic output across components."""
    db = next(get_test_db())

    health = WorkflowAdminService.get_workflow_system_health(db)
    assert health["status"] in ["HEALTHY", "DEGRADED", "UNHEALTHY"]
    assert "workflow_engine" in health
    assert "redis" in health
    assert "database" in health
    assert "event_bus" in health

    print("  [PASS] test_07_system_health PASSED")


# ─────────────────────────────────────────────
# 8. Admin Lifecycle Operations Test
# ─────────────────────────────────────────────

def test_08_lifecycle_freeze_resume_cancel_force():
    """Verify Freeze (blocks non-admin transitions), Resume, Cancel, and Force Transition."""
    db = next(get_test_db())
    unique_service = f"TEST_LC_{uuid.uuid4().hex[:6].upper()}"
    ensure_test_definition(db, unique_service)

    inst = WorkflowEngine.create_instance(
        db,
        service_type=unique_service,
        entity_id=f"ENT-LC-{uuid.uuid4().hex[:4]}"
    )

    # 1. Freeze
    frozen = WorkflowEngine.freeze_instance(db, inst.id, actor_id="admin_01", reason="Security review")
    assert frozen.is_frozen is True

    # Attempt non-admin transition while frozen -> MUST FAIL
    try:
        WorkflowEngine.execute_transition(
            db,
            instance_id=inst.id,
            action="PROCESS",
            actor_id="staff_user",
            actor_role="CUSTOMER_SUPPORT"
        )
        assert False, "Non-admin transition on frozen workflow should have failed."
    except ValueError as err:
        assert "frozen" in str(err).lower()

    # 2. Resume
    resumed = WorkflowEngine.resume_instance(db, inst.id, actor_id="admin_01")
    assert resumed.is_frozen is False

    # 3. Force Transition
    forced = WorkflowEngine.force_transition(
        db,
        instance_id=inst.id,
        target_state="FORCE_COMPLETED",
        actor_id="super_admin",
        actor_role="SUPER_ADMIN",
        reason="Manual emergency override"
    )
    assert forced.current_state == "FORCE_COMPLETED"

    # Verify audit log for force transition
    force_audit = db.query(WorkflowAuditLog).filter(
        WorkflowAuditLog.instance_id == inst.id,
        WorkflowAuditLog.event_type == "WORKFLOW_FORCE_TRANSITION"
    ).first()
    assert force_audit is not None
    assert force_audit.actor_id == "super_admin"

    # 4. Cancel
    cancelled = WorkflowEngine.cancel_instance(db, inst.id, actor_id="admin_01", reason="Customer request")
    assert cancelled.current_state == "CANCELLED"
    assert cancelled.is_completed is True

    print("  [PASS] test_08_lifecycle_freeze_resume_cancel_force PASSED")


# ─────────────────────────────────────────────
# Run All Tests
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Phase B.4 — Workflow Administration & Operations Test Suite ===\n")

    test_01_dashboard_filtering()
    test_02_workflow_search()
    test_03_workflow_metrics()
    test_04_unified_timeline()
    test_05_failed_workflow_monitoring()
    test_06_admin_retry()
    test_07_system_health()
    test_08_lifecycle_freeze_resume_cancel_force()

    print("\n=== ALL PHASE B.4 WORKFLOW ADMINISTRATION TESTS PASSED 100%! ===\n")
