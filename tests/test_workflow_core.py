"""
Production Test Suite for Phase B.1 - Workflow Engine Core.

Covers:
1. Create Workflow Definition & Version-pinned Instance
2. Valid Transition Execution
3. Invalid Transition Action Rejection
4. Terminal State Protection
5. Unauthorized Role Rejection
6. Guard Failure Rejection
7. Immutable History Persistence
8. Audit Log Persistence
9. Version Pinning across Workflow Definition updates
"""

import sys
import os
import uuid
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal, engine, Base
from app.models.schema import WorkflowDefinition, WorkflowInstance, WorkflowHistory, WorkflowAuditLog
from app.workflow.engine import WorkflowEngine
from app.workflow.guards import evaluate_guards


def get_test_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_01_create_workflow_and_instance():
    """Verify workflow definition registration and version-pinned instance creation."""
    db = next(get_test_db())
    service_type = f"CORE_SVC_{uuid.uuid4().hex[:6]}"

    # Create Definition
    wf_def = WorkflowDefinition(
        service_type=service_type,
        name="Core Workflow Test",
        version=1,
        initial_state="INIT",
        states_config={
            "INIT": {
                "allowed_actions": {
                    "SUBMIT": {"target": "PENDING", "roles": ["CUSTOMER"], "guards": {"required_fields": ["details"]}}
                }
            },
            "PENDING": {
                "allowed_actions": {
                    "APPROVE": {"target": "APPROVED", "roles": ["ADMIN"]}
                }
            },
            "APPROVED": {"terminal": True}
        },
        is_active=True
    )
    db.add(wf_def)
    db.commit()

    # Create Instance
    instance = WorkflowEngine.create_instance(
        db,
        service_type=service_type,
        entity_id="ENT-1001",
        actor_id="usr_01",
        initial_context={"key": "val"}
    )

    assert instance is not None
    assert instance.service_type == service_type
    assert instance.current_state == "INIT"
    assert instance.workflow_definition_id == wf_def.id
    assert instance.is_completed is False


def test_02_valid_transition_and_history_persistence():
    """Verify valid transition execution and immutable history/audit persistence."""
    db = next(get_test_db())
    service_type = f"CORE_SVC_{uuid.uuid4().hex[:6]}"

    wf_def = WorkflowDefinition(
        service_type=service_type,
        name="Core Valid Test",
        version=1,
        initial_state="INIT",
        states_config={
            "INIT": {
                "allowed_actions": {
                    "PROCEED": {"target": "STEP2", "roles": ["CUSTOMER"]}
                }
            },
            "STEP2": {"terminal": True}
        },
        is_active=True
    )
    db.add(wf_def)
    db.commit()

    instance = WorkflowEngine.create_instance(db, service_type, "ENT-1002")
    assert instance.current_state == "INIT"

    # Execute Valid Transition
    instance = WorkflowEngine.execute_transition(
        db,
        instance.id,
        action="PROCEED",
        actor_id="usr_02",
        actor_role="CUSTOMER",
        payload={"note": "going to step 2"}
    )

    assert instance.current_state == "STEP2"
    assert instance.is_completed is True

    # History Persistence Check
    history = db.query(WorkflowHistory).filter(WorkflowHistory.instance_id == instance.id).all()
    assert len(history) == 2  # INITIALIZE + PROCEED
    assert history[1].from_state == "INIT"
    assert history[1].to_state == "STEP2"
    assert history[1].action == "PROCEED"

    # Audit Persistence Check
    audits = db.query(WorkflowAuditLog).filter(WorkflowAuditLog.instance_id == instance.id).all()
    assert len(audits) >= 2


def test_03_invalid_action_and_terminal_state_protection():
    """Verify invalid action rejection and terminal state protection."""
    db = next(get_test_db())
    service_type = f"CORE_SVC_{uuid.uuid4().hex[:6]}"

    wf_def = WorkflowDefinition(
        service_type=service_type,
        name="Terminal Test",
        version=1,
        initial_state="START",
        states_config={
            "START": {
                "allowed_actions": {
                    "END": {"target": "FINAL", "roles": ["ADMIN"]}
                }
            },
            "FINAL": {"terminal": True}
        },
        is_active=True
    )
    db.add(wf_def)
    db.commit()

    instance = WorkflowEngine.create_instance(db, service_type, "ENT-1003")

    # Invalid Action Rejection
    with pytest.raises(ValueError) as exc:
        WorkflowEngine.execute_transition(db, instance.id, "NON_EXISTENT", actor_role="ADMIN")
    assert "Invalid action 'NON_EXISTENT'" in str(exc.value)

    # Transition to Terminal
    instance = WorkflowEngine.execute_transition(db, instance.id, "END", actor_role="ADMIN")
    assert instance.current_state == "FINAL"
    assert instance.is_completed is True

    # Terminal State Protection Rejection
    with pytest.raises(ValueError) as exc2:
        WorkflowEngine.execute_transition(db, instance.id, "END", actor_role="ADMIN")
    assert "Cannot transition from terminal state 'FINAL'" in str(exc2.value)


def test_04_unauthorized_role_rejection():
    """Verify unauthorized role transition rejection."""
    db = next(get_test_db())
    service_type = f"CORE_SVC_{uuid.uuid4().hex[:6]}"

    wf_def = WorkflowDefinition(
        service_type=service_type,
        name="Auth Test",
        version=1,
        initial_state="PENDING",
        states_config={
            "PENDING": {
                "allowed_actions": {
                    "APPROVE": {"target": "APPROVED", "roles": ["ADMIN", "OPERATIONS_MANAGER"]}
                }
            },
            "APPROVED": {"terminal": True}
        },
        is_active=True
    )
    db.add(wf_def)
    db.commit()

    instance = WorkflowEngine.create_instance(db, service_type, "ENT-1004")

    # CUSTOMER role attempting ADMIN action -> Rejected
    with pytest.raises(ValueError) as exc:
        WorkflowEngine.execute_transition(db, instance.id, "APPROVE", actor_role="CUSTOMER")
    assert "is not authorized to execute action" in str(exc.value)


def test_05_guard_failure_rejection():
    """Verify transition guard condition evaluation failure rejection."""
    db = next(get_test_db())
    service_type = f"CORE_SVC_{uuid.uuid4().hex[:6]}"

    wf_def = WorkflowDefinition(
        service_type=service_type,
        name="Guard Test",
        version=1,
        initial_state="DRAFT",
        states_config={
            "DRAFT": {
                "allowed_actions": {
                    "SUBMIT": {
                        "target": "SUBMITTED",
                        "roles": ["CUSTOMER"],
                        "guards": {
                            "required_fields": ["document_url"],
                            "min_value": {"amount": 50.0}
                        }
                    }
                }
            },
            "SUBMITTED": {"terminal": True}
        },
        is_active=True
    )
    db.add(wf_def)
    db.commit()

    instance = WorkflowEngine.create_instance(db, service_type, "ENT-1005")

    # Missing document_url guard failure
    with pytest.raises(ValueError) as exc:
        WorkflowEngine.execute_transition(
            db,
            instance.id,
            "SUBMIT",
            actor_role="CUSTOMER",
            payload={"amount": 100.0}
        )
    assert "Guard conditions failed" in str(exc.value)

    # min_value amount guard failure
    with pytest.raises(ValueError) as exc2:
        WorkflowEngine.execute_transition(
            db,
            instance.id,
            "SUBMIT",
            actor_role="CUSTOMER",
            payload={"document_url": "https://doc.pdf", "amount": 10.0}
        )
    assert "Guard conditions failed" in str(exc2.value)

    # Valid payload passing all guards -> Succeeds
    instance = WorkflowEngine.execute_transition(
        db,
        instance.id,
        "SUBMIT",
        actor_role="CUSTOMER",
        payload={"document_url": "https://doc.pdf", "amount": 75.0}
    )
    assert instance.current_state == "SUBMITTED"


def test_06_version_pinning():
    """Verify that workflow instances remain pinned to their creation version definition."""
    db = next(get_test_db())
    service_type = f"CORE_SVC_{uuid.uuid4().hex[:6]}"

    # Definition Version 1
    def1 = WorkflowDefinition(
        service_type=service_type,
        name="Versioned Workflow",
        version=1,
        initial_state="STATE_V1",
        states_config={
            "STATE_V1": {
                "allowed_actions": {
                    "NEXT": {"target": "FINAL_V1", "roles": ["CUSTOMER"]}
                }
            },
            "FINAL_V1": {"terminal": True}
        },
        is_active=True
    )
    db.add(def1)
    db.commit()

    # Create Instance 1 (Pinned to Version 1)
    instance1 = WorkflowEngine.create_instance(db, service_type, "ENT-V1")
    assert instance1.workflow_definition_id == def1.id

    # Create Definition Version 2 (Deactivate v1, Activate v2)
    def1.is_active = False
    def2 = WorkflowDefinition(
        service_type=service_type,
        name="Versioned Workflow",
        version=2,
        initial_state="STATE_V2",
        states_config={
            "STATE_V2": {
                "allowed_actions": {
                    "NEXT": {"target": "FINAL_V2", "roles": ["CUSTOMER"]}
                }
            },
            "FINAL_V2": {"terminal": True}
        },
        is_active=True
    )
    db.add(def2)
    db.commit()

    # Create Instance 2 (Pinned to Version 2)
    instance2 = WorkflowEngine.create_instance(db, service_type, "ENT-V2")
    assert instance2.workflow_definition_id == def2.id
    assert instance2.current_state == "STATE_V2"

    # Instance 1 transitions using Version 1 rules
    instance1 = WorkflowEngine.execute_transition(db, instance1.id, "NEXT", actor_role="CUSTOMER")
    assert instance1.current_state == "FINAL_V1"

    # Instance 2 transitions using Version 2 rules
    instance2 = WorkflowEngine.execute_transition(db, instance2.id, "NEXT", actor_role="CUSTOMER")
    assert instance2.current_state == "FINAL_V2"


if __name__ == "__main__":
    test_01_create_workflow_and_instance()
    test_02_valid_transition_and_history_persistence()
    test_03_invalid_action_and_terminal_state_protection()
    test_04_unauthorized_role_rejection()
    test_05_guard_failure_rejection()
    test_06_version_pinning()
    print("ALL PHASE B.1 WORKFLOW CORE TESTS PASSED 100%!")
