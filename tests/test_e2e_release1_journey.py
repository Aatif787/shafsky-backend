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
    from sqlalchemy import select

    customer_email = f"e2e_customer_{uuid.uuid4().hex[:6]}@shafsky.com"
    db = SessionLocal()
    try:
        customer = UserAuth(
            email=customer_email,
            password_hash=AuthService.hash_password("E2ePass2026!"),
            role=Role.CUSTOMER,
            is_verified=True,
            is_active=True,
        )
        db.add(customer)
        db.flush()
        db.add(Profile(auth_id=customer.id, email=customer_email, role=Role.CUSTOMER, full_name="E2E Test Customer"))
        admin = db.scalar(select(UserAuth).where(UserAuth.email == "admin@shafskyaviation.com"))
        if not admin:
            admin = UserAuth(
                email="admin@shafskyaviation.com",
                password_hash=AuthService.hash_password("ShafskyAdmin2026!"),
                role=Role.SUPER_ADMIN,
                is_verified=True,
                is_active=True,
            )
            db.add(admin)
            db.flush()
            db.add(Profile(auth_id=admin.id, email=admin.email, role=Role.SUPER_ADMIN, full_name="Admin"))
        db.commit()
        customer_id = str(customer.id)
        admin_id = str(admin.id)
    finally:
        db.close()

    customer_token = AuthService.create_access_token({
        "sub": customer_email,
        "user_id": customer_id,
        "role": "CUSTOMER"
    })
    customer_headers = {"Authorization": f"Bearer {customer_token}"}

    admin_token = AuthService.create_access_token({
        "sub": "admin@shafskyaviation.com",
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
        "service_package": "STANDARD_MEET_GREET",
        "flight_detail": {
            "airline": "Emirates",
            "flight_number": "EK502",
            "departure_airport": "BOM",
            "arrival_airport": "DEL",
            "scheduled_time": "2026-09-20T10:00:00Z",
            "flight_type": "ARRIVAL",
        },
        "passengers": [
            {
                "full_name": "E2E Test Customer",
                "passport_number": "Z1234567",
                "is_primary": True,
            }
        ]
    }
    res_bk = client.post("/api/airport/bookings", json=booking_payload, headers=customer_headers)
    assert res_bk.status_code == 201, res_bk.text
    bk_data = res_bk.json()
    booking_id = bk_data["id"]
    # Workflow starts in DRAFT; create_booking syncs AirportBooking.status to the instance.
    assert bk_data["status"] == "DRAFT"
    assert bk_data["workflow_instance_id"] is not None

    # ── STEP 3: Upload Documents / Register Attachments ───────────────────────
    att_payload = {
        "entity_type": "AIRPORT_BOOKING",
        "entity_id": booking_id,
        "filename": "passport_scan.pdf",
        "storage_path": f"documents/{booking_id}/passport.pdf",
        "category": "PASSPORT",
        "access_level": "STAFF",
    }
    res_att = client.post("/api/shared/attachments", json=att_payload, headers=admin_headers)
    assert res_att.status_code == 201, res_att.text
    assert res_att.json()["id"] is not None

    res_att_list = client.get(
        f"/api/shared/attachments/entity/AIRPORT_BOOKING/{booking_id}",
        headers=admin_headers,
    )
    assert res_att_list.status_code == 200
    assert len(res_att_list.json()) >= 1

    # ── STEP 4: Workflow Started Verification ─────────────────────────────────
    res_single = client.get(f"/api/airport/bookings/{booking_id}", headers=admin_headers)
    assert res_single.status_code == 200
    assert res_single.json()["workflow_instance_id"] is not None

    # ── STEP 5: Staff Officer Assignment ──────────────────────────────────────
    assign_payload = {
        "entity_type": "AIRPORT_BOOKING",
        "entity_id": booking_id,
        "staff_id": admin_id,
        "role_type": "CONCIERGE",
        "notes": "Assigned primary duty officer",
    }
    res_assign = client.post("/api/shared/assignments", json=assign_payload, headers=admin_headers)
    assert res_assign.status_code == 201, res_assign.text
    assert res_assign.json()["id"] is not None

    # ── STEP 6: Internal Notes Added ──────────────────────────────────────────
    note_payload = {
        "entity_type": "AIRPORT_BOOKING",
        "entity_id": booking_id,
        "content": "Customer VIP status confirmed at airport lounge.",
        "visibility": "INTERNAL",
    }
    res_note = client.post("/api/shared/notes", json=note_payload, headers=admin_headers)
    assert res_note.status_code == 201, res_note.text
    assert res_note.json()["id"] is not None

    # ── STEP 7: Timeline Updates Verification ─────────────────────────────────
    res_timeline = client.get(f"/api/shared/timeline/AIRPORT_BOOKING/{booking_id}", headers=admin_headers)
    assert res_timeline.status_code == 200
    timeline_entries = res_timeline.json()["data"]
    assert len(timeline_entries) >= 1

    # ── STEP 8: Airport meet-and-assist workflow actions ──────────────────────
    def _transition(action: str) -> dict:
        res = client.post(
            f"/api/airport/bookings/{booking_id}/transition",
            json={"action": action, "payload": {}},
            headers=admin_headers,
        )
        assert res.status_code == 200, res.text
        return res.json()

    assert _transition("CONFIRM")["status"] == "BOOKED"
    assert _transition("ASSIGN_STAFF")["status"] == "STAFF_ASSIGNED"
    assert _transition("MEET_PASSENGER")["status"] == "PASSENGER_MET"
    assert _transition("START_ASSISTANCE")["status"] == "ASSISTANCE_IN_PROGRESS"

    # ── STEP 9: Customer Views Status ─────────────────────────────────────────
    res_cust_bk = client.get(f"/api/airport/bookings/{booking_id}", headers=customer_headers)
    assert res_cust_bk.status_code == 200
    assert res_cust_bk.json()["status"] == "ASSISTANCE_IN_PROGRESS"

    # ── STEP 10: Booking Completed ───────────────────────────────────────────
    assert _transition("COMPLETE")["status"] == "COMPLETED"
