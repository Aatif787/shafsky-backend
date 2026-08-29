import pytest
import uuid
from unittest.mock import patch, MagicMock
from app.database import SessionLocal, Base, engine
from app.models.whatsapp_models import WhatsAppConversation
from app.models.schema import Booking
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine


@pytest.fixture(autouse=True)
def mock_razorpay_payment_link(monkeypatch):
    def fake_create(*args, **kwargs):
        ref = kwargs.get("booking_ref") or kwargs.get("reference_id") or "x"
        return {
            "success": True,
            "payment_link_id": f"plink_test_{uuid.uuid4().hex[:10]}",
            "short_url": f"https://rzp.io/i/wa_{uuid.uuid4().hex[:8]}",
            "order_id": None,
            "amount": kwargs.get("amount"),
            "currency": kwargs.get("currency", "INR"),
            "simulated": False,
            "notes": {"booking_ref": ref, "channel": "whatsapp"},
        }

    monkeypatch.setattr(
        "app.providers.razorpay_provider.razorpay_provider.create_payment_link",
        fake_create,
    )
    monkeypatch.setattr(
        "app.providers.razorpay_provider.razorpay_provider.list_payment_links_by_reference",
        lambda reference_id: {"success": True, "items": []},
    )
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify_booking_created",
        lambda *a, **k: {"status": "mocked"},
    )
    yield


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.services.notification_service.NotificationService.notify_booking_created")
def test_whatsapp_multiple_passengers_scales_price(mock_notify, mock_text, mock_buttons):
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        
        # 1. User selects a service with base unit price 3000.0
        selected_svc = {"id": "silver", "title": "Silver Meet & Greet", "base_price": 3000.0}
        conv.selected_service_id = "silver"
        conv.selected_service_name = "Silver Meet & Greet"
        conv.total_amount = 3000.0
        conv.passenger_count = 1
        conv.flight_details_json = {"unit_price": 3000.0, "base_price": 3000.0, "journey_type": "DEPARTURE", "travel_type": "DOMESTIC", "origin_iata": "DEL", "destination_iata": "BOM", "departure_scheduled": "2026-08-31T18:00:00+00:00", "verification_status": "verified", "verification_provider": "AVIATIONSTACK"}
        conv.selected_airport_iata = "DEL"
        conv.selected_airport_name = "Indira Gandhi International Airport"
        conv.flight_num = "AI101"
        conv.booking_date = "31 August 2026"
        conv.current_state = "PASSENGER_COUNT"
        db.commit()

        # 2. User enters 3 passengers
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "3")
        db.refresh(conv)
        assert conv.passenger_count == 3
        assert conv.total_amount == 9000.0
        assert conv.current_state == "CUSTOMER_NAME"

        # 3. Enter Customer Name
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "John Doe")
        db.refresh(conv)
        assert conv.customer_name == "John Doe"
        assert conv.current_state == "CUSTOMER_EMAIL"

        # 4. Enter Customer Email
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "john.doe@gmail.com")
        db.refresh(conv)
        assert conv.customer_email == "john.doe@gmail.com"
        assert conv.current_state == "CUSTOMER_PHONE"

        # 5. Enter Customer Phone
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Same")
        db.refresh(conv)
        assert conv.customer_phone == phone
        assert conv.current_state == "ADDITIONAL_REQUIREMENTS"

        # 6. Enter Notes -> Triggers Booking Summary
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "None")
        db.refresh(conv)
        assert conv.current_state == "BOOKING_REVIEW"
        assert conv.total_amount == 9000.0

        # Verify Booking Summary message contains ₹9,000 and 3 passengers
        assert mock_buttons.called or mock_text.called
        summary_call_args = mock_buttons.call_args[1]["body_text"] if mock_buttons.called else mock_text.call_args[0][1]
        assert "3" in summary_call_args
        assert "9,000" in summary_call_args

        # 7. User Confirms Booking
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
        db.refresh(conv)
        assert conv.current_state == "WAITING_PAYMENT"

        # Verify DB Booking record has total_amount = 9000.0
        booking = db.query(Booking).filter(Booking.booking_ref == conv.booking_ref).first()
        assert booking is not None
        assert float(booking.total_amount) == 9000.0

        # Verify notify_booking_created email payload received 9000.0
        assert mock_notify.called
        email_ctx = mock_notify.call_args[0][1]
        assert email_ctx["total_amount"] == 9000.0
        assert email_ctx["booking_ref"] == conv.booking_ref
        assert email_ctx["passenger_count"] == 3

    finally:
        db.close()


@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.services.notification_service.NotificationService.notify_booking_created")
def test_whatsapp_single_passenger_keeps_base_price(mock_notify, mock_text, mock_buttons):
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        
        conv.selected_service_id = "silver"
        conv.selected_service_name = "Silver Meet & Greet"
        conv.total_amount = 3000.0
        conv.passenger_count = 1
        conv.flight_details_json = {"unit_price": 3000.0, "base_price": 3000.0, "journey_type": "DEPARTURE", "travel_type": "DOMESTIC", "origin_iata": "DEL", "destination_iata": "BOM", "departure_scheduled": "2026-08-31T18:00:00+00:00", "verification_status": "verified", "verification_provider": "AVIATIONSTACK"}
        conv.selected_airport_iata = "DEL"
        conv.selected_airport_name = "Indira Gandhi International Airport"
        conv.flight_num = "AI101"
        conv.booking_date = "31 August 2026"
        conv.current_state = "PASSENGER_COUNT"
        db.commit()

        # 1 passenger
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")
        db.refresh(conv)
        assert conv.passenger_count == 1
        assert conv.total_amount == 3000.0

        # Name, email, phone, notes
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Single Traveler")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "single.traveler@gmail.com")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Same")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "None")
        db.refresh(conv)

        assert conv.total_amount == 3000.0
        summary_call_args = mock_buttons.call_args[1]["body_text"] if mock_buttons.called else mock_text.call_args[0][1]
        assert "3,000" in summary_call_args

        # Confirm
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
        db.refresh(conv)

        booking = db.query(Booking).filter(Booking.booking_ref == conv.booking_ref).first()
        assert booking is not None
        assert float(booking.total_amount) == 3000.0
        assert mock_notify.call_args[0][1]["total_amount"] == 3000.0
    finally:
        db.close()


def test_email_templates_show_shafsky_aviation_services_and_multi_passenger_amount():
    from app.services.notification_templates import NotificationTemplateEngine

    payload = {
        "booking_ref": "SHF-TEST-888",
        "passengerName": "John Doe",
        "passengerEmail": "john@example.com",
        "passengerPhone": "+919876543210",
        "passengerCount": 3,
        "airportCode": "DEL",
        "journeyType": "DEPARTURE",
        "service_name": "Silver Meet & Greet",
        "flightNum": "AI101",
        "originCode": "DEL",
        "destCode": "BOM",
        "departureTime": "2026-08-25T10:00:00+00:00",
        "totalAmount": 9000.0,
        "currency": "INR",
        "status": "PENDING",
    }

    # 1. Received Email Template
    received = NotificationTemplateEngine.render_template("BOOKING_RECEIVED", payload)
    assert "Shafsky Aviation Services" in received["subject"]
    assert "Shafsky Aviation Services" in received["html"]
    assert "INR 9,000.00" in received["html"]
    assert "Passengers:</strong> 3" in received["html"]

    # 2. Confirmed Email Template
    confirmed = NotificationTemplateEngine.render_template("BOOKING_CONFIRMATION", payload)
    assert "Shafsky Aviation Services" in confirmed["subject"]
    assert "Shafsky Aviation Services" in confirmed["html"]
    assert "INR 9,000.00" in confirmed["html"]
    assert "Passengers:</strong> 3" in confirmed["html"]

    # 3. Admin New Booking Email Template
    admin = NotificationTemplateEngine.render_template("ADMIN_NEW_BOOKING", payload)
    assert "INR 9,000.00" in admin["html"]
    assert "Passengers:</strong> 3" in admin["html"]

