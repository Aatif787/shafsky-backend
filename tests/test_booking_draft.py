"""
Unit & Integration Tests for Booking Draft Persistence & Field Validation Engine.
"""

import pytest
from app.database import SessionLocal
from app.services.service_config_service import ServiceConfigService


def test_valid_passenger_draft_creation():
    db = SessionLocal()
    try:
        payload = {
            "full_name": "Lord Henry Sterling",
            "email": "henry@sterling.com",
            "phone": "+919876543210",
            "guest_count": 2,
            "flight_number": "AI302",
            "journey_type": "arrival",
            "airport_code": "BOM",
            "selected_package_id": "premium",
            "selected_service_ids": ["buggy"],
            "service_date": "2026-08-20",
            "service_time": "14:00"
        }

        res = ServiceConfigService.save_booking_draft(db, payload)
        assert res["valid"] is True
        assert res["status"] == "DRAFT"
        assert res["booking_reference"].startswith("SHK-")
        assert res["subtotal"] > 0
        assert res["total"] > res["subtotal"]
    finally:
        db.close()


def test_invalid_email_rejection():
    db = SessionLocal()
    try:
        payload = {
            "full_name": "Lord Henry Sterling",
            "email": "invalid-email-address",
            "phone": "+919876543210",
            "flight_number": "AI302",
            "airport_code": "BOM",
            "journey_type": "arrival",
            "selected_package_id": "essential"
        }

        res = ServiceConfigService.save_booking_draft(db, payload)
        assert res["valid"] is False
        assert any(e["field"] == "email" for e in res["errors"])
    finally:
        db.close()


def test_invalid_phone_rejection():
    db = SessionLocal()
    try:
        payload = {
            "full_name": "Lord Henry Sterling",
            "email": "henry@sterling.com",
            "phone": "123",  # Too short
            "flight_number": "AI302",
            "airport_code": "BOM",
            "journey_type": "arrival",
            "selected_package_id": "essential"
        }

        res = ServiceConfigService.save_booking_draft(db, payload)
        assert res["valid"] is False
        assert any(e["field"] == "phone" for e in res["errors"])
    finally:
        db.close()


def test_missing_name_rejection():
    db = SessionLocal()
    try:
        payload = {
            "full_name": "",
            "email": "henry@sterling.com",
            "phone": "+919876543210",
            "flight_number": "AI302",
            "airport_code": "BOM",
            "journey_type": "arrival"
        }

        res = ServiceConfigService.save_booking_draft(db, payload)
        assert res["valid"] is False
        assert any(e["field"] == "full_name" for e in res["errors"])
    finally:
        db.close()


def test_update_existing_draft_no_duplicates():
    db = SessionLocal()
    try:
        payload1 = {
            "full_name": "Lord Henry Sterling",
            "email": "henry@sterling.com",
            "phone": "+919876543210",
            "guest_count": 1,
            "flight_number": "AI302",
            "journey_type": "arrival",
            "airport_code": "BOM",
            "selected_package_id": "essential",
            "service_date": "2026-08-20",
            "service_time": "14:00"
        }

        res1 = ServiceConfigService.save_booking_draft(db, payload1)
        assert res1["valid"] is True
        ref = res1["booking_reference"]

        # Update existing draft with modified passenger count & additional service
        payload2 = {
            "booking_ref": ref,
            "full_name": "Lord Henry Sterling",
            "email": "henry@sterling.com",
            "phone": "+919876543210",
            "guest_count": 3,
            "flight_number": "AI302",
            "journey_type": "arrival",
            "airport_code": "BOM",
            "selected_package_id": "premium",
            "selected_service_ids": ["buggy"],
            "service_date": "2026-08-20",
            "service_time": "14:00"
        }

        res2 = ServiceConfigService.save_booking_draft(db, payload2)
        assert res2["valid"] is True
        assert res2["booking_reference"] == ref
        assert res2["booking_context"]["guestCount"] == 3
    finally:
        db.close()


def test_uncovered_airport_rejection():
    db = SessionLocal()
    try:
        payload = {
            "full_name": "Lord Henry Sterling",
            "email": "henry@sterling.com",
            "phone": "+919876543210",
            "flight_number": "XYZ999",
            "journey_type": "arrival",
            "airport_code": "UNCOVERED_AIRPORT_XYZ",
            "selected_package_id": "essential"
        }

        res = ServiceConfigService.save_booking_draft(db, payload)
        assert res["valid"] is False
    finally:
        db.close()
