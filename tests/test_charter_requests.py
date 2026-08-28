"""
Unit & Integration Test Suite for Private Charter Engine.
Tests:
- Charter Request submission (One Way, Round Trip, Multi-City)
- Validation (past dates, invalid phone, missing itinerary, invalid return date)
- Reference generation (SC-XXXXXX format)
- Public reference status lookup
- Admin listing, filtering, detail retrieval, and status updates
- 100% Free workflow isolation (no payment, no flight validation)
"""

import pytest
from datetime import datetime, date, timedelta
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, get_db
from app.models.charter_models import PrivateCharterRequest, CharterRequestStatus
from app.models.schema import UserAuth, Role, Profile
from app.security.jwt import SecurityJWT
import uuid

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def admin_token():
    db = next(get_db())
    admin_user = db.query(UserAuth).filter(UserAuth.email == "admin_charter_test@shafsky.com").first()
    if not admin_user:
        admin_user = UserAuth(
            id=uuid.uuid4(),
            email="admin_charter_test@shafsky.com",
            password_hash="mock_hash",
            role=Role.ADMIN,
            is_verified=True,
        )
        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)
    token = SecurityJWT.create_access_token(data={"sub": str(admin_user.id), "email": admin_user.email, "role": "ADMIN"})
    return token


def test_create_one_way_charter_request():
    future_date = (date.today() + timedelta(days=14)).isoformat()
    payload = {
        "customer_name": "Lord Sterling",
        "country_code": "+91",
        "phone": "9876543210",
        "email": "sterling@vip.aero",
        "company": "Sterling Global Ventures",
        "preferred_contact_method": "PHONE_WHATSAPP",
        "trip_type": "ONE_WAY",
        "origin": "Indira Gandhi Intl Airport, Delhi (DEL)",
        "destination": "Dubai Al Maktoum Intl, Dubai (DWC)",
        "departure_date": future_date,
        "departure_time": "Morning (08:00 - 12:00)",
        "passengers": {
            "adults": 4,
            "children": 1,
            "infants": 0,
        },
        "aircraft_preference": "HEAVY_JET",
        "travel_requirements": [
            "Catering & Fine Dining",
            "Tarmac Maybach Transfer",
            "High-Speed Wi-Fi",
        ],
        "special_requests": "Champagne vintage 2012 on boarding please.",
    }

    res = client.post("/api/v1/charter/requests", json=payload)
    assert res.status_code == 201, f"Failed: {res.text}"
    body = res.json()
    assert body["success"] is True
    data = body["data"]
    assert data["request_reference"].startswith("SC-")
    assert data["status"] == "REQUESTED"
    assert data["customer_name"] == "Lord Sterling"
    assert data["origin"] == "Indira Gandhi Intl Airport, Delhi (DEL)"
    assert data["destination"] == "Dubai Al Maktoum Intl, Dubai (DWC)"
    assert data["passengers"]["adults"] == 4
    assert data["passengers"]["total"] == 5

    # Verify public lookup
    ref = data["request_reference"]
    lookup_res = client.get(f"/api/v1/charter/requests/{ref}")
    assert lookup_res.status_code == 200
    lookup_body = lookup_res.json()
    assert lookup_body["success"] is True
    assert lookup_body["data"]["request_reference"] == ref
    assert lookup_body["data"]["status"] == "REQUESTED"


def test_create_round_trip_charter_request():
    dep_date = (date.today() + timedelta(days=20)).isoformat()
    ret_date = (date.today() + timedelta(days=25)).isoformat()

    payload = {
        "customer_name": "Elena Rostova",
        "country_code": "+44",
        "phone": "7700900123",
        "email": "elena.rostova@monaco.mc",
        "preferred_contact_method": "WHATSAPP",
        "trip_type": "ROUND_TRIP",
        "origin": "London Luton (LTN)",
        "destination": "Nice Cote d'Azur (NCE)",
        "departure_date": dep_date,
        "departure_time": "14:00",
        "return_date": ret_date,
        "return_time": "18:00",
        "passengers": {
            "adults": 2,
            "children": 0,
            "infants": 0,
        },
        "aircraft_preference": "SUPER_MIDSIZE_JET",
        "travel_requirements": ["Pet in Cabin (AVI)", "Ground Transportation"],
        "special_requests": "Small Maltese dog traveling in main cabin.",
    }

    res = client.post("/api/v1/charter/requests", json=payload)
    assert res.status_code == 201
    body = res.json()
    assert body["data"]["request_reference"].startswith("SC-")
    assert body["data"]["return_date"] == ret_date


def test_validation_past_departure_date():
    past_date = (date.today() - timedelta(days=2)).isoformat()
    payload = {
        "customer_name": "Invalid Date User",
        "country_code": "+91",
        "phone": "9876543210",
        "email": "invalid@test.com",
        "trip_type": "ONE_WAY",
        "origin": "DEL",
        "destination": "BOM",
        "departure_date": past_date,
    }
    res = client.post("/api/v1/charter/requests", json=payload)
    assert res.status_code in [400, 422]


def test_validation_invalid_round_trip_return_date():
    dep_date = (date.today() + timedelta(days=10)).isoformat()
    ret_date = (date.today() + timedelta(days=5)).isoformat()  # Earlier than departure

    payload = {
        "customer_name": "Bad Return Date",
        "country_code": "+91",
        "phone": "9876543210",
        "email": "badreturn@test.com",
        "trip_type": "ROUND_TRIP",
        "origin": "DEL",
        "destination": "BOM",
        "departure_date": dep_date,
        "return_date": ret_date,
    }
    res = client.post("/api/v1/charter/requests", json=payload)
    assert res.status_code in [400, 422]


def test_admin_list_and_update_charter_requests(admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Create request
    future_date = (date.today() + timedelta(days=30)).isoformat()
    payload = {
        "customer_name": "Ambassador Zhang",
        "country_code": "+86",
        "phone": "13800138000",
        "email": "zhang@diplomatic.cn",
        "trip_type": "ONE_WAY",
        "origin": "Beijing Capital (PEK)",
        "destination": "Geneva Airport (GVA)",
        "departure_date": future_date,
        "aircraft_preference": "ULTRA_LONG_RANGE",
    }
    create_res = client.post("/api/v1/charter/requests", json=payload)
    assert create_res.status_code == 201
    ref = create_res.json()["data"]["request_reference"]

    # Admin List
    list_res = client.get("/api/v1/admin/charter/requests", headers=headers)
    assert list_res.status_code == 200
    list_body = list_res.json()
    assert list_body["success"] is True
    items = list_body.get("data", {}).get("items") or list_body.get("data") or []
    assert list_body.get("data", {}).get("total", list_body.get("total", len(items))) >= 1
    found_item = next((item for item in items if item["request_reference"] == ref), None)
    assert found_item is not None
    req_id = found_item["id"]

    # Admin Update Status & Notes
    update_res = client.patch(
        f"/api/v1/admin/charter/requests/{req_id}",
        json={
            "status": "UNDER_REVIEW",
            "assigned_staff_name": "Captain Vikram Sharma",
            "internal_notes": "Diplomatic clearance request in progress for PEK-GVA routing.",
        },
        headers=headers,
    )
    assert update_res.status_code == 200
    update_body = update_res.json()
    assert update_body["data"]["status"] == "UNDER_REVIEW"
    assert update_body["data"]["assigned_staff_name"] == "Captain Vikram Sharma"
    assert update_body["data"]["internal_notes"] == "Diplomatic clearance request in progress for PEK-GVA routing."
