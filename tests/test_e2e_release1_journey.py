"""
End-to-End Release 1 Verification Test Suite.
Traces the complete 10-step Customer & Operations lifecycle journey.
"""

import pytest
import uuid
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.schema import UserAuth, Profile, Role, Booking, BookingStatus
from app.services.auth_service import AuthService

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


def test_full_release1_customer_journey():
    # ── STEP 1: Register / Login / Auth Context ──────────────────────────────
    customer_email = f"e2e_customer_{uuid.uuid4().hex[:6]}@shafsky.com"
    customer_id = str(uuid.uuid4())
    customer_token = AuthService.create_access_token({
        "sub": customer_email,
        "user_id": customer_id,
        "role": "CUSTOMER"
    })
    customer_headers = {"Authorization": f"Bearer {customer_token}"}

    admin_email = "admin@shafskyaviation.com"
    admin_id = str(uuid.uuid4())
    admin_token = AuthService.create_access_token({
        "sub": admin_email,
        "user_id": admin_id,
        "role": "SUPER_ADMIN"
    })
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Verify Profile Endpoint
    res_prof = client.get("/api/auth/profile", headers=customer_headers)
    assert res_prof.status_code == 200
    assert res_prof.json()["success"] is True

    # ── STEP 2: Create Airport Booking ────────────────────────────────────────
    booking_payload = {
        "contact_name": "E2E Test Customer",
        "contact_email": customer_email,
        "contact_phone": "+919876543210",
        "service_code": "MEET_GREET",
        "flight_number": "EK-502",
        "airline": "Emirates",
        "departure_airport": "BOM",
        "arrival_airport": "DXB",
        "depart_date": "2026-09-20T10:00:00Z",
        "passengers": [
            {
                "full_name": "E2E Test Customer",
                "passenger_type": "ADULT",
                "passport_number": "Z1234567"
            }
        ]
    }
    res_bk = client.post("/api/airport/bookings", json=booking_payload, headers=customer_headers)
    assert res_bk.status_code == 201, res_bk.text
    bk_data = res_bk.json()["data"]
    booking_id = bk_data["id"]
    assert bk_data["status"] == "NEW_BOOKING"
    assert bk_data["workflow_instance_id"] is not None

    # ── STEP 3: Upload Documents / Register Attachments ───────────────────────
    att_payload = {
        "entity_type": "AIRPORT_BOOKING",
        "entity_id": booking_id,
        "filename": "passport_scan.pdf",
        "storage_path": f"documents/{booking_id}/passport.pdf",
        "category": "PASSPORT",
        "access_level": "RESTRICTED"
    }
    res_att = client.post("/api/shared/attachments/register", json=att_payload, headers=customer_headers)
    assert res_att.status_code == 200, res_att.text
    assert res_att.json()["success"] is True

    # Fetch registered attachments
    res_att_list = client.get(f"/api/shared/attachments/AIRPORT_BOOKING/{booking_id}", headers=customer_headers)
    assert res_att_list.status_code == 200
    assert len(res_att_list.json()["data"]) >= 1

    # ── STEP 4: Workflow Started Verification ─────────────────────────────────
    res_single = client.get(f"/api/airport/bookings/{booking_id}", headers=admin_headers)
    assert res_single.status_code == 200
    assert res_single.json()["data"]["workflow_instance_id"] is not None

    # ── STEP 5: Staff Officer Assignment ──────────────────────────────────────
    assign_payload = {
        "booking_id": booking_id,
        "staff_user_id": admin_id,
        "role_type": "CONCIERGE",
        "notes": "Assigned primary duty officer"
    }
    res_assign = client.post("/api/shared/assignments", json=assign_payload, headers=admin_headers)
    assert res_assign.status_code == 200, res_assign.text
    assert res_assign.json()["success"] is True

    # ── STEP 6: Internal Notes Added ──────────────────────────────────────────
    note_payload = {
        "entity_type": "AIRPORT_BOOKING",
        "entity_id": booking_id,
        "content": "Customer VIP status confirmed at airport lounge.",
        "is_internal": True
    }
    res_note = client.post("/api/shared/notes", json=note_payload, headers=admin_headers)
    assert res_note.status_code == 200, res_note.text
    assert res_note.json()["success"] is True

    # ── STEP 7: Timeline Updates Verification ─────────────────────────────────
    res_timeline = client.get(f"/api/shared/timeline/AIRPORT_BOOKING/{booking_id}", headers=admin_headers)
    assert res_timeline.status_code == 200
    timeline_entries = res_timeline.json()["data"]
    assert len(timeline_entries) >= 1

    # ── STEP 8: Booking Status Transition to UNDER_REVIEW & CONFIRMED ────────
    trans_payload1 = {
        "target_status": "UNDER_REVIEW",
        "reason": "Officer reviewing flight details"
    }
    res_trans1 = client.post(f"/api/airport/bookings/{booking_id}/transition", json=trans_payload1, headers=admin_headers)
    assert res_trans1.status_code == 200, res_trans1.text
    assert res_trans1.json()["data"]["status"] == "UNDER_REVIEW"

    trans_payload2 = {
        "target_status": "CONFIRMED",
        "reason": "Ground escort arranged"
    }
    res_trans2 = client.post(f"/api/airport/bookings/{booking_id}/transition", json=trans_payload2, headers=admin_headers)
    assert res_trans2.status_code == 200, res_trans2.text
    assert res_trans2.json()["data"]["status"] == "CONFIRMED"

    # ── STEP 9: Customer Views Status ─────────────────────────────────────────
    res_cust_bk = client.get(f"/api/airport/bookings/{booking_id}", headers=customer_headers)
    assert res_cust_bk.status_code == 200
    assert res_cust_bk.json()["data"]["status"] == "CONFIRMED"

    # ── STEP 10: Booking Completed ───────────────────────────────────────────
    trans_payload3 = {
        "target_status": "COMPLETED",
        "reason": "Service delivered successfully"
    }
    res_trans3 = client.post(f"/api/airport/bookings/{booking_id}/transition", json=trans_payload3, headers=admin_headers)
    assert res_trans3.status_code == 200, res_trans3.text
    assert res_trans3.json()["data"]["status"] == "COMPLETED"
