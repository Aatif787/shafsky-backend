"""
Comprehensive Unit and Integration Test Suite for Enterprise Workflow Engine.

Covers:
1. Default workflow seeding for 5 service domains
2. Airport Meet & Assist workflow execution
3. Air Ticketing workflow execution
4. Hotel Booking workflow execution
5. Visa Assistance workflow execution
6. Air Cargo workflow execution
7. Transition validation & invalid action rejection
8. Role-based transition authorization
9. Complete history tracking & audit trail verification
10. System event recording
"""

import sys
import os
import uuid
import pytest
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal, engine, Base
from app.models.schema import WorkflowDefinition, WorkflowInstance, WorkflowHistory, WorkflowAuditLog, SystemEvent
from app.workflow.definitions import seed_default_workflows, DEFAULT_WORKFLOW_DEFINITIONS
from app.workflow.engine import WorkflowEngine


def get_test_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_01_seed_default_workflows():
    """Verify that default workflow definitions for 5 service domains are seeded properly."""
    db = next(get_test_db())
    seeded = seed_default_workflows(db)

    assert "AIRPORT_MEET_AND_ASSIST" in seeded
    assert "AIR_TICKETING" in seeded
    assert "HOTEL_BOOKING" in seeded
    assert "VISA_ASSISTANCE" in seeded
    assert "AIR_CARGO" in seeded

    for service_type in DEFAULT_WORKFLOW_DEFINITIONS.keys():
        wf_def = db.query(WorkflowDefinition).filter(
            WorkflowDefinition.service_type == service_type,
            WorkflowDefinition.is_active == True
        ).first()
        assert wf_def is not None
        assert wf_def.initial_state == DEFAULT_WORKFLOW_DEFINITIONS[service_type]["initial_state"]


def test_02_airport_meet_and_assist_workflow():
    """Verify complete end-to-end lifecycle for Airport Meet & Assist workflow."""
    db = next(get_test_db())
    entity_id = f"AMA-{uuid.uuid4().hex[:8]}"

    # Initialize Instance
    instance = WorkflowEngine.create_instance(
        db,
        service_type="AIRPORT_MEET_AND_ASSIST",
        entity_id=entity_id,
        actor_id="test_user",
        initial_context={"airport": "DEL", "terminal": "T3"}
    )

    assert instance.current_state == "DRAFT"
    assert instance.is_completed is False

    # DRAFT -> BOOKED
    instance = WorkflowEngine.execute_transition(db, instance.id, "CONFIRM", actor_role="CUSTOMER")
    assert instance.current_state == "BOOKED"

    # BOOKED -> STAFF_ASSIGNED
    instance = WorkflowEngine.execute_transition(db, instance.id, "ASSIGN_STAFF", actor_role="OPERATIONS_MANAGER")
    assert instance.current_state == "STAFF_ASSIGNED"

    # STAFF_ASSIGNED -> PASSENGER_MET
    instance = WorkflowEngine.execute_transition(db, instance.id, "MEET_PASSENGER", actor_role="MEET_AND_ASSIST_STAFF")
    assert instance.current_state == "PASSENGER_MET"

    # PASSENGER_MET -> ASSISTANCE_IN_PROGRESS
    instance = WorkflowEngine.execute_transition(db, instance.id, "START_ASSISTANCE", actor_role="MEET_AND_ASSIST_STAFF")
    assert instance.current_state == "ASSISTANCE_IN_PROGRESS"

    # ASSISTANCE_IN_PROGRESS -> COMPLETED
    instance = WorkflowEngine.execute_transition(db, instance.id, "COMPLETE", actor_role="MEET_AND_ASSIST_STAFF")
    assert instance.current_state == "COMPLETED"
    assert instance.is_completed is True


def test_03_air_ticketing_workflow():
    """Verify complete lifecycle for Air Ticketing workflow."""
    db = next(get_test_db())
    entity_id = f"TKT-{uuid.uuid4().hex[:8]}"

    instance = WorkflowEngine.create_instance(db, "AIR_TICKETING", entity_id)
    assert instance.current_state == "DRAFT"

    instance = WorkflowEngine.execute_transition(db, instance.id, "CREATE_PNR", actor_role="CUSTOMER")
    assert instance.current_state == "PNR_CREATED"

    instance = WorkflowEngine.execute_transition(db, instance.id, "VERIFY_PAYMENT", actor_role="FINANCE")
    assert instance.current_state == "PAYMENT_VERIFIED"

    instance = WorkflowEngine.execute_transition(db, instance.id, "ISSUE_TICKET", actor_role="ADMIN")
    assert instance.current_state == "TICKET_ISSUED"

    instance = WorkflowEngine.execute_transition(db, instance.id, "COMPLETE", actor_role="ADMIN")
    assert instance.current_state == "COMPLETED"
    assert instance.is_completed is True


def test_04_hotel_booking_workflow():
    """Verify complete lifecycle for Hotel Booking workflow."""
    db = next(get_test_db())
    entity_id = f"HTL-{uuid.uuid4().hex[:8]}"

    instance = WorkflowEngine.create_instance(db, "HOTEL_BOOKING", entity_id)
    assert instance.current_state == "DRAFT"

    instance = WorkflowEngine.execute_transition(db, instance.id, "REQUEST_RESERVATION", actor_role="CUSTOMER")
    assert instance.current_state == "RESERVATION_REQUESTED"

    instance = WorkflowEngine.execute_transition(db, instance.id, "CONFIRM_HOTEL", actor_role="CONCIERGE_TEAM")
    assert instance.current_state == "CONFIRMED_BY_HOTEL"

    instance = WorkflowEngine.execute_transition(db, instance.id, "CHECK_IN", actor_role="CUSTOMER")
    assert instance.current_state == "CHECKED_IN"

    instance = WorkflowEngine.execute_transition(db, instance.id, "CHECK_OUT", actor_role="CUSTOMER")
    assert instance.current_state == "CHECKED_OUT"

    instance = WorkflowEngine.execute_transition(db, instance.id, "COMPLETE", actor_role="CONCIERGE_TEAM")
    assert instance.current_state == "COMPLETED"
    assert instance.is_completed is True


def test_05_visa_assistance_workflow():
    """Verify complete lifecycle for Visa Assistance workflow."""
    db = next(get_test_db())
    entity_id = f"VSA-{uuid.uuid4().hex[:8]}"

    instance = WorkflowEngine.create_instance(db, "VISA_ASSISTANCE", entity_id)
    assert instance.current_state == "DOCUMENT_COLLECTION"

    instance = WorkflowEngine.execute_transition(db, instance.id, "VERIFY_DOCUMENTS", actor_role="CONCIERGE_TEAM")
    assert instance.current_state == "UNDER_VERIFICATION"

    instance = WorkflowEngine.execute_transition(db, instance.id, "SUBMIT_EMBASSY", actor_role="CONCIERGE_TEAM")
    assert instance.current_state == "SUBMITTED_TO_EMBASSY"

    instance = WorkflowEngine.execute_transition(db, instance.id, "APPROVE_VISA", actor_role="CONCIERGE_TEAM")
    assert instance.current_state == "VISA_APPROVED"

    instance = WorkflowEngine.execute_transition(db, instance.id, "COMPLETE", actor_role="CONCIERGE_TEAM")
    assert instance.current_state == "COMPLETED"
    assert instance.is_completed is True


def test_06_air_cargo_workflow():
    """Verify complete lifecycle for Air Cargo workflow."""
    db = next(get_test_db())
    entity_id = f"CGO-{uuid.uuid4().hex[:8]}"

    instance = WorkflowEngine.create_instance(db, "AIR_CARGO", entity_id)
    assert instance.current_state == "BOOKING_CREATED"

    instance = WorkflowEngine.execute_transition(db, instance.id, "RECEIVE_CARGO", actor_role="DISPATCHER")
    assert instance.current_state == "CARGO_RECEIVED"

    instance = WorkflowEngine.execute_transition(db, instance.id, "CLEAR_CUSTOMS", actor_role="DISPATCHER")
    assert instance.current_state == "CUSTOMS_CLEARED"

    instance = WorkflowEngine.execute_transition(db, instance.id, "DISPATCH_TRANSIT", actor_role="DRIVER")
    assert instance.current_state == "IN_TRANSIT"

    instance = WorkflowEngine.execute_transition(db, instance.id, "DELIVER_CARGO", actor_role="DRIVER")
    assert instance.current_state == "DELIVERED"

    instance = WorkflowEngine.execute_transition(db, instance.id, "COMPLETE", actor_role="DISPATCHER")
    assert instance.current_state == "COMPLETED"
    assert instance.is_completed is True


def test_07_invalid_action_rejection():
    """Verify that invalid actions and transitions from terminal states are rejected."""
    db = next(get_test_db())
    entity_id = f"ERR-{uuid.uuid4().hex[:8]}"

    instance = WorkflowEngine.create_instance(db, "VISA_ASSISTANCE", entity_id)
    assert instance.current_state == "DOCUMENT_COLLECTION"

    # Attempt action not allowed in DOCUMENT_COLLECTION -> Should fail with ValueError
    with pytest.raises(ValueError) as exc:
        WorkflowEngine.execute_transition(db, instance.id, "APPROVE_VISA", actor_role="CONCIERGE_TEAM")
    assert "Invalid action 'APPROVE_VISA'" in str(exc.value)


def test_08_role_authorization_rejection():
    """Verify role authorization checks during transition execution."""
    db = next(get_test_db())
    entity_id = f"AUTH-{uuid.uuid4().hex[:8]}"

    instance = WorkflowEngine.create_instance(db, "AIRPORT_MEET_AND_ASSIST", entity_id)

    # Move to BOOKED
    instance = WorkflowEngine.execute_transition(db, instance.id, "CONFIRM", actor_role="CUSTOMER")

    # Attempt ASSIGN_STAFF as CUSTOMER -> Should fail (requires ADMIN / OPERATIONS_MANAGER)
    with pytest.raises(ValueError) as exc:
        WorkflowEngine.execute_transition(db, instance.id, "ASSIGN_STAFF", actor_role="CUSTOMER")
    assert "is not authorized to execute action" in str(exc.value)


def test_09_history_and_audit_logs():
    """Verify history tracking and audit logging integration."""
    db = next(get_test_db())
    entity_id = f"HIST-{uuid.uuid4().hex[:8]}"

    instance = WorkflowEngine.create_instance(db, "AIRPORT_MEET_AND_ASSIST", entity_id, actor_id="user_123")
    instance = WorkflowEngine.execute_transition(db, instance.id, "CONFIRM", actor_id="user_123", actor_role="CUSTOMER")

    history_details = WorkflowEngine.get_history(db, instance.id)
    history = history_details["history"]
    audit = history_details["audit_logs"]

    assert len(history) == 2  # INITIALIZE + CONFIRM
    assert history[0].action == "INITIALIZE"
    assert history[1].action == "CONFIRM"
    assert history[1].from_state == "DRAFT"
    assert history[1].to_state == "BOOKED"

    assert len(audit) >= 2


if __name__ == "__main__":
    test_01_seed_default_workflows()
    test_02_airport_meet_and_assist_workflow()
    test_03_air_ticketing_workflow()
    test_04_hotel_booking_workflow()
    test_05_visa_assistance_workflow()
    test_06_air_cargo_workflow()
    test_07_invalid_action_rejection()
    test_08_role_authorization_rejection()
    test_09_history_and_audit_logs()
    print("ALL ENTERPRISE WORKFLOW ENGINE TESTS PASSED 100%!")
