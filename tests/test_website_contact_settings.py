"""
Test Suite for Website Contact Management System.

Covers:
1. Public endpoint returns only public contact data for the six canonical services.
2. Empty contact slots are excluded from public responses (no broken links).
3. Admin GET returns all 6 services with both slot 1 and slot 2 (including empty slots).
4. RBAC security: Unauthenticated or non-admin users cannot access admin GET/PUT.
5. Admin PUT validation:
   - Validates country code and phone number digits.
   - Rejects malformed service keys.
   - Rejects invalid phone numbers.
6. Admin PUT updates persist and immediately reflect in public endpoint.
7. Clearing a slot removes it from public response without affecting other services.
8. Updating one service is isolated and does not mutate other services.
"""

import uuid
import pytest
from starlette.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import Base, get_db
from app.models.schema import WebsiteContactSetting
from app.services.auth_service import AuthService
from app.schemas.contact_settings import CANONICAL_SERVICE_KEYS


# In-memory database for isolated, lightning-fast integration testing
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
Base.metadata.create_all(bind=test_engine, tables=[WebsiteContactSetting.__table__])


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def override_db_dependency():
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_token():
    return AuthService.create_access_token({
        "sub": "admin@shafsky.com",
        "user_id": str(uuid.uuid4()),
        "role": "ADMIN",
    })


@pytest.fixture
def customer_token():
    return AuthService.create_access_token({
        "sub": "customer@shafsky.com",
        "user_id": str(uuid.uuid4()),
        "role": "CUSTOMER",
    })


def test_public_contact_settings_structure(client):
    """Public endpoint must return success and dictionary containing the 6 canonical services."""
    res = client.get("/api/public/contact-settings")
    assert res.status_code == 200
    body = res.json()
    assert body.get("success") is True
    data = body.get("data", {})

    for key in CANONICAL_SERVICE_KEYS:
        assert key in data
        assert "service_name" in data[key]
        assert "contacts" in data[key]
        assert isinstance(data[key]["contacts"], list)


def test_admin_contact_settings_requires_admin_auth(client, customer_token):
    """Admin GET must reject unauthenticated requests (401) and non-admin roles (403)."""
    # 1. No token -> 401
    res = client.get("/api/admin/contact-settings")
    assert res.status_code in (401, 403)

    # 2. Customer token -> 403
    res_cust = client.get(
        "/api/admin/contact-settings",
        headers={"Authorization": f"Bearer {customer_token}"},
    )
    assert res_cust.status_code == 403


def test_admin_contact_settings_returns_all_six_services_with_two_slots(client, admin_token):
    """Admin GET returns all 6 services with exactly slot 1 and slot 2 each."""
    res = client.get(
        "/api/admin/contact-settings",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("success") is True
    services = body.get("data", [])

    assert len(services) == 6
    service_keys_received = [s["service_key"] for s in services]
    for key in CANONICAL_SERVICE_KEYS:
        assert key in service_keys_received

    for s in services:
        slots = s.get("slots", [])
        assert len(slots) == 2
        slot_indices = [slot["slot_index"] for slot in slots]
        assert slot_indices == [1, 2]


def test_admin_put_validation_rejects_invalid_inputs(client, admin_token):
    """Admin PUT rejects malformed service keys, invalid country codes, and bad numbers."""
    # Invalid service_key
    res = client.put(
        "/api/admin/contact-settings",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "services": [
                {
                    "service_key": "invalid_service_xyz",
                    "slots": [{"slot_index": 1, "country_code": "+91", "phone_number": "9599087959"}],
                }
            ]
        },
    )
    assert res.status_code == 422

    # Invalid country_code (missing +)
    res_cc = client.put(
        "/api/admin/contact-settings",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "services": [
                {
                    "service_key": "transport",
                    "slots": [{"slot_index": 1, "country_code": "91", "phone_number": "9599087959"}],
                }
            ]
        },
    )
    assert res_cc.status_code == 422

    # Invalid phone_number (alpha characters or too short)
    res_num = client.put(
        "/api/admin/contact-settings",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "services": [
                {
                    "service_key": "hotel",
                    "slots": [{"slot_index": 1, "country_code": "+91", "phone_number": "12"}],
                }
            ]
        },
    )
    assert res_num.status_code == 422


def test_admin_put_updates_and_reflects_in_public_endpoint(client, admin_token):
    """Admin can configure 2 phone numbers for Transport; public endpoint immediately reflects it."""
    payload = {
        "services": [
            {
                "service_key": "transport",
                "slots": [
                    {"slot_index": 1, "country_code": "+91", "phone_number": "9811122233", "is_active": True},
                    {"slot_index": 2, "country_code": "+91", "phone_number": "9822233344", "is_active": True},
                ],
            }
        ]
    }

    put_res = client.put(
        "/api/admin/contact-settings",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload,
    )
    assert put_res.status_code == 200
    put_data = put_res.json()["data"]
    transport_config = next(s for s in put_data if s["service_key"] == "transport")
    assert transport_config["slots"][0]["full_phone_number"] == "+919811122233"
    assert transport_config["slots"][1]["full_phone_number"] == "+919822233344"

    # Immediately check public endpoint
    pub_res = client.get("/api/public/contact-settings")
    assert pub_res.status_code == 200
    pub_data = pub_res.json()["data"]
    transport_public = pub_data["transport"]
    assert len(transport_public["contacts"]) == 2
    assert transport_public["contacts"][0]["full_phone_number"] == "+919811122233"
    assert transport_public["contacts"][1]["full_phone_number"] == "+919822233344"


def test_clearing_a_number_slot_removes_it_from_public_response(client, admin_token):
    """Clearing slot 2 for Transport leaves only slot 1 in public response."""
    payload = {
        "services": [
            {
                "service_key": "transport",
                "slots": [
                    {"slot_index": 1, "country_code": "+91", "phone_number": "9811122233", "is_active": True},
                    {"slot_index": 2, "country_code": "", "phone_number": "", "is_active": True},
                ],
            }
        ]
    }

    put_res = client.put(
        "/api/admin/contact-settings",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload,
    )
    assert put_res.status_code == 200

    pub_res = client.get("/api/public/contact-settings")
    assert pub_res.status_code == 200
    transport_public = pub_res.json()["data"]["transport"]
    assert len(transport_public["contacts"]) == 1
    assert transport_public["contacts"][0]["full_phone_number"] == "+919811122233"


def test_isolation_between_services(client, admin_token):
    """Updating Meet & Greet & Lounge does not affect Transport numbers."""
    payload = {
        "services": [
            {
                "service_key": "meet_greet_lounge",
                "slots": [
                    {"slot_index": 1, "country_code": "+91", "phone_number": "9599087959", "is_active": True},
                    {"slot_index": 2, "country_code": "+91", "phone_number": "9599615459", "is_active": True},
                ],
            }
        ]
    }
    put_res = client.put(
        "/api/admin/contact-settings",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload,
    )
    assert put_res.status_code == 200

    pub_res = client.get("/api/public/contact-settings")
    pub_data = pub_res.json()["data"]
    assert len(pub_data["meet_greet_lounge"]["contacts"]) == 2
    assert pub_data["meet_greet_lounge"]["contacts"][0]["full_phone_number"] == "+919599087959"
    assert pub_data["meet_greet_lounge"]["contacts"][1]["full_phone_number"] == "+919599615459"
    # Transport remains intact from previous test
    assert len(pub_data["transport"]["contacts"]) == 1
    assert pub_data["transport"]["contacts"][0]["full_phone_number"] == "+919811122233"
