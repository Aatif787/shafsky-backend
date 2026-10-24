"""Unit tests for Passenger Name validation in booking schemas, enquiries, and services."""

import pytest
from pydantic import ValidationError
from app.schemas.booking import BookingCreate
from app.schemas.enquiry import ServiceEnquiryCreate
from app.schemas.journey_schemas import ServiceQueryCreate
from app.schemas.airport import PassengerCreate
from app.services.service_config_service import ServiceConfigService


def _valid_booking_payload(name: str):
    return {
        "passengerName": name,
        "passengerEmail": "passenger@example.com",
        "passengerPhone": "+919876543210",
        "serviceType": "Meet & Assist",
        "totalAmount": 1500.0,
    }


from typing import Any, cast
from sqlalchemy.orm import Session

def _valid_enquiry_payload(name: str) -> dict[str, Any]:
    return {
        "passengerName": name,
        "passengerEmail": "enquiry@example.com",
        "passengerPhone": "+919876543210",
        "serviceCategory": "Ground Transport",
        "serviceType": "Chauffeur",
    }


# =====================================================================
# ACCEPTANCE TESTS FOR BookingCreate
# =====================================================================
@pytest.mark.parametrize("name", [
    "Aariz Farooqui",
    "Aariz",
    "Rahul Kumar",
    "  Aariz Farooqui  ",  # Leading/trailing spaces trimmed
])
def test_booking_create_accepts_valid_names(name: str):
    payload = _valid_booking_payload(name)
    bc = BookingCreate(**payload)
    assert bc.passenger_name == name.strip()


@pytest.mark.parametrize("name", [
    "30",
    "12345",
    "999",
    "",
    "   ",
    "30 Aariz",
    "Alex123",
])
def test_booking_create_rejects_numeric_and_invalid_names(name: str):
    payload = _valid_booking_payload(name)
    with pytest.raises(ValidationError) as exc_info:
        BookingCreate(**payload)
    assert "Please enter a valid passenger name using letters only." in str(exc_info.value)


# =====================================================================
# ACCEPTANCE TESTS FOR ServiceEnquiryCreate
# =====================================================================
@pytest.mark.parametrize("name", [
    "Aariz Farooqui",
    "Aariz",
    "Rahul Kumar",
])
def test_enquiry_create_accepts_valid_names(name: str):
    payload = _valid_enquiry_payload(name)
    ec = ServiceEnquiryCreate(**payload)
    assert ec.passenger_name == name.strip()


@pytest.mark.parametrize("name", [
    "30",
    "12345",
    "999",
    "",
    "   ",
])
def test_enquiry_create_rejects_numeric_names(name: str):
    payload = _valid_enquiry_payload(name)
    with pytest.raises(ValidationError) as exc_info:
        ServiceEnquiryCreate(**payload)
    assert "Please enter a valid passenger name using letters only." in str(exc_info.value)


# =====================================================================
# ACCEPTANCE TESTS FOR ServiceQueryCreate & PassengerCreate
# =====================================================================
def test_service_query_create_name_validation():
    # Valid name
    sq = ServiceQueryCreate(
        passenger_name="Aariz Farooqui",
        passenger_email="aariz@example.com",
        passenger_phone="+919876543210"
    )
    assert sq.passenger_name == "Aariz Farooqui"

    # Numeric name
    with pytest.raises(ValidationError) as exc_info:
        ServiceQueryCreate(
            passenger_name="30",
            passenger_email="aariz@example.com",
            passenger_phone="+919876543210"
        )
    assert "Please enter a valid passenger name using letters only." in str(exc_info.value)


def test_passenger_create_name_validation():
    # Valid name
    pc = PassengerCreate(full_name="Rahul Kumar")
    assert pc.full_name == "Rahul Kumar"

    # Numeric name
    with pytest.raises(ValidationError) as exc_info:
        PassengerCreate(full_name="12345")
    assert "Please enter a valid passenger name using letters only." in str(exc_info.value)


# =====================================================================
# SERVICE DRAFT VALIDATION
# =====================================================================
def test_save_booking_draft_rejects_numeric_name():
    service = ServiceConfigService()
    # 30 as passenger name
    res = service.save_booking_draft(
        db=cast(Session, None),  # Validation fails before DB access
        payload={
            "full_name": "30",
            "email": "valid@example.com",
            "phone": "+919876543210",
        }
    )
    assert res["valid"] is False
    assert any(
        err["field"] == "full_name" and err["message"] == "Please enter a valid passenger name using letters only."
        for err in res["errors"]
    )


def test_phone_validation_remains_unchanged():
    # Test that valid phone numbers are accepted and phone field logic is unaffected
    payload = _valid_booking_payload("Aariz Farooqui")
    payload["passengerPhone"] = "+919876543210"
    bc = BookingCreate(**payload)
    assert bc.passenger_phone == "+919876543210"
