import pytest
from pydantic import ValidationError

from app.schemas.booking import BookingCreate
from app.schemas.enquiry import ServiceEnquiryCreate


def booking_payload(**overrides):
    payload = {
        "passengerName": "Aariz Farooqui",
        "passengerEmail": "aariz.farooqui@gmail.com",
        "passengerPhone": "+918864931247",
        "serviceType": "Airport Assistance",
        "totalAmount": 3000,
    }
    payload.update(overrides)
    return payload


def enquiry_payload(**overrides):
    payload = {
        "passengerName": "Aariz Farooqui",
        "passengerEmail": "aariz.farooqui@gmail.com",
        "passengerPhone": "+918864931247",
        "serviceCategory": "Transport",
        "serviceType": "Ground Transport",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("name", ["5555555", "12345", "Aariz123", "!!!", "  "])
def test_booking_rejects_invalid_passenger_names(name):
    with pytest.raises(ValidationError):
        BookingCreate.model_validate(booking_payload(passengerName=name))


@pytest.mark.parametrize("name", ["5555555", "12345", "Aariz123", "!!!", "  "])
def test_enquiry_rejects_invalid_passenger_names(name):
    with pytest.raises(ValidationError):
        ServiceEnquiryCreate.model_validate(enquiry_payload(passengerName=name))


@pytest.mark.parametrize("email", ["abc", "abc@", "abc@gmail", "test@@gmail.com", "user name@gmail.com"])
def test_booking_rejects_invalid_email_addresses(email):
    with pytest.raises(ValidationError):
        BookingCreate.model_validate(booking_payload(passengerEmail=email))


@pytest.mark.parametrize("phone", ["abcdefghij", "98765abc10", "+91 98ABC54321", "12345"])
def test_booking_rejects_invalid_phone_numbers(phone):
    with pytest.raises(ValidationError):
        BookingCreate.model_validate(booking_payload(passengerPhone=phone))


@pytest.mark.parametrize("phone", ["abcdefghij", "98765abc10", "+91 98ABC54321", "12345"])
def test_enquiry_rejects_invalid_phone_numbers(phone):
    with pytest.raises(ValidationError):
        ServiceEnquiryCreate.model_validate(enquiry_payload(passengerPhone=phone))


def test_valid_customer_details_are_normalized_and_accepted():
    booking = BookingCreate.model_validate(
        booking_payload(passengerName="  Aariz   Farooqui  ", passengerPhone="+91 88649-31247")
    )
    assert booking.passenger_name == "Aariz Farooqui"
    assert booking.passenger_phone == "+918864931247"

    enquiry = ServiceEnquiryCreate.model_validate(
        enquiry_payload(passengerName="  Aariz   Farooqui  ", passengerPhone="+91 88649-31247")
    )
    assert enquiry.passenger_name == "Aariz Farooqui"
    assert enquiry.passenger_phone == "+918864931247"
