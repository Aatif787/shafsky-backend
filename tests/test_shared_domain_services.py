"""
Production Test Suite for Phase B.5 — Shared Domain Services.

Covers:
1.  Assignment lifecycle (assign → reassign → complete)
2.  Assignment workload tracking
3.  Assignment history immutability
4.  Timeline chronological ordering
5.  Timeline comment creation
6.  Note create with visibility filtering
7.  Note edit history (revisions)
8.  Attachment registration and access control
9.  Attachment soft-delete
10. SLA definition and deadline calculation
11. SLA breach detection
12. SLA escalation and resolution
13. Search with filters and pagination
"""

import sys
import os
import uuid
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal, engine, Base
import app.models.shared_domain  # Ensure shared domain models are loaded

from app.services.assignment_service import AssignmentService
from app.services.timeline_service import TimelineService
from app.services.notes_service import NotesService
from app.services.attachment_service import AttachmentService
from app.services.sla_service import SLAService
from app.services.search_service import SearchService


def get_test_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─────────────────────────────────────────────
# 1. Assignment Lifecycle
# ─────────────────────────────────────────────

def test_01_assignment_lifecycle():
    """Verify assign → reassign → complete lifecycle."""
    db = next(get_test_db())
    entity_type = "BOOKING"
    entity_id = str(uuid.uuid4())
    staff_1 = uuid.uuid4()
    staff_2 = uuid.uuid4()

    # Assign
    a1 = AssignmentService.assign(db, entity_type, entity_id, staff_1, assigned_by="mgr_01", role_type="GREETER")
    assert a1.status == "ASSIGNED"
    assert a1.staff_id == staff_1
    assert a1.role_type == "GREETER"

    # Reassign
    a2 = AssignmentService.reassign(db, a1.id, staff_2, reassigned_by="mgr_01", reason="Shift change")
    assert a2.staff_id == staff_2
    assert a2.status == "ASSIGNED"

    # Verify old assignment is REASSIGNED
    db.expire_all()
    from app.models.shared_domain import Assignment
    old_a = db.query(Assignment).filter(Assignment.id == a1.id).first()
    assert old_a.status == "REASSIGNED"

    # Complete new assignment
    a3 = AssignmentService.complete(db, a2.id, completed_by="staff_02")
    assert a3.status == "COMPLETED"

    print("  [PASS] test_01_assignment_lifecycle PASSED")


# ─────────────────────────────────────────────
# 2. Assignment Workload Tracking
# ─────────────────────────────────────────────

def test_02_assignment_workload():
    """Verify workload counting for staff."""
    db = next(get_test_db())
    staff_id = uuid.uuid4()

    for i in range(3):
        AssignmentService.assign(db, "CASE", str(uuid.uuid4()), staff_id, role_type="HANDLER")

    result = AssignmentService.get_workload(db, staff_id)
    assert result["active_count"] == 3
    assert len(result["assignments"]) == 3

    print("  [PASS] test_02_assignment_workload PASSED")


# ─────────────────────────────────────────────
# 3. Assignment History Immutability
# ─────────────────────────────────────────────

def test_03_assignment_history():
    """Verify immutable history records for assignment actions."""
    db = next(get_test_db())
    staff_id = uuid.uuid4()
    entity_id = str(uuid.uuid4())

    a = AssignmentService.assign(db, "BOOKING", entity_id, staff_id)
    AssignmentService.release(db, a.id, released_by="mgr_release", reason="No longer needed")

    history = AssignmentService.get_history(db, a.id)
    assert len(history) >= 2
    assert history[0].action == "ASSIGN"
    assert history[0].to_status == "ASSIGNED"
    assert history[1].action == "RELEASE"
    assert history[1].to_status == "RELEASED"
    assert history[1].reason == "No longer needed"

    print("  [PASS] test_03_assignment_history PASSED")


# ─────────────────────────────────────────────
# 4. Timeline Chronological Ordering
# ─────────────────────────────────────────────

def test_04_timeline_ordering():
    """Verify timeline entries are ordered chronologically."""
    db = next(get_test_db())
    entity_type = "BOOKING"
    entity_id = str(uuid.uuid4())

    TimelineService.add_entry(db, entity_type, entity_id, "CREATED", "Booking created")
    TimelineService.add_entry(db, entity_type, entity_id, "ASSIGNED", "Staff assigned")
    TimelineService.add_entry(db, entity_type, entity_id, "COMPLETED", "Service completed")

    result = TimelineService.get_timeline(db, entity_type, entity_id, sort="asc")
    data = result["data"]
    assert len(data) == 3
    assert result["total"] == 3

    # Verify ascending order
    for i in range(len(data) - 1):
        assert data[i].created_at <= data[i + 1].created_at

    # Verify descending order
    result_desc = TimelineService.get_timeline(db, entity_type, entity_id, sort="desc")
    data_desc = result_desc["data"]
    for i in range(len(data_desc) - 1):
        assert data_desc[i].created_at >= data_desc[i + 1].created_at

    print("  [PASS] test_04_timeline_ordering PASSED")


# ─────────────────────────────────────────────
# 5. Timeline Comment Creation
# ─────────────────────────────────────────────

def test_05_timeline_comment():
    """Verify timeline comment creation."""
    db = next(get_test_db())
    entity_type = "CASE"
    entity_id = str(uuid.uuid4())

    comment = TimelineService.add_comment(
        db, entity_type, entity_id, actor_id="agent_01", content="Passenger needs wheelchair assistance"
    )
    assert comment.event_type == "COMMENT"
    assert comment.details["content"] == "Passenger needs wheelchair assistance"
    assert comment.actor_id == "agent_01"

    print("  [PASS] test_05_timeline_comment PASSED")


# ─────────────────────────────────────────────
# 6. Note Visibility Filtering
# ─────────────────────────────────────────────

def test_06_note_visibility():
    """Verify note creation with INTERNAL and CUSTOMER visibility filtering."""
    db = next(get_test_db())
    entity_type = "BOOKING"
    entity_id = str(uuid.uuid4())

    NotesService.create(db, entity_type, entity_id, "Internal ops note", visibility="INTERNAL", author_id="ops_01")
    NotesService.create(db, entity_type, entity_id, "Welcome note for customer", visibility="CUSTOMER", author_id="ops_01")
    NotesService.create(db, entity_type, entity_id, "Another internal note", visibility="INTERNAL", author_id="ops_02")

    # All notes
    all_notes = NotesService.get_notes(db, entity_type, entity_id)
    assert all_notes["total"] == 3

    # Internal only
    internal = NotesService.get_notes(db, entity_type, entity_id, visibility_filter="INTERNAL")
    assert internal["total"] == 2

    # Customer only
    customer = NotesService.get_notes(db, entity_type, entity_id, visibility_filter="CUSTOMER")
    assert customer["total"] == 1
    assert customer["data"][0].content == "Welcome note for customer"

    print("  [PASS] test_06_note_visibility PASSED")


# ─────────────────────────────────────────────
# 7. Note Edit History (Revisions)
# ─────────────────────────────────────────────

def test_07_note_revisions():
    """Verify immutable note edit history."""
    db = next(get_test_db())
    entity_type = "CASE"
    entity_id = str(uuid.uuid4())

    note = NotesService.create(db, entity_type, entity_id, "Version 1 content", author_id="author_01")
    assert note.content == "Version 1 content"

    # First update
    NotesService.update(db, note.id, "Version 2 content", editor_id="editor_01")

    # Second update
    NotesService.update(db, note.id, "Version 3 content", editor_id="editor_02")

    revisions = NotesService.get_revisions(db, note.id)
    assert len(revisions) == 3
    assert revisions[0].revision_number == 1
    assert revisions[0].content_snapshot == "Version 1 content"
    assert revisions[1].revision_number == 2
    assert revisions[1].content_snapshot == "Version 2 content"
    assert revisions[2].revision_number == 3
    assert revisions[2].content_snapshot == "Version 3 content"
    assert revisions[2].edited_by == "editor_02"

    print("  [PASS] test_07_note_revisions PASSED")


# ─────────────────────────────────────────────
# 8. Attachment Access Control
# ─────────────────────────────────────────────

def test_08_attachment_access_control():
    """Verify attachment registration and tier-based access control."""
    db = next(get_test_db())
    entity_type = "BOOKING"
    entity_id = str(uuid.uuid4())

    att = AttachmentService.register(
        db, entity_type, entity_id,
        filename="passport_scan.pdf",
        storage_path="/storage/docs/passport_scan.pdf",
        file_size=1024000,
        mime_type="application/pdf",
        category="DOCUMENT",
        uploaded_by="ops_01",
        access_level="STAFF",
    )
    assert att.filename == "passport_scan.pdf"
    assert att.access_level == "STAFF"

    # Admin can access STAFF-level
    result_admin = AttachmentService.check_access(db, att.id, "ADMIN")
    assert result_admin["allowed"] is True

    # Staff can access STAFF-level
    result_staff = AttachmentService.check_access(db, att.id, "OPERATIONS_MANAGER")
    assert result_staff["allowed"] is True

    # Customer CANNOT access STAFF-level
    result_customer = AttachmentService.check_access(db, att.id, "CUSTOMER")
    assert result_customer["allowed"] is False

    # PUBLIC attachment — everyone can access
    att_pub = AttachmentService.register(
        db, entity_type, entity_id,
        filename="brochure.pdf",
        storage_path="/storage/public/brochure.pdf",
        access_level="PUBLIC",
    )
    result_pub = AttachmentService.check_access(db, att_pub.id, "CUSTOMER")
    assert result_pub["allowed"] is True

    print("  [PASS] test_08_attachment_access_control PASSED")


# ─────────────────────────────────────────────
# 9. Attachment Soft-Delete
# ─────────────────────────────────────────────

def test_09_attachment_soft_delete():
    """Verify attachment soft-deletion."""
    db = next(get_test_db())
    entity_type = "CASE"
    entity_id = str(uuid.uuid4())

    att = AttachmentService.register(
        db, entity_type, entity_id,
        filename="report.xlsx",
        storage_path="/storage/reports/report.xlsx",
        category="REPORT",
    )

    # Verify visible
    visible = AttachmentService.get_attachments(db, entity_type, entity_id)
    assert len(visible) == 1

    # Soft-delete
    deleted = AttachmentService.delete(db, att.id, deleted_by="admin_01")
    assert deleted.is_deleted is True

    # Verify hidden
    hidden = AttachmentService.get_attachments(db, entity_type, entity_id)
    assert len(hidden) == 0

    # Verify access check fails for deleted
    access = AttachmentService.check_access(db, att.id, "ADMIN")
    assert access["allowed"] is False

    print("  [PASS] test_09_attachment_soft_delete PASSED")


# ─────────────────────────────────────────────
# 10. SLA Definition and Deadline Calculation
# ─────────────────────────────────────────────

def test_10_sla_deadline_calculation():
    """Verify SLA definition creation and deadline calculation."""
    db = next(get_test_db())
    svc_type = f"SLA_SVC_{uuid.uuid4().hex[:6]}"

    sla_def = SLAService.create_definition(
        db,
        service_type=svc_type,
        priority="HIGH",
        response_time_minutes=30,
        resolution_time_minutes=120,
    )
    assert sla_def.service_type == svc_type.upper()
    assert sla_def.resolution_time_minutes == 120

    entity_id = str(uuid.uuid4())
    before = datetime.now(timezone.utc)
    sla_inst = SLAService.start_sla(db, "BOOKING", entity_id, svc_type, "HIGH", started_by="ops_01")
    after = datetime.now(timezone.utc)

    assert sla_inst.status == "ACTIVE"
    assert sla_inst.started_by == "ops_01"

    # Deadline should be ~120 minutes from start
    expected_min = before + timedelta(minutes=120)
    expected_max = after + timedelta(minutes=120)
    assert expected_min <= sla_inst.deadline_at <= expected_max

    print("  [PASS] test_10_sla_deadline_calculation PASSED")


# ─────────────────────────────────────────────
# 11. SLA Breach Detection
# ─────────────────────────────────────────────

def test_11_sla_breach_detection():
    """Verify SLA breach detection when deadline is passed."""
    db = next(get_test_db())
    svc_type = f"SLA_SVC_{uuid.uuid4().hex[:6]}"

    SLAService.create_definition(db, svc_type, "URGENT", response_time_minutes=5, resolution_time_minutes=1)

    entity_id = str(uuid.uuid4())
    sla_inst = SLAService.start_sla(db, "CASE", entity_id, svc_type, "URGENT")

    # Manually set deadline to the past to simulate breach
    from app.models.shared_domain import SLAInstance
    inst = db.query(SLAInstance).filter(SLAInstance.id == sla_inst.id).first()
    inst.deadline_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()

    # Check breach
    checked = SLAService.check_breach(db, sla_inst.id)
    assert checked.status == "BREACHED"
    assert checked.breached_at is not None

    print("  [PASS] test_11_sla_breach_detection PASSED")


# ─────────────────────────────────────────────
# 12. SLA Escalation and Resolution
# ─────────────────────────────────────────────

def test_12_sla_escalation_and_resolution():
    """Verify SLA escalation and resolution workflow."""
    db = next(get_test_db())
    svc_type = f"SLA_SVC_{uuid.uuid4().hex[:6]}"

    SLAService.create_definition(db, svc_type, "NORMAL", response_time_minutes=60, resolution_time_minutes=480)

    entity_id = str(uuid.uuid4())
    sla_inst = SLAService.start_sla(db, "BOOKING", entity_id, svc_type, "NORMAL")
    assert sla_inst.status == "ACTIVE"

    # Escalate
    escalated = SLAService.escalate(db, sla_inst.id, escalated_by="mgr_01", reason="VIP customer")
    assert escalated.status == "ESCALATED"
    assert escalated.escalated_by == "mgr_01"
    assert escalated.escalation_reason == "VIP customer"
    assert escalated.escalated_at is not None

    # Resolve
    resolved = SLAService.resolve_sla(db, sla_inst.id, resolved_by="agent_01")
    assert resolved.status == "RESOLVED"
    assert resolved.resolved_at is not None
    assert resolved.resolved_by == "agent_01"

    print("  [PASS] test_12_sla_escalation_and_resolution PASSED")


# ─────────────────────────────────────────────
# 13. Search with Filters and Pagination
# ─────────────────────────────────────────────

def test_13_search_filters():
    """Verify global search with entity type filters and pagination."""
    db = next(get_test_db())
    tag = uuid.uuid4().hex[:8]
    entity_type = "BOOKING"
    entity_id = str(uuid.uuid4())

    # Create searchable entries
    TimelineService.add_entry(db, entity_type, entity_id, "CREATED", f"Booking created {tag}")
    NotesService.create(db, entity_type, entity_id, f"VIP note {tag}", author_id="ops_01")
    AttachmentService.register(
        db, entity_type, entity_id,
        filename=f"invoice_{tag}.pdf",
        storage_path=f"/storage/{tag}.pdf",
    )

    # Search by tag
    result = SearchService.search(db, query=tag, limit=10)
    assert result["total"] >= 3

    # Search with entity_type filter
    result_filtered = SearchService.search(db, query=tag, entity_types=[entity_type], limit=10)
    assert result_filtered["total"] >= 3

    # Pagination
    result_page = SearchService.search(db, query=tag, limit=1, offset=0)
    assert len(result_page["results"]) <= 1
    assert result_page["total"] >= 3

    print("  [PASS] test_13_search_filters PASSED")


# ─────────────────────────────────────────────
# Run All Tests
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Phase B.5 — Shared Domain Services Test Suite ===\n")

    test_01_assignment_lifecycle()
    test_02_assignment_workload()
    test_03_assignment_history()
    test_04_timeline_ordering()
    test_05_timeline_comment()
    test_06_note_visibility()
    test_07_note_revisions()
    test_08_attachment_access_control()
    test_09_attachment_soft_delete()
    test_10_sla_deadline_calculation()
    test_11_sla_breach_detection()
    test_12_sla_escalation_and_resolution()
    test_13_search_filters()

    print("\n=== ALL PHASE B.5 SHARED DOMAIN SERVICES TESTS PASSED 100%! ===\n")
