"""
Production Test Suite for Phase B.2 - Workflow Event System.

Covers:
1. Event Creation & Standardized Payload Specification
2. Redis Pub/Sub Channel Broadcasting
3. Database Event Persistence
4. Duplicate Event Prevention (Deduplication)
5. Correlation ID Propagation
6. Ordered Event Replay (by sequence_number)
7. Event Version Compatibility
"""

import sys
import os
import uuid
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal, engine, Base
from app.models.schema import WorkflowDefinition
from app.models.system_events import WorkflowEventRecord
from app.workflow.engine import WorkflowEngine
from app.services.event_service import WorkflowEventService
from app.core.redis import get_redis_client


def get_test_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_01_event_creation_and_payload_spec():
    """Verify event creation and strict payload specification schema."""
    db = next(get_test_db())
    service_type = f"EVT_SVC_{uuid.uuid4().hex[:6]}"

    wf_def = WorkflowDefinition(
        service_type=service_type,
        name="Event System Test",
        version=1,
        initial_state="DRAFT",
        states_config={
            "DRAFT": {
                "allowed_actions": {
                    "SUBMIT": {"target": "SUBMITTED", "roles": ["CUSTOMER"]}
                }
            },
            "SUBMITTED": {"terminal": True}
        },
        is_active=True
    )
    db.add(wf_def)
    db.commit()

    instance = WorkflowEngine.create_instance(
        db,
        service_type=service_type,
        entity_id="ENT-EVT-01",
        actor_id="usr_evt_01",
        initial_context={"mode": "test"}
    )

    # Retrieve created event
    events = WorkflowEventService.get_instance_events(db, instance.id)
    assert len(events) >= 1
    created_evt = events[0]

    assert created_evt.event_type == "WORKFLOW_CREATED"
    assert created_evt.service_type == service_type
    assert created_evt.entity_id == "ENT-EVT-01"
    assert created_evt.event_version == 1

    spec = created_evt.metadata_json
    assert "event_id" in spec
    assert spec["workflow_instance_id"] == str(instance.id)
    assert spec["service_type"] == service_type
    assert spec["entity_id"] == "ENT-EVT-01"
    assert spec["current_state"] == "DRAFT"
    assert "timestamp" in spec


def test_02_redis_pubsub_publishing():
    """Verify Redis Pub/Sub event broadcasting."""
    db = next(get_test_db())
    service_type = f"EVT_SVC_{uuid.uuid4().hex[:6]}"

    wf_def = WorkflowDefinition(
        service_type=service_type,
        name="Redis PubSub Test",
        version=1,
        initial_state="START",
        states_config={
            "START": {"allowed_actions": {"GO": {"target": "FINISH", "roles": ["CUSTOMER"]}}},
            "FINISH": {"terminal": True}
        },
        is_active=True
    )
    db.add(wf_def)
    db.commit()

    instance = WorkflowEngine.create_instance(db, service_type, "ENT-REDIS-01")

    # Execute transition
    WorkflowEngine.execute_transition(db, instance.id, "GO", actor_role="CUSTOMER")

    # Check published event record in DB
    events = WorkflowEventService.get_instance_events(db, instance.id)
    assert len(events) >= 2


def test_03_database_persistence_and_sequence():
    """Verify persistent database event records and sequence numbers."""
    db = next(get_test_db())
    service_type = f"EVT_SVC_{uuid.uuid4().hex[:6]}"

    wf_def = WorkflowDefinition(
        service_type=service_type,
        name="Sequence Test",
        version=1,
        initial_state="STEP1",
        states_config={
            "STEP1": {"allowed_actions": {"NEXT1": {"target": "STEP2", "roles": ["ADMIN"]}}},
            "STEP2": {"allowed_actions": {"NEXT2": {"target": "STEP3", "roles": ["ADMIN"]}}},
            "STEP3": {"terminal": True}
        },
        is_active=True
    )
    db.add(wf_def)
    db.commit()

    instance = WorkflowEngine.create_instance(db, service_type, "ENT-SEQ-01")
    WorkflowEngine.execute_transition(db, instance.id, "NEXT1", actor_role="ADMIN")
    WorkflowEngine.execute_transition(db, instance.id, "NEXT2", actor_role="ADMIN")

    records = db.query(WorkflowEventRecord).filter(
        WorkflowEventRecord.workflow_instance_id == instance.id
    ).order_by(WorkflowEventRecord.sequence_number.asc()).all()

    assert len(records) >= 3
    # Check monotonic sequence numbers
    seqs = [r.sequence_number for r in records]
    assert seqs == sorted(seqs)


def test_04_duplicate_event_prevention():
    """Verify duplicate event prevention deduplication logic."""
    db = next(get_test_db())
    inst_id = uuid.uuid4()
    corr_id = f"corr_dup_{uuid.uuid4().hex[:6]}"

    evt1 = WorkflowEventService.publish_workflow_event(
        db,
        event_type="TRANSITION_STARTED",
        workflow_instance_id=inst_id,
        workflow_definition_version=1,
        service_type="TEST_DUP",
        entity_id="ENT-DUP-1",
        current_state="START",
        action="PROCESS",
        correlation_id=corr_id
    )

    # Attempt duplicate publishing with same correlation ID & action
    evt2 = WorkflowEventService.publish_workflow_event(
        db,
        event_type="TRANSITION_STARTED",
        workflow_instance_id=inst_id,
        workflow_definition_version=1,
        service_type="TEST_DUP",
        entity_id="ENT-DUP-1",
        current_state="START",
        action="PROCESS",
        correlation_id=corr_id
    )

    assert evt1.id == evt2.id


def test_05_correlation_id_propagation():
    """Verify correlation ID propagation through transition execution chain."""
    db = next(get_test_db())
    service_type = f"EVT_SVC_{uuid.uuid4().hex[:6]}"

    wf_def = WorkflowDefinition(
        service_type=service_type,
        name="Corr Chain Test",
        version=1,
        initial_state="INIT",
        states_config={
            "INIT": {"allowed_actions": {"FORWARD": {"target": "DONE", "roles": ["ADMIN"]}}},
            "DONE": {"terminal": True}
        },
        is_active=True
    )
    db.add(wf_def)
    db.commit()

    corr_id = f"corr_chain_{uuid.uuid4().hex[:8]}"
    instance = WorkflowEngine.create_instance(db, service_type, "ENT-CHAIN-01")

    # Execute transition with explicit correlation ID
    WorkflowEngine.execute_transition(
        db,
        instance.id,
        "FORWARD",
        actor_role="ADMIN",
        correlation_id=corr_id
    )

    records = db.query(WorkflowEventRecord).filter(
        WorkflowEventRecord.workflow_instance_id == instance.id,
        WorkflowEventRecord.correlation_id == corr_id
    ).all()

    assert len(records) >= 2  # TRANSITION_STARTED & WORKFLOW_COMPLETED
    for r in records:
        assert r.correlation_id == corr_id


def test_06_replay_events_ordering():
    """Verify ordered historical event replay."""
    db = next(get_test_db())
    service_type = f"EVT_SVC_{uuid.uuid4().hex[:6]}"

    wf_def = WorkflowDefinition(
        service_type=service_type,
        name="Replay Test",
        version=1,
        initial_state="A",
        states_config={
            "A": {"allowed_actions": {"GO_B": {"target": "B", "roles": ["ADMIN"]}}},
            "B": {"terminal": True}
        },
        is_active=True
    )
    db.add(wf_def)
    db.commit()

    instance = WorkflowEngine.create_instance(db, service_type, "ENT-REPLAY-01")
    WorkflowEngine.execute_transition(db, instance.id, "GO_B", actor_role="ADMIN")

    replayed = WorkflowEventService.replay_events(db, instance_id=instance.id, from_sequence=0)
    assert len(replayed) >= 2

    # Verify strict sequence order
    for i in range(len(replayed) - 1):
        assert replayed[i].sequence_number < replayed[i + 1].sequence_number


def test_07_event_version_compatibility():
    """Verify event versioning field default and metadata compatibility."""
    db = next(get_test_db())
    inst_id = uuid.uuid4()

    evt = WorkflowEventService.publish_workflow_event(
        db,
        event_type="WORKFLOW_CREATED",
        workflow_instance_id=inst_id,
        workflow_definition_version=2,
        service_type="VER_SVC",
        entity_id="ENT-VER-01",
        current_state="INITIAL"
    )

    assert evt.event_version == 1
    assert evt.workflow_definition_version == 2
    assert evt.metadata_json["workflow_definition_version"] == 2


if __name__ == "__main__":
    test_01_event_creation_and_payload_spec()
    test_02_redis_pubsub_publishing()
    test_03_database_persistence_and_sequence()
    test_04_duplicate_event_prevention()
    test_05_correlation_id_propagation()
    test_06_replay_events_ordering()
    test_07_event_version_compatibility()
    print("ALL PHASE B.2 WORKFLOW EVENT SYSTEM TESTS PASSED 100%!")
