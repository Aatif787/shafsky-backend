import sys
import os
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.schema import UserAuth, Role, ServicesConfig, Booking, BookingStatus
from app.security.jwt import SecurityJWT
from app.booking.service_validator import ServiceValidator
from app.services.service_config_service import ServiceConfigService

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    ServiceConfigService.seed_default_catalog(db)
    db.close()
    yield

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def _seed_journey_catalog():
    """Seed the production journey catalog + global airports for country resolution."""
    import uuid as _uuid
    Base.metadata.create_all(bind=engine)
    from app.seeds.seed_journey_data import run_seed
    run_seed()
    from app.models.schema import AirportManagement
    db = SessionLocal()
    try:
        for code, country in (
            ("LHR", "United Kingdom"),
            ("JFK", "United States"),
            ("DXB", "United Arab Emirates"),
        ):
            if not db.query(AirportManagement).filter_by(code=code).first():
                db.add(AirportManagement(
                    id=_uuid.uuid4(),
                    code=code,
                    name=f"{code} International Airport",
                    city=code,
                    country=country,
                    is_active=True,
                ))
        db.commit()
    finally:
        db.close()
    yield

@pytest.fixture
def admin_token():
    payload = {
        "sub": "admin@shafsky.com",
        "userId": "00000000-0000-0000-0000-000000000001",
        "role": Role.ADMIN.value,
        "email": "admin@shafsky.com"
    }
    return SecurityJWT.create_access_token(payload)

# ─── TEST SERVICE CATALOG API ──────────────────────────────────────────────────

def test_get_public_service_catalog(client):
    response = client.get("/api/services/catalog")
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["success"] is True
    catalog = res_data["data"]
    
    categories = [cat["category"] for cat in catalog]
    assert "Airport Assistance" in categories
    assert "Ground Transport" in categories
    assert "Private Charter" in categories
    assert "Cargo & Logistics" in categories
    assert "Medical Assistance" in categories
    assert "Travel Support" in categories

# ─── TEST BOOKING CREATION ACROSS ALL 6 CATEGORIES ─────────────────────────────

def test_create_airport_assistance_booking(client):
    dep_time = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    arr_time = (datetime.now(timezone.utc) + timedelta(hours=52)).isoformat()
    payload = {
        "passengerName": "Alice Vance",
        "passengerEmail": "alice@shafsky-mail.com",
        "passengerPhone": "+19876543210",
        "serviceCategory": "Airport Assistance",
        "serviceType": "Meet & Greet",
        "flightNum": "SHF-101",
        "originCode": "DEL",
        "destCode": "BOM",
        "departureTime": dep_time,
        "arrivalTime": arr_time,
        "totalAmount": 4500.0,
        "currency": "INR",
        "serviceOptions": {"terminal": "T3", "wheelchair": False}
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["serviceCategory"] == "Airport Assistance"
    assert data["serviceType"] == "Meet & Greet"
    assert data["flightNum"] == "SHF-101"

def test_create_ground_transport_booking(client):
    payload = {
        "passengerName": "Bob Smith",
        "passengerEmail": "bob@shafsky-mail.com",
        "passengerPhone": "+19876543211",
        "originCode": "DEL",
        "destCode": "BOM",
        "serviceCategory": "Ground Transport",
        "serviceType": "Platinum",
        "totalAmount": 6000.0,
        "currency": "INR",
        "serviceOptions": {
            "pickup_location": "Terminal 3, DEL",
            "dropoff_location": "Taj Palace, New Delhi",
            "vehicle_model": "Mercedes E-Class"
        }
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["serviceCategory"] == "Ground Transport"
    assert data["serviceType"] == "Platinum"
    assert data["serviceOptions"]["pickup_location"] == "Terminal 3, DEL"

def test_create_private_charter_booking(client):
    payload = {
        "passengerName": "Charles Xavier",
        "passengerEmail": "charles@shafsky-mail.com",
        "passengerPhone": "+19876543212",
        "originCode": "DEL",
        "destCode": "BOM",
        "serviceCategory": "Private Charter",
        "serviceType": "Platinum",
        "totalAmount": 250000.0,
        "currency": "INR",
        "serviceOptions": {
            "origin": "DEL",
            "destination": "BOM",
            "passengers": 4
        }
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["serviceCategory"] == "Private Charter"
    assert data["serviceType"] == "Platinum"

def test_create_cargo_logistics_booking(client):
    payload = {
        "passengerName": "David Logistics",
        "passengerEmail": "david@shafsky-mail.com",
        "passengerPhone": "+19876543213",
        "originCode": "DEL",
        "destCode": "BOM",
        "serviceCategory": "Cargo & Logistics",
        "serviceType": "Platinum",
        "totalAmount": 15000.0,
        "currency": "INR",
        "serviceOptions": {
            "origin": "DEL",
            "destination": "DXB",
            "cargo_weight_kg": 250,
            "dimensions": "120x80x100cm"
        }
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["serviceCategory"] == "Cargo & Logistics"
    assert data["serviceType"] == "Platinum"

def test_create_medical_assistance_booking(client):
    payload = {
        "passengerName": "Dr. Eleanor Medical",
        "passengerEmail": "eleanor@shafsky-mail.com",
        "passengerPhone": "+19876543214",
        "originCode": "DEL",
        "destCode": "BOM",
        "serviceCategory": "Medical Assistance",
        "serviceType": "Platinum",
        "totalAmount": 600000.0,
        "currency": "INR",
        "serviceOptions": {
            "patient_condition": "Post-cardiac surgery critical transport",
            "origin_hospital": "Apollo Delhi",
            "dest_hospital": "Mount Elizabeth Singapore"
        }
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["serviceCategory"] == "Medical Assistance"
    assert data["serviceType"] == "Platinum"

def test_create_travel_support_booking(client):
    payload = {
        "passengerName": "Fiona Travel",
        "passengerEmail": "fiona@shafsky-mail.com",
        "passengerPhone": "+19876543215",
        "originCode": "DEL",
        "destCode": "BOM",
        "serviceCategory": "Travel Support",
        "serviceType": "Platinum",
        "totalAmount": 5000.0,
        "currency": "INR",
        "serviceOptions": {
            "destination_country": "Schengen / France",
            "visa_type": "Business Fast Track"
        }
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["serviceCategory"] == "Travel Support"
    assert data["serviceType"] == "Platinum"

# ─── TEST DYNAMIC VALIDATION & RELEASE 1 BACKWARD COMPATIBILITY ──────────────

def test_release_1_backward_compatibility(client):
    """Release 1 payload without serviceCategory should default to Airport Assistance seamlessly."""
    dep_time = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    arr_time = (datetime.now(timezone.utc) + timedelta(hours=52)).isoformat()
    payload = {
        "passengerName": "Release1 Client",
        "passengerEmail": "r1@shafsky-mail.com",
        "passengerPhone": "+19876543299",
        "serviceType": "silver",
        "flightNum": "AI-101",
        "originCode": "BOM",
        "destCode": "DEL",
        "departureTime": dep_time,
        "arrivalTime": arr_time,
        "selectedServices": {"porter": True},
        "totalAmount": 4500.0,
        "currency": "INR"
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["serviceCategory"] == "Airport Assistance"
    assert data["serviceType"] == "silver"

def test_validation_error_missing_ground_transport_pickup(client):
    payload = {
        "passengerName": "Bad Transport",
        "passengerEmail": "bad@shafsky-mail.com",
        "passengerPhone": "+19876543211",
        "serviceCategory": "Ground Transport",
        "serviceType": "Luxury Sedan",
        "totalAmount": 6000.0,
        "serviceOptions": {} # Missing pickup and dropoff
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 400
    assert "pickup" in response.json()["detail"].lower()

# ─── TEST ADMIN SERVICE CONFIGURATION ─────────────────────────────────────────

def test_admin_update_service_config(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    patch_payload = {
        "basePrice": 5200.0,
        "description": "Updated VIP meet and greet description",
        "isActive": True,
        "isHidden": False
    }
    response = client.patch(
        "/api/admin/services/config/airport_assistance_meet_greet",
        json=patch_payload,
        headers=headers
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert float(data["basePrice"]) == 5200.0
    assert data["description"] == "Updated VIP meet and greet description"


# ─── TEST JOURNEY-SPECIFIC AIRPORT ASSISTANCE TIME VALIDATION ─────────────────

def test_departure_with_departure_time_only_is_valid(client):
    """DEPARTURE journey should succeed with only departure_time (arrival_time not required)."""
    dep_time = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    payload = {
        "passengerName": "Dep Passenger",
        "passengerEmail": "dep@shafsky-mail.com",
        "passengerPhone": "+919876543210",
        "serviceCategory": "Airport Assistance",
        "serviceType": "silver",
        "flightNum": "SHF-DEP1",
        "originCode": "DEL",
        "destCode": "BOM",
        "departureTime": dep_time,
        "metadataJson": {
            "journey_type": "DEPARTURE",
            "flight_type": "DOMESTIC",
            "service_airport": "DEL"
        },
        "totalAmount": 4500.0,
        "currency": "INR"
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["departureTime"] is not None
    assert body["data"]["arrivalTime"] is None


def test_departure_without_departure_time_is_invalid(client):
    """DEPARTURE journey must fail if departure_time is missing."""
    arr_time = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    payload = {
        "passengerName": "Dep Missing",
        "passengerEmail": "dep_missing@shafsky-mail.com",
        "passengerPhone": "+919876543210",
        "serviceCategory": "Airport Assistance",
        "serviceType": "silver",
        "flightNum": "SHF-DEP2",
        "originCode": "DEL",
        "destCode": "BOM",
        "arrivalTime": arr_time,
        "metadataJson": {
            "journey_type": "DEPARTURE",
            "flight_type": "DOMESTIC",
            "service_airport": "DEL"
        },
        "totalAmount": 4500.0,
        "currency": "INR"
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 400


def test_arrival_with_arrival_time_only_is_valid(client):
    """ARRIVAL journey should succeed with only arrival_time (departure_time not required)."""
    arr_time = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    payload = {
        "passengerName": "Arr Passenger",
        "passengerEmail": "arr@shafsky-mail.com",
        "passengerPhone": "+919876543210",
        "serviceCategory": "Airport Assistance",
        "serviceType": "silver",
        "flightNum": "SHF-ARR1",
        "originCode": "BOM",
        "destCode": "DEL",
        "arrivalTime": arr_time,
        "metadataJson": {
            "journey_type": "ARRIVAL",
            "flight_type": "DOMESTIC",
            "service_airport": "DEL"
        },
        "totalAmount": 4500.0,
        "currency": "INR"
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["arrivalTime"] is not None
    assert body["data"]["departureTime"] is None


def test_arrival_without_arrival_time_is_invalid(client):
    """ARRIVAL journey must fail if arrival_time is missing."""
    dep_time = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    payload = {
        "passengerName": "Arr Missing",
        "passengerEmail": "arr_missing@shafsky-mail.com",
        "passengerPhone": "+919876543210",
        "serviceCategory": "Airport Assistance",
        "serviceType": "silver",
        "flightNum": "SHF-ARR2",
        "originCode": "BOM",
        "destCode": "DEL",
        "departureTime": dep_time,
        "metadataJson": {
            "journey_type": "ARRIVAL",
            "flight_type": "DOMESTIC",
            "service_airport": "DEL"
        },
        "totalAmount": 4500.0,
        "currency": "INR"
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 400


def test_transit_with_both_times_is_valid(client):
    """TRANSIT journey succeeds when both arrival_time and departure_time are provided."""
    dep_time = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    arr_time = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    payload = {
        "passengerName": "Transit Passenger",
        "passengerEmail": "transit@shafsky-mail.com",
        "passengerPhone": "+919876543210",
        "serviceCategory": "Airport Assistance",
        "serviceType": "meet_greet",
        "flightNum": "SHF-TR1",
        "originCode": "DEL",
        "destCode": "DXB",
        "departureTime": dep_time,
        "arrivalTime": arr_time,
        "metadataJson": {
            "journey_type": "TRANSIT",
            "flight_type": "DOMESTIC_INTERNATIONAL",
            "transit_code": "DEL",
            "service_airport": "DEL"
        },
        "totalAmount": 7500.0,
        "currency": "INR"
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True


def test_transit_missing_time_is_invalid(client):
    """TRANSIT journey must fail if either arrival_time or departure_time is missing."""
    dep_time = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    payload = {
        "passengerName": "Transit Missing",
        "passengerEmail": "transit_missing@shafsky-mail.com",
        "passengerPhone": "+919876543210",
        "serviceCategory": "Airport Assistance",
        "serviceType": "meet_greet",
        "flightNum": "SHF-TR2",
        "originCode": "BOM",
        "destCode": "DXB",
        "departureTime": dep_time,
        "metadataJson": {
            "journey_type": "TRANSIT",
            "flight_type": "DOMESTIC_INTERNATIONAL",
            "transit_code": "DEL",
            "service_airport": "DEL"
        },
        "totalAmount": 4500.0,
        "currency": "INR"
    }
    response = client.post("/api/bookings", json=payload)
    assert response.status_code == 400
    assert "transit" in response.json()["detail"].lower()


# ─── DIRECT SERVICEVALIDATOR UNIT TESTS ───────────────────────────────────────

def test_service_validator_departure_valid():
    from app.schemas.booking import BookingCreate
    payload = BookingCreate(
        passengerName="Direct Dep",
        passengerEmail="direct_dep@shafsky-mail.com",
        passengerPhone="+919876543210",
        serviceCategory="Airport Assistance",
        serviceType="silver",
        flightNum="AI-101",
        originCode="DEL",
        destCode="BOM",
        departureTime=datetime.now(timezone.utc) + timedelta(hours=24),
        metadataJson={"journey_type": "DEPARTURE"},
        totalAmount=2500.0
    )
    cat = ServiceValidator.validate_booking(payload)
    assert cat == "Airport Assistance"


def test_service_validator_departure_missing_departure_time():
    from app.schemas.booking import BookingCreate
    from fastapi import HTTPException
    payload = BookingCreate(
        passengerName="Direct Dep Missing",
        passengerEmail="direct_dep_missing@shafsky-mail.com",
        passengerPhone="+919876543210",
        serviceCategory="Airport Assistance",
        serviceType="silver",
        flightNum="AI-101",
        originCode="DEL",
        destCode="BOM",
        metadataJson={"journey_type": "DEPARTURE"},
        totalAmount=2500.0
    )
    with pytest.raises(HTTPException) as exc:
        ServiceValidator.validate_booking(payload)
    assert exc.value.status_code == 400
    assert "departure" in exc.value.detail.lower()


def test_service_validator_arrival_valid():
    from app.schemas.booking import BookingCreate
    payload = BookingCreate(
        passengerName="Direct Arr",
        passengerEmail="direct_arr@shafsky-mail.com",
        passengerPhone="+919876543210",
        serviceCategory="Airport Assistance",
        serviceType="silver",
        flightNum="AI-102",
        originCode="BOM",
        destCode="DEL",
        arrivalTime=datetime.now(timezone.utc) + timedelta(hours=24),
        metadataJson={"journey_type": "ARRIVAL"},
        totalAmount=2500.0
    )
    cat = ServiceValidator.validate_booking(payload)
    assert cat == "Airport Assistance"


def test_service_validator_arrival_missing_arrival_time():
    from app.schemas.booking import BookingCreate
    from fastapi import HTTPException
    payload = BookingCreate(
        passengerName="Direct Arr Missing",
        passengerEmail="direct_arr_missing@shafsky-mail.com",
        passengerPhone="+919876543210",
        serviceCategory="Airport Assistance",
        serviceType="silver",
        flightNum="AI-102",
        originCode="BOM",
        destCode="DEL",
        metadataJson={"journey_type": "ARRIVAL"},
        totalAmount=2500.0
    )
    with pytest.raises(HTTPException) as exc:
        ServiceValidator.validate_booking(payload)
    assert exc.value.status_code == 400
    assert "arrival" in exc.value.detail.lower()


def test_service_validator_transit_valid():
    from app.schemas.booking import BookingCreate
    payload = BookingCreate(
        passengerName="Direct Transit",
        passengerEmail="direct_tr@shafsky-mail.com",
        passengerPhone="+919876543210",
        serviceCategory="Airport Assistance",
        serviceType="silver",
        flightNum="AI-103",
        originCode="BOM",
        destCode="DXB",
        arrivalTime=datetime.now(timezone.utc) + timedelta(hours=20),
        departureTime=datetime.now(timezone.utc) + timedelta(hours=24),
        metadataJson={"journey_type": "TRANSIT"},
        totalAmount=2500.0
    )
    cat = ServiceValidator.validate_booking(payload)
    assert cat == "Airport Assistance"


def test_service_validator_transit_missing_time():
    from app.schemas.booking import BookingCreate
    from fastapi import HTTPException
    payload = BookingCreate(
        passengerName="Direct Transit Missing",
        passengerEmail="direct_tr_missing@shafsky-mail.com",
        passengerPhone="+919876543210",
        serviceCategory="Airport Assistance",
        serviceType="silver",
        flightNum="AI-103",
        originCode="BOM",
        destCode="DXB",

        metadataJson={"journey_type": "TRANSIT"},
        totalAmount=2500.0
    )
    with pytest.raises(HTTPException) as exc:
        ServiceValidator.validate_booking(payload)
    assert exc.value.status_code == 400
    assert "transit" in exc.value.detail.lower()


