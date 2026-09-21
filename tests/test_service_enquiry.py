"""Tests for quote-only service enquiries (non-airport) appearing in admin bookings."""

from unittest.mock import MagicMock, patch
import uuid
import pytest
from fastapi import HTTPException

from app.services.booking_service import BookingService
from app.models.schema import BookingStatus
from app.services.notification_service import NotificationService


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


# =====================================================================
# TEST 1: Valid Ground Transport enquiry -> 201 Created, stored,
# total_amount = 0, quote_only = true, WhatsApp notification attempted
# =====================================================================
def test_valid_ground_transport_enquiry_success_and_whatsapp_attempted():
    db = _db_with_unique_ref()

    with patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message") as mock_wa:
        mock_wa.return_value = {"success": True, "message_id": "wamid.test_123"}

        booking = BookingService.create_service_enquiry(
            db,
            passenger_name="Jane Chauffeur",
            passenger_email="jane@customer-mail.test",
            passenger_phone="+919876543210",
            service_category="Ground Transport",
            service_type="Luxury Sedan",
            origin="DEL Airport Terminal 3",
            destination="The Leela Palace, New Delhi",
            service_date="2026-10-20",
            notes="Need flight tracking and bottled water",
            details={
                "vehicle_id": "sedan-e-class",
                "vehicle_name": "Mercedes-Benz E-Class",
                "vehicle_category": "Luxury Vehicles",
                "provider": "Shafsky Fleet",
                "passenger_count": 2,
                "reference_price": 4500,
            },
        )

        assert booking.service_category == "Ground Transport"
        assert booking.service_type == "Luxury Sedan"
        assert booking.status == BookingStatus.PENDING
        assert float(booking.total_amount) == 0.0
        assert booking.metadata_json.get("enquiry") is True
        assert booking.metadata_json.get("quote_only") is True
        assert booking.booking_ref.startswith("SHF-")
        assert booking.origin_code == "DEL Airport Terminal 3"
        assert booking.dest_code == "The Leela Palace, New Delhi"
        db.add.assert_called_once()
        db.commit.assert_called()

        # Verify WhatsApp notification was attempted to officer phone
        mock_wa.assert_called_once()
        args, _ = mock_wa.call_args
        to_phone, msg_text = args[0], args[1]
        assert to_phone == "919999999999" or len(to_phone) >= 10
        assert "NEW TRANSPORT ENQUIRY" in msg_text
        assert booking.booking_ref in msg_text
        assert "Jane Chauffeur" in msg_text
        assert "+919876543210" in msg_text
        assert "jane@customer-mail.test" in msg_text
        assert "Mercedes-Benz E-Class" in msg_text
        assert "sedan-e-class" in msg_text
        assert "Luxury Vehicles" in msg_text
        assert "Shafsky Fleet" in msg_text
        assert "DEL Airport Terminal 3" in msg_text
        assert "The Leela Palace, New Delhi" in msg_text
        assert "2026-10-20" in msg_text
        assert "Passengers*: 2" in msg_text
        assert "REFERENCE PRICING — NOT FINAL QUOTE" in msg_text
        assert "Need flight tracking and bottled water" in msg_text


# =====================================================================
# TEST 2: WhatsApp failure -> enquiry still succeeds, no duplicate
# enquiry, failure logged safely
# =====================================================================
def test_whatsapp_failure_does_not_fail_enquiry():
    db = _db_with_unique_ref()

    with patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message") as mock_wa:
        mock_wa.side_effect = Exception("Meta Graph API Network Exception (HTTP 500)")

        booking = BookingService.create_service_enquiry(
            db,
            passenger_name="Resilient Customer",
            passenger_email="resilient@customer-mail.test",
            passenger_phone="+919876543211",
            service_category="Ground Transport",
            service_type="SUV",
            origin="Terminal 1, BOM",
            destination="Bandra Kurla Complex",
            service_date="2026-11-01",
            details={"vehicle_name": "Toyota Fortuner"},
        )

        # The enquiry must remain successfully stored despite WhatsApp outage
        assert booking is not None
        assert booking.service_category == "Ground Transport"
        assert booking.status == BookingStatus.PENDING
        assert float(booking.total_amount) == 0.0
        assert db.add.call_count == 1  # No duplicate enquiry created


# =====================================================================
# TEST 3: Missing pickup -> validation failure (HTTP 400)
# =====================================================================
def test_missing_pickup_validation_failure():
    db = _db_with_unique_ref()
    with pytest.raises(HTTPException) as exc:
        BookingService.create_service_enquiry(
            db,
            passenger_name="No Pickup Guest",
            passenger_email="nopickup@customer-mail.test",
            passenger_phone="+919876543212",
            service_category="Ground Transport",
            service_type="Luxury Vehicles",
            origin=None,
            destination="Connaught Place, Delhi",
            details={},
        )
    assert exc.value.status_code == 400
    assert "pickup" in exc.value.detail.lower()


# =====================================================================
# TEST 4: Missing dropoff -> validation failure (HTTP 400)
# =====================================================================
def test_missing_dropoff_validation_failure():
    db = _db_with_unique_ref()
    with pytest.raises(HTTPException) as exc:
        BookingService.create_service_enquiry(
            db,
            passenger_name="No Dropoff Guest",
            passenger_email="nodrop@customer-mail.test",
            passenger_phone="+919876543213",
            service_category="Ground Transport",
            service_type="Executive Van",
            origin="DEL Airport",
            destination=None,
            details={},
        )
    assert exc.value.status_code == 400
    assert "dropoff" in exc.value.detail.lower()


# =====================================================================
# TEST 5: Vehicle details are preserved
# =====================================================================
def test_vehicle_details_are_preserved():
    db = _db_with_unique_ref()

    with patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message"):
        booking = BookingService.create_service_enquiry(
            db,
            passenger_name="VIP Executive",
            passenger_email="vip@customer-mail.test",
            passenger_phone="+919876543214",
            service_category="Ground Transport",
            service_type="Ultra Luxury",
            origin="Kempegowda Airport Bangalore",
            destination="UB City, Bangalore",
            service_date="2026-10-25",
            notes="Chilled sparkling water requested",
            details={
                "vehicle_id": "rolls-royce-ghost-01",
                "vehicle_name": "Rolls-Royce Ghost",
                "vehicle_category": "Ultra Luxury",
                "provider": "Shafsky Elite Fleet",
                "passenger_count": 3,
            },
        )

        # Verify details preserved in service_options
        assert booking.service_options["vehicle_id"] == "rolls-royce-ghost-01"
        assert booking.service_options["vehicle_name"] == "Rolls-Royce Ghost"
        assert booking.service_options["vehicle_category"] == "Ultra Luxury"
        assert booking.service_options["provider"] == "Shafsky Elite Fleet"
        assert booking.service_options["passenger_count"] == 3

        # Verify details preserved in metadata_json
        meta_details = booking.metadata_json.get("details", {})
        assert meta_details["vehicle_id"] == "rolls-royce-ghost-01"
        assert meta_details["vehicle_name"] == "Rolls-Royce Ghost"
        assert meta_details["provider"] == "Shafsky Elite Fleet"
        assert booking.notes == "Chilled sparkling water requested"


# =====================================================================
# TEST 6: Reference pricing is stored only as informational data
# =====================================================================
def test_reference_pricing_is_informational_only():
    db = _db_with_unique_ref()

    with patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message") as mock_wa:
        booking = BookingService.create_service_enquiry(
            db,
            passenger_name="Pricing Test Guest",
            passenger_email="pricing@customer-mail.test",
            passenger_phone="+919876543215",
            service_category="Ground Transport",
            service_type="Sedan",
            origin="DEL T3",
            destination="Noida Sector 62",
            details={
                "reference_price": 8500.0,
                "estimated_price": 8500.0,
            },
        )

        # Crucial check: Authoritative amount in database MUST be 0.0
        assert float(booking.total_amount) == 0.0
        assert booking.metadata_json["quote_only"] is True

        # Informational pricing preserved in options / details
        assert booking.service_options["reference_price"] == 8500.0
        assert booking.metadata_json["details"]["reference_price"] == 8500.0

        # Verify label in WhatsApp message
        mock_wa.assert_called_once()
        msg_text = mock_wa.call_args[0][1]
        assert "REFERENCE PRICING — NOT FINAL QUOTE" in msg_text
        assert "8,500.00" in msg_text or "8500" in msg_text


# =====================================================================
# TEST 7: Payment service is NOT called
# =====================================================================
def test_payment_service_is_not_called():
    db = _db_with_unique_ref()

    with patch("app.services.payment_service.PaymentService.initiate_payment") as mock_payment_init, \
         patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message"):

        booking = BookingService.create_service_enquiry(
            db,
            passenger_name="No Payment Guest",
            passenger_email="nopayment@customer-mail.test",
            passenger_phone="+919876543216",
            service_category="Ground Transport",
            service_type="Luxury Van",
            origin="Hyderabad Airport RGIA",
            destination="Hitec City",
            details={"vehicle_name": "Toyota Vellfire", "reference_price": 15000},
        )

        assert booking is not None
        # PaymentService must never be invoked for enquiries
        mock_payment_init.assert_not_called()
        assert float(booking.total_amount) == 0.0


# =====================================================================
# TEST 8: Non-Ground-Transport enquiries are NOT affected
# =====================================================================
def test_non_ground_transport_enquiries_not_affected():
    db = _db_with_unique_ref()

    with patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message") as mock_wa:
        # Hotel Booking (Travel Support)
        hotel_booking = BookingService.create_service_enquiry(
            db,
            passenger_name="Hotel Guest",
            passenger_email="hotel@customer-mail.test",
            passenger_phone="+919876543217",
            service_category="Travel Support",
            service_type="Hotel Booking",
            origin="Jaipur",
            destination="Jaipur",
            service_date="2026-10-01 to 2026-10-05",
            notes="Palace suite",
            details={"rooms": 2},
        )

        assert hotel_booking.service_category == "Travel Support"
        assert hotel_booking.status == BookingStatus.PENDING
        assert float(hotel_booking.total_amount) == 0.0
        # Transport WhatsApp dispatch MUST NOT be triggered for Travel Support
        mock_wa.assert_not_called()


# =====================================================================
# Existing Guards Preserved
# =====================================================================
def test_airport_assistance_rejected_as_enquiry():
    db = _db_with_unique_ref()
    with pytest.raises(HTTPException) as exc:
        BookingService.create_service_enquiry(
            db,
            passenger_name="Airport Guest",
            passenger_email="guest@customer-mail.test",
            passenger_phone="+919876543218",
            service_category="Airport Assistance",
            service_type="Meet & Greet",
            origin="DEL",
            destination="BOM",
        )
    assert exc.value.status_code == 400


# =====================================================================
# End-to-End HTTP Endpoint Test: POST /api/bookings/enquiries
# =====================================================================
def test_api_endpoint_ground_transport_enquiry():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_db

    mock_db = _db_with_unique_ref()
    app.dependency_overrides[get_db] = lambda: mock_db
    try:
        client = TestClient(app)
        with patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message") as mock_wa:
            mock_wa.return_value = {"success": True, "message_id": "wamid.http_test"}

            payload = {
                "passengerName": "HTTP Test Guest",
                "passengerEmail": "httptest@shafsky-guest.com",
                "passengerPhone": "+919876543299",
                "serviceCategory": "Ground Transport",
                "serviceType": "Executive Van",
                "origin": "DEL Terminal 3",
                "destination": "Gurugram Cyber City",
                "serviceDate": "2026-11-15",
                "notes": "Flight AI-102 arrival",
                "details": {
                    "vehicle_id": "van-alphard-01",
                    "vehicle_name": "Toyota Alphard",
                    "passenger_count": 4,
                    "reference_price": 9500,
                },
            }

            response = client.post("/api/bookings/enquiries", json=payload)
            assert response.status_code == 201
            body = response.json()
            assert body["success"] is True
            assert body["data"]["serviceCategory"] == "Ground Transport"
            assert body["data"]["serviceType"] == "Executive Van"
            assert body["data"]["status"] == "PENDING"
            assert body["data"]["bookingRef"].startswith("SHF-")

            # Verify WhatsApp alert dispatched to team
            mock_wa.assert_called_once()
            msg_text = mock_wa.call_args[0][1]
            assert "Toyota Alphard" in msg_text
            assert "Gurugram Cyber City" in msg_text
            assert "REFERENCE PRICING — NOT FINAL QUOTE" in msg_text
    finally:
        app.dependency_overrides.pop(get_db, None)



