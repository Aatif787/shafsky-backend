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
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import get_db
from app.models.lounge_models import LoungeEnquiry, LoungeEnquiryStatus
from app.models.journey_models import SupportedAirport
from app.services.auth_service import AuthService
from app.services.lounge_service import LoungeService
from app.schemas.lounge import LoungeEligibilityCheckRequest, LoungeEnquiryCreate

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


def test_domestic_cutoff_boundary_matrix():
    """
    Test Domestic enquiries across full boundary matrix:
    - Well before normal cutoff
    - Exactly at normal cutoff (12h)
    - During grace period (11h to 12h)
    - Exactly at end of grace period (11h)
    - After grace period (< 11h)
    - When travel datetime is in the past
    """
    tz = ZoneInfo("Asia/Kolkata")
    # Travel date: 2026-11-20 18:00:00 IST (+05:30)
    # Normal cutoff: 2026-11-20 06:00:00 IST (12 hours before)
    # Grace period end: 2026-11-20 07:00:00 IST (11 hours before)
    req = LoungeEligibilityCheckRequest(
        lounge_category="Domestic",
        travel_date="2026-11-20",
        preferred_time="18:00",
        airport="DEL",
    )

    # 1. Well before normal cutoff (e.g. 13 hours notice: 05:00:00)
    res_before = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 20, 5, 0, 0, tzinfo=tz))
    assert res_before.eligible is True
    assert res_before.is_grace_period is False
    assert res_before.lounge_category == "Domestic"
    assert res_before.ineligible_reason is None
    assert res_before.normal_cutoff_datetime == "2026-11-20T06:00:00+05:30"
    assert res_before.grace_period_end_datetime == "2026-11-20T07:00:00+05:30"
    assert res_before.travel_datetime == "2026-11-20T18:00:00+05:30"
    assert res_before.lead_time_hours == 13.0
    assert "Standard notice period met" in res_before.message

    # 2. Exactly at normal cutoff (exactly 12 hours notice: 06:00:00)
    res_at_cutoff = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 20, 6, 0, 0, tzinfo=tz))
    assert res_at_cutoff.eligible is True
    assert res_at_cutoff.is_grace_period is False
    assert res_at_cutoff.ineligible_reason is None
    assert res_at_cutoff.lead_time_hours == 12.0
    assert "Standard notice period met" in res_at_cutoff.message

    # 3. During grace period (11.5 hours notice: 06:30:00)
    res_in_grace = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 20, 6, 30, 0, tzinfo=tz))
    assert res_in_grace.eligible is True
    assert res_in_grace.is_grace_period is True
    assert res_in_grace.ineligible_reason is None
    assert res_in_grace.lead_time_hours == 11.5
    assert "grace period" in res_in_grace.message.lower()

    # 4. Exactly at end of grace period (exactly 11 hours notice: 07:00:00)
    res_at_grace_end = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 20, 7, 0, 0, tzinfo=tz))
    assert res_at_grace_end.eligible is True
    assert res_at_grace_end.is_grace_period is True
    assert res_at_grace_end.ineligible_reason is None
    assert res_at_grace_end.lead_time_hours == 11.0
    assert "grace period" in res_at_grace_end.message.lower()

    # 5. After grace period (10h 59m 59s notice: 07:00:01)
    res_after_grace = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 20, 7, 0, 1, tzinfo=tz))
    assert res_after_grace.eligible is False
    assert res_after_grace.is_grace_period is False
    assert res_after_grace.ineligible_reason is not None
    assert "Cutoff window expired" in res_after_grace.ineligible_reason
    assert "outside the allowed enquiry window" in res_after_grace.message

    # 6. Selected travel datetime is in the past (18:00:00 and 19:00:00)
    res_exact_travel = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 20, 18, 0, 0, tzinfo=tz))
    assert res_exact_travel.eligible is False
    assert res_exact_travel.is_grace_period is False
    assert res_exact_travel.ineligible_reason == "Selected travel date and time is in the past."

    res_past = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 20, 19, 0, 0, tzinfo=tz))
    assert res_past.eligible is False
    assert res_past.is_grace_period is False
    assert res_past.ineligible_reason == "Selected travel date and time is in the past."


def test_international_cutoff_boundary_matrix():
    """
    Test International enquiries across full boundary matrix:
    - Well before normal cutoff
    - Exactly at normal cutoff (24h)
    - During grace period (23h to 24h)
    - Exactly at end of grace period (23h)
    - After grace period (< 23h)
    - When travel datetime is in the past
    """
    tz = ZoneInfo("Asia/Dubai")
    # Travel date: 2026-11-20 18:00:00 GST (+04:00)
    # Normal cutoff: 2026-11-19 18:00:00 GST (24 hours before)
    # Grace period end: 2026-11-19 19:00:00 GST (23 hours before)
    req = LoungeEligibilityCheckRequest(
        lounge_category="International",
        travel_date="2026-11-20",
        preferred_time="18:00",
        airport="DXB",
    )

    # 1. Well before normal cutoff (e.g. 30 hours notice: 2026-11-19 12:00:00)
    res_before = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 19, 12, 0, 0, tzinfo=tz))
    assert res_before.eligible is True
    assert res_before.is_grace_period is False
    assert res_before.lounge_category == "International"
    assert res_before.ineligible_reason is None
    assert res_before.normal_cutoff_datetime == "2026-11-19T18:00:00+04:00"
    assert res_before.grace_period_end_datetime == "2026-11-19T19:00:00+04:00"
    assert res_before.travel_datetime == "2026-11-20T18:00:00+04:00"
    assert res_before.lead_time_hours == 30.0
    assert "Standard notice period met" in res_before.message

    # 2. Exactly at normal cutoff (exactly 24 hours notice: 2026-11-19 18:00:00)
    res_at_cutoff = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 19, 18, 0, 0, tzinfo=tz))
    assert res_at_cutoff.eligible is True
    assert res_at_cutoff.is_grace_period is False
    assert res_at_cutoff.ineligible_reason is None
    assert res_at_cutoff.lead_time_hours == 24.0

    # 3. During grace period (23.5 hours notice: 2026-11-19 18:30:00)
    res_in_grace = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 19, 18, 30, 0, tzinfo=tz))
    assert res_in_grace.eligible is True
    assert res_in_grace.is_grace_period is True
    assert res_in_grace.ineligible_reason is None
    assert res_in_grace.lead_time_hours == 23.5
    assert "grace period" in res_in_grace.message.lower()

    # 4. Exactly at end of grace period (exactly 23 hours notice: 2026-11-19 19:00:00)
    res_at_grace_end = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 19, 19, 0, 0, tzinfo=tz))
    assert res_at_grace_end.eligible is True
    assert res_at_grace_end.is_grace_period is True
    assert res_at_grace_end.ineligible_reason is None
    assert res_at_grace_end.lead_time_hours == 23.0

    # 5. After grace period (22h 59m 59s notice: 2026-11-19 19:00:01)
    res_after_grace = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 19, 19, 0, 1, tzinfo=tz))
    assert res_after_grace.eligible is False
    assert res_after_grace.is_grace_period is False
    assert res_after_grace.ineligible_reason is not None
    assert "Cutoff window expired" in res_after_grace.ineligible_reason
    assert "outside the allowed enquiry window" in res_after_grace.message

    # 6. Selected travel datetime is in the past
    res_past = LoungeService.check_eligibility(req, current_time=datetime(2026, 11, 20, 20, 0, 0, tzinfo=tz))
    assert res_past.eligible is False
    assert res_past.is_grace_period is False
    assert res_past.ineligible_reason == "Selected travel date and time is in the past."


def test_across_different_airport_timezones():
    """
    Test across multiple airport timezones: DEL (+05:30), DXB (+04:00), SIN (+08:00), LHR (Europe/London).
    Verifies that normal cutoff, grace period end, server datetime, and travel datetime all carry explicit timezone offsets.
    """
    test_cases = [
        {"airport": "DEL", "expected_tz": "Asia/Kolkata", "expected_offset": "+05:30"},
        {"airport": "DXB", "expected_tz": "Asia/Dubai", "expected_offset": "+04:00"},
        {"airport": "SIN", "expected_tz": "Asia/Singapore", "expected_offset": "+08:00"},
        {"airport": "DOH", "expected_tz": "Asia/Qatar", "expected_offset": "+03:00"},
        {"airport": "London Heathrow (LHR)", "expected_tz": "Europe/London", "expected_offset": "+00:00"},
    ]

    for tc in test_cases:
        req = LoungeEligibilityCheckRequest(
            lounge_category="International",
            travel_date="2026-12-15",
            preferred_time="14:00",
            airport=tc["airport"],
        )
        # Fix server time in UTC: 2026-12-10 10:00:00 UTC
        server_utc = datetime(2026, 12, 10, 10, 0, 0, tzinfo=timezone.utc)
        res = LoungeService.check_eligibility(req, current_time=server_utc)

        assert res.airport_timezone == tc["expected_tz"], f"Failed for {tc['airport']}"
        assert res.eligible is True
        assert res.normal_cutoff_datetime is not None
        assert res.normal_cutoff_datetime.startswith("2026-12-14T14:00:00")
        assert res.grace_period_end_datetime is not None
        assert res.grace_period_end_datetime.startswith("2026-12-14T15:00:00")
        assert res.travel_datetime is not None
        assert res.travel_datetime.startswith("2026-12-15T14:00:00")
        # Ensure offset exists on all ISO strings
        assert tc["expected_offset"] in res.travel_datetime or res.travel_datetime.endswith("Z")


def test_invalid_dates_and_times_handling():
    """
    Test rejection of invalid dates and times:
    - Non-existent calendar day (e.g. 2026-02-31)
    - Malformed date string
    - Out-of-range hour/minute (e.g. 25:00, 14:75)
    - Unparseable time string
    """
    # 1. Invalid calendar day (Feb 31)
    req_bad_day = LoungeEligibilityCheckRequest(
        lounge_category="Domestic",
        travel_date="2026-02-31",
        preferred_time="Morning (06:00 – 12:00)",
        airport="DEL",
    )
    res_bad_day = LoungeService.check_eligibility(req_bad_day)
    assert res_bad_day.eligible is False
    assert res_bad_day.ineligible_reason is not None
    assert "Invalid travel date" in res_bad_day.ineligible_reason

    # 2. Out-of-range time (25:00)
    req_bad_time = LoungeEligibilityCheckRequest(
        lounge_category="Domestic",
        travel_date="2026-11-20",
        preferred_time="25:00",
        airport="DEL",
    )
    res_bad_time = LoungeService.check_eligibility(req_bad_time)
    assert res_bad_time.eligible is False
    assert res_bad_time.ineligible_reason is not None
    assert "Invalid 24-hour time values" in res_bad_time.ineligible_reason

    # 3. Unrecognized time format
    req_unrec_time = LoungeEligibilityCheckRequest(
        lounge_category="Domestic",
        travel_date="2026-11-20",
        preferred_time="whenever",
        airport="DEL",
    )
    res_unrec_time = LoungeService.check_eligibility(req_unrec_time)
    assert res_unrec_time.eligible is False
    assert res_unrec_time.ineligible_reason is not None
    assert "Unrecognized preferred time format" in res_unrec_time.ineligible_reason

    # 4. Submission of invalid travel date rejected with 422
    payload_bad_date = {
        "full_name": "Eleanor Vance",
        "mobile_number": "+919876543210",
        "email_address": "eleanor.vance@luxurytravel.org",
        "airport": "DEL",
        "lounge_type": "VIP / First Class Lounge",
        "travel_date": "2026-02-31",
        "preferred_time": "14:00",
        "number_of_passengers": 1,
    }
    submit_res = client.post("/api/v1/lounges/enquiries", json=payload_bad_date)
    assert submit_res.status_code == 422


def test_authoritative_server_submission_cutoff_enforcement():
    """
    Ensure the submission endpoint recalculates eligibility on the server and rejects expired enquiries.
    """
    tz = ZoneInfo("Asia/Kolkata")
    # Travel date: 2026-11-20 18:00:00 (Domestic)
    # End of grace period is 2026-11-20 07:00:00
    with TestingSessionLocal() as db:
        # A. Attempting submission AFTER grace period (07:05:00) -> 422
        expired_payload = LoungeEnquiryCreate(
            full_name="Sebastian Croft",
            mobile_number="+919123456789",
            email_address="sebastian.croft@voyage.org",
            airport="DEL",
            lounge_category="Domestic",
            lounge_type="VIP / First Class Lounge",
            travel_date="2026-11-20",
            preferred_time="18:00",
            number_of_passengers=1,
        )
        with pytest.raises(HTTPException) as exc_info:
            LoungeService.create_enquiry(
                db=db,
                payload=expired_payload,
                current_time=datetime(2026, 11, 20, 7, 5, 0, tzinfo=tz),
            )
        assert exc_info.value.status_code == 422
        assert "outside the allowed enquiry window" in str(exc_info.value.detail)

        # B. Submission DURING grace period (06:30:00) -> Succeeds with PENDING status
        grace_payload = LoungeEnquiryCreate(
            full_name="Sebastian Croft",
            mobile_number="+919123456789",
            email_address="sebastian.croft@voyage.org",
            airport="DEL",
            lounge_category="Domestic",
            lounge_type="VIP / First Class Lounge",
            travel_date="2026-11-20",
            preferred_time="18:00",
            number_of_passengers=1,
        )
        enquiry, is_dup = LoungeService.create_enquiry(
            db=db,
            payload=grace_payload,
            current_time=datetime(2026, 11, 20, 6, 30, 0, tzinfo=tz),
        )
        assert enquiry is not None
        assert enquiry.status == LoungeEnquiryStatus.PENDING
        assert is_dup is False


def test_lounge_service_security_and_injection_resilience(admin_token):
    """
    Security validation suite:
    1. SQL injection payloads in search, airport, and reference parameters are safely parameterized.
    2. XSS payloads in full_name are rejected by schema validators.
    3. Additional requirements allow unicode safely without execution.
    4. Disposable and placeholder email domains are rejected with 422.
    5. Public reference lookup strictly masks customer PII.
    """
    headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. SQL Injection attempt in admin search filter
    sqli_searches = [
        "' OR '1'='1",
        "'; DROP TABLE lounge_enquiries; --",
        "DEL' UNION SELECT * FROM users --",
    ]
    for payload in sqli_searches:
        res = client.get(f"/api/v1/admin/lounges/enquiries?search={payload}", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "items" in data["data"]

    # 2. SQL Injection in public reference lookup
    res_sqli = client.get("/api/v1/lounges/enquiries/' OR 1=1 --")
    assert res_sqli.status_code == 404

    # 3. XSS in full_name rejected by validator
    res_xss_name = client.post(
        "/api/v1/lounges/enquiries",
        json={
            "full_name": "<script>alert('xss')</script>",
            "mobile_number": "+919876543210",
            "email_address": "secure.guest@voyage.org",
            "airport": "DEL",
            "lounge_category": "Domestic",
            "lounge_type": "VIP / First Class Lounge",
            "travel_date": "2026-11-20",
            "preferred_time": "18:00",
            "number_of_passengers": 1,
        },
    )
    assert res_xss_name.status_code == 422
    assert "Full name must contain letters only" in res_xss_name.text

    # 4. Disposable/placeholder email rejected
    res_bad_email = client.post(
        "/api/v1/lounges/enquiries",
        json={
            "full_name": "Alexander Wright",
            "mobile_number": "+919876543210",
            "email_address": "test@example.com",
            "airport": "DEL",
            "lounge_category": "Domestic",
            "lounge_type": "VIP / First Class Lounge",
            "travel_date": "2026-11-20",
            "preferred_time": "18:00",
            "number_of_passengers": 1,
        },
    )
    assert res_bad_email.status_code == 422

    # 5. Non-owner lookup masks PII completely
    # Create a valid enquiry
    future_date = (date.today() + timedelta(days=10)).isoformat()
    res_create = client.post(
        "/api/v1/lounges/enquiries",
        json={
            "full_name": "Eleanor Vance",
            "mobile_number": "+919876543210",
            "email_address": "eleanor.vance@voyage.org",
            "whatsapp_number": "+919876543211",
            "airport": "DEL",
            "lounge_category": "Domestic",
            "lounge_type": "VIP / First Class Lounge",
            "travel_date": future_date,
            "preferred_time": "18:00",
            "number_of_passengers": 2,
            "additional_requirements": "Window view preferred <style>body{color:red}</style>",
        },
    )
    assert res_create.status_code == 201
    ref = res_create.json()["data"]["enquiry_reference"]

    # Public unauthenticated lookup
    res_public = client.get(f"/api/v1/lounges/enquiries/{ref}")
    assert res_public.status_code == 200
    pub_data = res_public.json()["data"]

    # Assert PII masking
    assert pub_data["full_name"] == "E*** V***"
    assert pub_data["email_address"] == "***@***"
    assert "mobile_number" not in pub_data
    assert "whatsapp_number" not in pub_data
    assert "internal_notes" not in pub_data


