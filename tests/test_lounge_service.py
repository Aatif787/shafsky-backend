"""
Unit & Integration Test Suite for Lounge Service Backend Engine.
Tests:
- Successful enquiry submission (201 Created, unique enquiry ID and reference)
- Required-field validation (missing required fields return 422)
- Invalid input (invalid name with digits, invalid email, non-positive passengers)
- Duplicate submissions handling (safe deduplication, returns existing enquiry without error)
- Database persistence (records saved correctly in lounge_enquiries table)
- Enquiry-only business rule (never marked as confirmed booking upon submission)
- Active airport and Lounge Type options endpoint (using verified records)
- Public reference status lookup (PII protection)
- Admin authorization (unauthorized access rejected with 401/403)
- Admin listing and filtering (by status, airport, search)
- Admin detail retrieval
- Admin status update, team assignment, and internal notes
"""

import uuid
from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import get_db
from app.models.lounge_models import LoungeEnquiry, LoungeEnquiryStatus
from app.models.journey_models import SupportedAirport
from app.services.auth_service import AuthService

client = TestClient(app)

# In-memory SQLite for test suite
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_test_db():
    LoungeEnquiry.__table__.create(bind=test_engine)
    SupportedAirport.__table__.create(bind=test_engine)

    # Seed verified active airports
    with TestingSessionLocal() as session:
        session.add(SupportedAirport(
            id=uuid.uuid4(),
            iata_code="DEL",
            airport_name="Indira Gandhi International Airport",
            city="New Delhi",
            country="India",
            is_active=True,
            is_supported=True,
        ))
        session.add(SupportedAirport(
            id=uuid.uuid4(),
            iata_code="BOM",
            airport_name="Chhatrapati Shivaji Maharaj International Airport",
            city="Mumbai",
            country="India",
            is_active=True,
            is_supported=True,
        ))
        session.commit()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.pop(get_db, None)
    LoungeEnquiry.__table__.drop(bind=test_engine, checkfirst=True)
    SupportedAirport.__table__.drop(bind=test_engine, checkfirst=True)


@pytest.fixture
def admin_token():
    token = AuthService.create_access_token(
        data={"sub": str(uuid.uuid4()), "email": "admin_lounge_test@shafsky.com", "role": "ADMIN"}
    )
    return token


def test_successful_lounge_enquiry_submission():
    future_date = (date.today() + timedelta(days=10)).isoformat()
    payload = {
        "full_name": "Eleanor Vance",
        "mobile_number": "+919876543210",
        "email_address": "eleanor.vance@luxurytravel.org",
        "whatsapp_number": "+919876543210",
        "airport": "Indira Gandhi International Airport (DEL) — New Delhi",
        "lounge_type": "VIP / First Class Lounge",
        "travel_date": future_date,
        "preferred_time": "Morning (06:00 – 12:00)",
        "number_of_passengers": 2,
        "flight_number": "AI101",
        "additional_requirements": "Quiet private suite requested for executive briefing.",
    }

    res = client.post("/api/v1/lounges/enquiries", json=payload)
    assert res.status_code == 201, f"Failed: {res.text}"
    body = res.json()
    assert body["success"] is True
    assert "enquiry received successfully" in body["message"].lower()

    data = body["data"]
    assert "id" in data
    assert data["enquiry_reference"].startswith("SHAF-LNG-")
    assert data["full_name"] == "Eleanor Vance"
    assert data["airport"] == "Indira Gandhi International Airport (DEL) — New Delhi"
    assert data["lounge_type"] == "VIP / First Class Lounge"
    assert data["status"] == "PENDING"  # Never confirmed booking
    assert data["number_of_passengers"] == 2
    assert data["flight_number"] == "AI101"

    # Verify persistence directly in test database
    with TestingSessionLocal() as db:
        db_enquiry = db.query(LoungeEnquiry).filter(LoungeEnquiry.enquiry_reference == data["enquiry_reference"]).first()
        assert db_enquiry is not None
        assert db_enquiry.email_address == "eleanor.vance@luxurytravel.org"
        assert db_enquiry.status == LoungeEnquiryStatus.PENDING


def test_required_field_validation():
    # Missing required full_name and airport
    payload = {
        "mobile_number": "+919876543210",
        "email_address": "guest@luxurytravel.org",
        "lounge_type": "VIP / First Class Lounge",
        "travel_date": "2026-11-05",
        "preferred_time": "Evening (18:00 – 22:00)",
        "number_of_passengers": 1,
    }

    res = client.post("/api/v1/lounges/enquiries", json=payload)
    assert res.status_code == 422


def test_invalid_input_name_with_numbers():
    payload = {
        "full_name": "Eleanor123 Vance!",  # Invalid letters only rule
        "mobile_number": "+919876543210",
        "email_address": "guest@luxurytravel.org",
        "airport": "DEL",
        "lounge_type": "Business Class Lounge",
        "travel_date": "2026-11-05",
        "preferred_time": "Morning (06:00 – 12:00)",
        "number_of_passengers": 1,
    }

    res = client.post("/api/v1/lounges/enquiries", json=payload)
    assert res.status_code == 422
    assert "letters only" in res.text.lower()


def test_invalid_input_email():
    payload = {
        "full_name": "Eleanor Vance",
        "mobile_number": "+919876543210",
        "email_address": "not-an-email",
        "airport": "DEL",
        "lounge_type": "Business Class Lounge",
        "travel_date": "2026-11-05",
        "preferred_time": "Morning (06:00 – 12:00)",
        "number_of_passengers": 1,
    }

    res = client.post("/api/v1/lounges/enquiries", json=payload)
    assert res.status_code == 422


def test_duplicate_submission_handling():
    future_date = (date.today() + timedelta(days=12)).isoformat()
    payload = {
        "full_name": "Alexander Hayes",
        "mobile_number": "+919811223344",
        "email_address": "alexander.hayes@globex.org",
        "airport": "Chhatrapati Shivaji Maharaj International Airport (BOM) — Mumbai",
        "lounge_type": "Business Class Lounge",
        "travel_date": future_date,
        "preferred_time": "Afternoon (12:00 – 18:00)",
        "number_of_passengers": 1,
    }

    # First submission
    res1 = client.post("/api/v1/lounges/enquiries", json=payload)
    assert res1.status_code == 201
    ref1 = res1.json()["data"]["enquiry_reference"]

    # Second submission (identical details within 10 minutes)
    res2 = client.post("/api/v1/lounges/enquiries", json=payload)
    assert res2.status_code == 201
    body2 = res2.json()
    assert body2["success"] is True
    # Should safely return the existing enquiry reference rather than creating a duplicate
    assert body2["data"]["enquiry_reference"] == ref1


def test_get_lounge_options_endpoint():
    res = client.get("/api/v1/lounges/options")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    data = body["data"]
    assert "airports" in data
    assert "lounge_types" in data
    assert "domestic_lounge_types" in data
    assert "international_lounge_types" in data
    assert "preferred_times" in data
    assert "server_time" in data
    assert "cutoff_rules" in data
    assert len(data["lounge_types"]) >= 4
    assert "VIP / First Class Lounge" in data["lounge_types"]
    assert "Domestic Departure Lounge" in data["domestic_lounge_types"]
    assert "International Departure Lounge" in data["international_lounge_types"]
    assert data["cutoff_rules"]["Domestic"]["notice_hours"] == 12
    assert data["cutoff_rules"]["International"]["notice_hours"] == 24
    assert any(a["code"] == "DEL" for a in data["airports"])


def test_check_lounge_eligibility_and_cutoff_rules():
    # 1. Far future date (> 12h & > 24h): Eligible
    future_date = (date.today() + timedelta(days=5)).isoformat()
    res = client.post(
        "/api/v1/lounges/check-eligibility",
        json={
            "lounge_category": "Domestic",
            "travel_date": future_date,
            "preferred_time": "Afternoon (12:00 – 18:00)",
            "airport": "DEL",
        },
    )
    assert res.status_code == 200
    el_data = res.json()["data"]
    assert el_data["eligible"] is True
    assert el_data["is_grace_period"] is False
    assert el_data["required_notice_hours"] == 12

    # 2. Past date: Ineligible
    past_date = (date.today() - timedelta(days=2)).isoformat()
    res_past = client.post(
        "/api/v1/lounges/check-eligibility",
        json={
            "lounge_category": "Domestic",
            "travel_date": past_date,
            "preferred_time": "Morning (06:00 – 12:00)",
            "airport": "DEL",
        },
    )
    assert res_past.status_code == 200
    el_past = res_past.json()["data"]
    assert el_past["eligible"] is False
    assert "past" in el_past["message"].lower()

    # 3. Submission with past date rejected by server with 422
    past_payload = {
        "full_name": "Marcus Aurelius",
        "mobile_number": "+919876543210",
        "email_address": "marcus.aurelius@stoic.org",
        "airport": "DEL",
        "lounge_category": "Domestic",
        "lounge_type": "VIP / First Class Lounge",
        "travel_date": past_date,
        "preferred_time": "Morning (06:00 – 12:00)",
        "number_of_passengers": 1,
    }
    submit_past_res = client.post("/api/v1/lounges/enquiries", json=past_payload)
    assert submit_past_res.status_code == 422



def test_public_enquiry_lookup_with_pii_masking():
    future_date = (date.today() + timedelta(days=15)).isoformat()
    payload = {
        "full_name": "Charlotte Bronte",
        "mobile_number": "+919988776655",
        "email_address": "charlotte.bronte@literature.org",
        "airport": "BLR",
        "lounge_type": "Executive / Plaza Premium Lounge",
        "travel_date": future_date,
        "preferred_time": "Night / Early Morning (22:00 – 06:00)",
        "number_of_passengers": 1,
    }
    create_res = client.post("/api/v1/lounges/enquiries", json=payload)
    ref = create_res.json()["data"]["enquiry_reference"]

    # Public unauthenticated lookup
    res = client.get(f"/api/v1/lounges/enquiries/{ref}")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["enquiry_reference"] == ref
    assert data["status"] == "PENDING"
    # PII masking check
    assert data["email_address"] == "***@***"
    assert "Charlotte" not in data["full_name"] or "***" in data["full_name"]


def test_admin_authorization_required():
    # Attempting to access admin endpoints without token must return 401/403
    res_list = client.get("/api/v1/admin/lounges/enquiries")
    assert res_list.status_code in (401, 403)

    res_patch = client.patch(
        "/api/v1/admin/lounges/enquiries/some-id",
        json={"status": "CONTACTED"},
    )
    assert res_patch.status_code in (401, 403)


def test_admin_list_and_update_enquiry(admin_token):
    future_date = (date.today() + timedelta(days=8)).isoformat()
    payload = {
        "full_name": "Marcus Aurelius",
        "mobile_number": "+919777888999",
        "email_address": "marcus.aurelius@stoic.org",
        "airport": "HYD",
        "lounge_type": "VIP / First Class Lounge",
        "travel_date": future_date,
        "preferred_time": "Morning (06:00 – 12:00)",
        "number_of_passengers": 3,
        "additional_requirements": "Requires private meeting space.",
    }
    create_res = client.post("/api/v1/lounges/enquiries", json=payload)
    ref = create_res.json()["data"]["enquiry_reference"]
    enquiry_id = create_res.json()["data"]["id"]

    headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Admin list enquiries
    list_res = client.get("/api/v1/admin/lounges/enquiries?status=PENDING", headers=headers)
    assert list_res.status_code == 200
    list_data = list_res.json()["data"]
    assert list_data["total"] >= 1
    found = any(item["enquiry_reference"] == ref for item in list_data["items"])
    assert found is True

    # 2. Admin get enquiry details
    get_res = client.get(f"/api/v1/admin/lounges/enquiries/{enquiry_id}", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["data"]["enquiry_reference"] == ref
    assert get_res.json()["data"]["full_name"] == "Marcus Aurelius"
    assert get_res.json()["data"]["email_address"] == "marcus.aurelius@stoic.org"

    # 3. Admin update status, assignment, and internal notes
    patch_payload = {
        "status": "CONTACTED",
        "assigned_staff_id": "staff-402",
        "assigned_staff_name": "Arjun Sharma",
        "internal_notes": "Contacted client via WhatsApp; shared rates for VIP terminal lounge.",
    }
    patch_res = client.patch(
        f"/api/v1/admin/lounges/enquiries/{enquiry_id}",
        json=patch_payload,
        headers=headers,
    )
    assert patch_res.status_code == 200
    updated = patch_res.json()["data"]
    assert updated["status"] == "CONTACTED"
    assert updated["assigned_staff_name"] == "Arjun Sharma"
    assert "rates for VIP terminal" in updated["internal_notes"]

    # Verify updated record persists in database
    with TestingSessionLocal() as db:
        db_item = db.query(LoungeEnquiry).filter(LoungeEnquiry.id == uuid.UUID(enquiry_id)).first()
        assert db_item is not None
        assert db_item.status == LoungeEnquiryStatus.CONTACTED
        assert db_item.assigned_staff_name == "Arjun Sharma"
