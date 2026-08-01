"""
Unit Test Suite for Air Ticketing Domain Foundation.
Tests Ticket Booking Creation, Passenger Roster Management,
Status Transitions, Timeline Events, Audit Logging, and API Router.
"""

import pytest
import uuid
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, get_db
from app.models.ticketing import AirTicketBooking, AirTicketPassenger, AirTicketStatus
from app.services.auth_service import AuthService

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


def test_create_and_get_air_ticket_booking():
    payload = {
        "contact_name": "Aariz Farooqui",
        "contact_email": "aariz@shafsky.com",
        "contact_phone": "+919876543210",
        "airline_name": "Emirates",
        "flight_number": "EK-501",
        "cabin_class": "FIRST",
        "origin_iata": "BOM",
        "destination_iata": "DXB",
        "departure_time": "2026-09-15T10:30:00Z",
        "arrival_time": "2026-09-15T12:45:00Z",
        "base_fare": 75000.0,
        "taxes_amount": 12500.0,
        "currency": "INR",
        "passengers": [
            {
                "passenger_type": "ADULT",
                "title": "MR",
                "first_name": "Aariz",
                "last_name": "Farooqui",
                "passport_number": "Z9876543",
                "seat_number": "1A"
            }
        ]
    }

    res = client.post("/api/ticketing/bookings", json=payload)
    assert res.status_code == 201, f"Failed with status {res.status_code}: {res.text}"
    data = res.json()
    assert data["success"] is True
    assert "booking_ref" in data["data"]
    assert data["data"]["booking_ref"].startswith("TKT-")
    assert data["data"]["total_fare"] == 87500.0
    assert len(data["data"]["passengers"]) == 1
    booking_id = data["data"]["id"]

    # GET Booking
    get_res = client.get(f"/api/ticketing/bookings/{booking_id}")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["success"] is True
    assert get_data["data"]["airline_name"] == "Emirates"


def test_list_and_search_air_ticket_bookings():
    # List Bookings
    res = client.get("/api/ticketing/bookings")
    assert res.status_code == 200
    assert res.json()["success"] is True

    # Search Bookings
    search_res = client.get("/api/ticketing/bookings?search=EK-501")
    assert search_res.status_code == 200
    assert search_res.json()["success"] is True


def test_add_passenger_and_transition():
    # 1. Create base booking
    payload = {
        "contact_name": "Jane Doe",
        "contact_email": "jane@shafsky.com",
        "contact_phone": "+19876543210",
        "airline_name": "British Airways",
        "flight_number": "BA-198",
        "cabin_class": "BUSINESS",
        "origin_iata": "DEL",
        "destination_iata": "LHR",
        "departure_time": "2026-10-01T08:00:00Z",
        "base_fare": 120000.0,
        "taxes_amount": 18000.0
    }
    res = client.post("/api/ticketing/bookings", json=payload)
    booking_id = res.json()["data"]["id"]

    # 2. Add passenger
    p_payload = {
        "passenger_type": "ADULT",
        "title": "MS",
        "first_name": "Jane",
        "last_name": "Doe",
        "seat_number": "4K"
    }
    res_pass = client.post(f"/api/ticketing/bookings/{booking_id}/passengers", json=p_payload)
    assert res_pass.status_code == 200
    assert res_pass.json()["success"] is True
    assert res_pass.json()["data"]["first_name"] == "Jane"

    # 3. Transition booking status
    trans_payload = {
        "target_state": "CONFIRMED",
        "pnr_code": "BA7789",
        "reason": "Payment verified and PNR generated"
    }
    res_trans = client.post(f"/api/ticketing/bookings/{booking_id}/transition", json=trans_payload)
    assert res_trans.status_code == 200
    trans_data = res_trans.json()
    assert trans_data["success"] is True
    assert trans_data["data"]["status"] == "CONFIRMED"
    assert trans_data["data"]["pnr_code"] == "BA7789"
