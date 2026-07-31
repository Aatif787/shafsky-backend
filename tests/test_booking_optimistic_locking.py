"""
Unit and Concurrency Test Suite for Milestone A3: Booking Optimistic Locking.
"""

import sys
import os
import pytest
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.main import app
from app.database import SessionLocal
from app.models.schema import Booking, BookingStatus
from app.booking.exceptions import ConcurrencyException
from app.services.booking_service import BookingService
from sqlalchemy.orm.exc import StaleDataError

client = TestClient(app)


def get_admin_headers():
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"}
    )
    assert res.status_code == 200, res.text
    token = res.json()["data"]["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def test_01_booking_creation_version_default():
    """Verify that a newly created booking has version=1."""
    dep_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    arr_time = (datetime.now(timezone.utc) + timedelta(days=2, hours=3)).isoformat()

    payload = {
        "passenger_name": "John Doe",
        "passenger_email": "johndoe@shafskyaviation.com",
        "passenger_phone": "+1234567890",
        "flight_num": "SF101",
        "origin_code": "DEL",
        "dest_code": "BOM",
        "departure_time": dep_time,
        "arrival_time": arr_time,
        "service_type": "MEET_AND_GREET",
        "total_amount": 2500.0,
        "currency": "INR"
    }

    res = client.post("/api/bookings", json=payload)
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    assert data["bookingRef"] is not None
    assert data["version"] == 1
    assert data["status"] == "PENDING"


def test_02_sequential_version_increments():
    """Verify that version increments on every successful update."""
    admin_headers = get_admin_headers()
    dep_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    arr_time = (datetime.now(timezone.utc) + timedelta(days=2, hours=3)).isoformat()

    payload = {
        "passenger_name": "Jane Smith",
        "passenger_email": "janesmith@shafskyaviation.com",
        "passenger_phone": "+1234567890",
        "flight_num": "SF202",
        "origin_code": "DEL",
        "dest_code": "LHR",
        "departure_time": dep_time,
        "arrival_time": arr_time,
        "service_type": "VIP_LOUNGE",
        "total_amount": 4500.0,
        "currency": "INR"
    }

    res_create = client.post("/api/bookings", json=payload)
    assert res_create.status_code == 201
    booking_ref = res_create.json()["data"]["bookingRef"]
    assert res_create.json()["data"]["version"] == 1

    # First Update: PENDING -> CONFIRMED
    res_up1 = client.patch(
        f"/api/bookings/admin/{booking_ref}/status",
        json={"status": "CONFIRMED", "version": 1},
        headers=admin_headers
    )
    assert res_up1.status_code == 200, res_up1.text
    assert res_up1.json()["data"]["version"] == 2
    assert res_up1.json()["data"]["status"] == "CONFIRMED"

    # Second Update: CONFIRMED -> ASSIGNED
    res_up2 = client.patch(
        f"/api/bookings/admin/{booking_ref}/status",
        json={"status": "ASSIGNED", "version": 2},
        headers=admin_headers
    )
    assert res_up2.status_code == 200, res_up2.text
    assert res_up2.json()["data"]["version"] == 3
    assert res_up2.json()["data"]["status"] == "ASSIGNED"


def test_03_stale_update_returns_http_409_conflict():
    """Verify that submitting a stale version returns HTTP 409 Conflict."""
    admin_headers = get_admin_headers()
    dep_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    arr_time = (datetime.now(timezone.utc) + timedelta(days=2, hours=3)).isoformat()

    payload = {
        "passenger_name": "Alex Vance",
        "passenger_email": "alex@shafskyaviation.com",
        "passenger_phone": "+1234567890",
        "flight_num": "SF303",
        "origin_code": "BOM",
        "dest_code": "DXB",
        "departure_time": dep_time,
        "arrival_time": arr_time,
        "service_type": "CONCIERGE",
        "total_amount": 7500.0,
        "currency": "INR"
    }

    res_create = client.post("/api/bookings", json=payload)
    assert res_create.status_code == 201
    booking_ref = res_create.json()["data"]["bookingRef"]

    # Transaction A updates to CONFIRMED (version becomes 2)
    res_up1 = client.patch(
        f"/api/bookings/admin/{booking_ref}/status",
        json={"status": "CONFIRMED", "version": 1},
        headers=admin_headers
    )
    assert res_up1.status_code == 200
    assert res_up1.json()["data"]["version"] == 2

    # Transaction B sends stale update specifying version 1
    res_stale = client.patch(
        f"/api/bookings/admin/{booking_ref}/status",
        json={"status": "CANCELLED", "version": 1},
        headers=admin_headers
    )
    assert res_stale.status_code == 409
    assert "concurrency conflict" in res_stale.json()["detail"].lower()


def test_04_concurrent_thread_updates():
    """Simulate concurrent threads updating the same booking simultaneously."""
    admin_headers = get_admin_headers()
    dep_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    arr_time = (datetime.now(timezone.utc) + timedelta(days=2, hours=3)).isoformat()

    payload = {
        "passenger_name": "Concurrent Passenger",
        "passenger_email": "concurrent@shafskyaviation.com",
        "passenger_phone": "+1234567890",
        "flight_num": "SF999",
        "origin_code": "DEL",
        "dest_code": "BLR",
        "departure_time": dep_time,
        "arrival_time": arr_time,
        "service_type": "FAST_TRACK",
        "total_amount": 3200.0,
        "currency": "INR"
    }

    res_create = client.post("/api/bookings", json=payload)
    assert res_create.status_code == 201
    booking_ref = res_create.json()["data"]["bookingRef"]

    def perform_update(new_status):
        db = SessionLocal()
        try:
            res = BookingService.admin_update_status(db, booking_ref, new_status, expected_version=1)
            return ("SUCCESS", res)
        except Exception as exc:
            return ("FAILED", exc)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(perform_update, "CONFIRMED")
        f2 = executor.submit(perform_update, "IN_PROGRESS")
        r1 = f1.result()
        r2 = f2.result()

    statuses = [r1[0], r2[0]]
    # Exactly one thread should succeed, and one should fail with ConcurrencyException
    assert "SUCCESS" in statuses
    assert "FAILED" in statuses

    failed_result = r1[1] if r1[0] == "FAILED" else r2[1]
    assert isinstance(failed_result, ConcurrencyException)
    assert failed_result.status_code == 409


if __name__ == "__main__":
    test_01_booking_creation_version_default()
    test_02_sequential_version_increments()
    test_03_stale_update_returns_http_409_conflict()
    test_04_concurrent_thread_updates()
    print("ALL MILESTONE A3 OPTIMISTIC LOCKING TESTS PASSED 100%!")
