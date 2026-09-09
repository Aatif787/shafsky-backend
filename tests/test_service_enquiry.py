"""Tests for quote-only service enquiries (non-airport) appearing in admin bookings."""

from unittest.mock import MagicMock, patch
import uuid

import pytest
from fastapi import HTTPException

from app.services.booking_service import BookingService
from app.models.schema import BookingStatus


def _db_with_unique_ref():
    db = MagicMock()
    db.scalar.return_value = None

    def _commit():
        pass

    db.commit.side_effect = _commit
    db.refresh.side_effect = lambda obj: setattr(obj, "id", uuid.uuid4()) or setattr(
        obj, "created_at", None
    )
    return db


@patch("app.services.booking_service.NotificationService", create=True)
def test_create_hotel_enquiry_persists_pending_booking(_notify_mod):
    db = _db_with_unique_ref()

    with patch("app.services.notification_service.NotificationService") as ns:
        ns.notify_booking_created = MagicMock()
        booking = BookingService.create_service_enquiry(
            db,
            passenger_name="Aariz Test",
            passenger_email="aariz@customer-mail.test",
            passenger_phone="+919876543210",
            service_category="Travel Support",
            service_type="Hotel Booking",
            origin="Jaipur",
            destination="Jaipur",
            service_date="2026-10-01 to 2026-10-05",
            notes="Palace suite",
            details={"rooms": 2},
        )

    assert booking.service_category == "Travel Support"
    assert booking.service_type == "Hotel Booking"
    assert booking.status == BookingStatus.PENDING
    assert float(booking.total_amount) == 0.0
    assert booking.metadata_json.get("enquiry") is True
    assert booking.booking_ref.startswith("SHF-")
    db.add.assert_called_once()
    db.commit.assert_called()


def test_create_ground_transport_enquiry_requires_pickup_drop():
    db = _db_with_unique_ref()
    with pytest.raises(HTTPException) as exc:
        BookingService.create_service_enquiry(
            db,
            passenger_name="Driver Guest",
            passenger_email="guest@customer-mail.test",
            passenger_phone="9876543210",
            service_category="Ground Transport",
            service_type="Luxury Vehicles",
            origin=None,
            destination=None,
        )
    assert exc.value.status_code == 400


def test_airport_assistance_rejected_as_enquiry():
    db = _db_with_unique_ref()
    with pytest.raises(HTTPException) as exc:
        BookingService.create_service_enquiry(
            db,
            passenger_name="Airport Guest",
            passenger_email="guest@customer-mail.test",
            passenger_phone="9876543210",
            service_category="Airport Assistance",
            service_type="Meet & Greet",
            origin="DEL",
            destination="BOM",
        )
    assert exc.value.status_code == 400
