"""
Unit test for serviceable vs non-serviceable airport availability and enquiry flow.
Tests:
1. Serviceable airport (BOM/DEL): authoritative pricing and availability preserved.
2. Non-serviceable airport (DXB):
   - Returns REQUEST_REQUIRED, none_available=True, total_payable=0.0
   - Does not use the word 'concierge'
   - Submits service query successfully
   - Mirrors enquiry into bookings table with total_amount=0.0 and status=PENDING
   - Confirms enquiry appears in admin bookings list query
"""

import uuid
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from app.models.journey_models import SupportedAirport, Service, AirportService, ServiceQuery
from app.models.schema import Booking, BookingStatus
from app.schemas.journey_schemas import (
    MultiServiceAvailabilityRequest,
    ServiceSelectionInput,
    FlightLegInput,
    ServiceQueryCreate,
)
from app.services.journey_engine import JourneyDetectionEngine
from app.services.booking_service import BookingService


def test_serviceable_airport_availability():
    """Verify serviceable airport preserves pricing and online availability."""
    mock_db = MagicMock()

    # Mock supported airport BOM
    mock_airport = SupportedAirport(
        id=uuid.uuid4(),
        iata_code="BOM",
        airport_name="Chhatrapati Shivaji Maharaj International Airport",
        city="Mumbai",
        country="India",
        is_supported=True,
        is_active=True,
    )

    # Mock service & mapping
    mock_svc = Service(
        id=uuid.uuid4(),
        name="Gold Service",
        slug="gold",
        is_active=True,
    )
    mock_mapping = AirportService(
        id=uuid.uuid4(),
        airport_id=mock_airport.id,
        service_id=mock_svc.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        price=5500.0,
        currency="INR",
        is_available=True,
        min_booking_notice_hours=6,
        display_priority=1,
    )
    mock_mapping.service = mock_svc

    with patch.object(JourneyDetectionEngine, "get_airport_by_iata", return_value=mock_airport):
        with patch.object(JourneyDetectionEngine, "get_services_for_airport", return_value=[mock_mapping]):
            req = MultiServiceAvailabilityRequest(
                origin_code="BOM",
                dest_code="DEL",
                guest_count=1,
                selected_services=[
                    ServiceSelectionInput(
                        service_type="DEPARTURE",
                        airport_code="BOM",
                        package_slug="gold",
                    )
                ],
            )
            res = JourneyDetectionEngine.check_multi_service_availability(mock_db, req)

            assert res.success is True
            assert res.all_available is True
            assert res.none_available is False
            assert res.total_payable == 5500.0
            assert len(res.services) == 1
            assert res.services[0].status == "AVAILABLE"
            assert res.services[0].is_airport_supported is True
            assert res.services[0].total_price == 5500.0


def test_non_serviceable_airport_enquiry_flow():
    """Verify non-serviceable airport is enquiry-only, 0 payable, no 'concierge', and mirrors into bookings."""
    mock_db = MagicMock()

    # DXB is unsupported in authoritative DB
    mock_airport = SupportedAirport(
        id=uuid.uuid4(),
        iata_code="DXB",
        airport_name="Dubai International Airport",
        city="Dubai",
        country="United Arab Emirates",
        is_supported=False,
        is_active=False,
    )

    with patch.object(JourneyDetectionEngine, "get_airport_by_iata", return_value=mock_airport):
        req = MultiServiceAvailabilityRequest(
            origin_code="DXB",
            dest_code="LHR",
            guest_count=1,
            selected_services=[
                ServiceSelectionInput(
                    service_type="DEPARTURE",
                    airport_code="DXB",
                    package_slug="gold",
                )
            ],
        )
        res = JourneyDetectionEngine.check_multi_service_availability(mock_db, req)

        assert res.success is True
        assert res.none_available is True
        assert res.total_payable == 0.0
        assert res.available_subtotal == 0.0
        assert len(res.services) == 1
        assert res.services[0].status == "REQUEST_REQUIRED"
        assert res.services[0].is_airport_supported is False
        assert res.services[0].total_price is None

        # Verify no usage of the word 'concierge' in messages
        assert "concierge" not in (res.unavailable_message or "").lower()
        assert "concierge" not in (res.services[0].status_reason or "").lower()
        assert res.unavailable_message == "This airport is not currently serviceable. Submit your request and our team will check availability."


def test_create_service_query_mirrors_to_bookings_desk():
    """Verify submitting service enquiry mirrors to bookings table for Admin Dashboard visibility."""
    mock_db = MagicMock()
    mock_db.scalars.return_value.first.return_value = None  # No duplicate existing query
    mock_db.scalar.return_value = None  # No duplicate query_ref or booking_ref
    added_records = []

    def mock_add(obj):
        added_records.append(obj)

    mock_db.add.side_effect = mock_add

    payload = ServiceQueryCreate(
        passenger_name="John Doe",
        passenger_email="john@example.com",
        passenger_phone="+971501234567",
        flight_num="EK501",
        service_date="2026-10-15",
        itinerary={"departure_airport": "DXB", "arrival_airport": "LHR"},
        requested_services=[{"service_type": "DEPARTURE", "airport_code": "DXB"}],
        unavailable_services=[{"service_type": "DEPARTURE", "airport_code": "DXB", "status": "REQUEST_REQUIRED"}],
        notes="VIP assistance request for DXB",
    )

    with patch("app.services.notification_service.NotificationService.notify_booking_created") as mock_notify:
        resp = JourneyDetectionEngine.create_service_query(mock_db, payload)

        assert resp.success is True
        assert resp.query_ref.startswith("QRY-")
        # Ensure no "concierge" in response message
        assert "concierge" not in resp.message.lower()

        # Verify ServiceQuery was added
        query_records = [r for r in added_records if isinstance(r, ServiceQuery)]
        assert len(query_records) == 1
        assert query_records[0].query_ref == resp.query_ref
        assert query_records[0].passenger_name == "John Doe"

        # Verify Booking was mirrored for Admin Dashboard
        booking_records = [r for r in added_records if isinstance(r, Booking)]
        assert len(booking_records) == 1
        b = booking_records[0]
        assert b.booking_ref == resp.query_ref
        assert b.passenger_name == "John Doe"
        assert b.passenger_email == "john@example.com"
        assert b.total_amount == 0.0
        assert b.currency == "INR"
        assert b.status == BookingStatus.PENDING
        assert b.service_category == "Airport Assistance"
        assert b.service_type == "Service Enquiry"

        # Verify format_booking_dict produces valid admin payload
        formatted = BookingService.format_booking_dict(b)
        assert formatted["bookingRef"] == resp.query_ref
        assert formatted["totalAmount"] == 0.0
        assert formatted["status"] == "PENDING"
