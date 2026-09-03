"""
WhatsApp Airport Services - "Flight Not Confirmed Yet" friendly path tests.

Locks in:
- The flight step offers Enter Flight Number / Flight Not Confirmed buttons
- Choosing "not confirmed" skips verification, reaches the booking, and
  creates NO fabricated flight data (flight_num = N/A on the Booking row)
- The summary shows "Flight: To be confirmed with our team"
- The officer notification flags FLIGHT TO BE CONFIRMED
- Priced/verified flow is unaffected (covered by existing suites)
"""

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from unittest.mock import MagicMock

from app.main import app  # noqa: F401
from app.database import Base, engine, SessionLocal
from app.models.whatsapp_models import WhatsAppConversation
from app.models.schema import Booking
from app.integrations.whatsapp.client import WhatsAppClient
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.providers.razorpay_provider import razorpay_provider as _rzp

Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def mock_network(monkeypatch):
    monkeypatch.setattr(WhatsAppClient, "_post_payload", MagicMock(return_value={"success": True, "message_id": "wamid.mock"}))
    monkeypatch.setattr(WhatsAppClient, "send_message", MagicMock(return_value={"success": True, "message_id": "wamid.m"}))
    monkeypatch.setattr(WhatsAppClient, "send_text_message", MagicMock(return_value={"success": True, "message_id": "wamid.t"}))
    monkeypatch.setattr(WhatsAppClient, "send_interactive_buttons", MagicMock(return_value={"success": True, "message_id": "wamid.b"}))
    monkeypatch.setattr(WhatsAppClient, "send_interactive_list", MagicMock(return_value={"success": True, "message_id": "wamid.l"}))
    monkeypatch.setattr(
        _rzp,
        "create_payment_link",
        lambda *a, **k: {
            "success": True,
            "payment_link_id": f"plink_{uuid.uuid4().hex[:10]}",
            "short_url": f"https://rzp.io/i/{uuid.uuid4().hex[:8]}",
            "order_id": None,
            "amount": 4500.0,
            "currency": "INR",
            "status": "created",
            "simulated": False,
            "expire_by": 9999999999,
            "notes": {"channel": "whatsapp"},
        },
    )
    monkeypatch.setattr(_rzp, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})


def test_flight_not_confirmed_path_end_to_end():
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "START"
        db.commit()

        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Hi")                    # category menu
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")                     # Airport Services
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "2")                     # Departure
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")                     # Domestic
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Delhi")                 # airport (+ terminal)
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")                     # terminal / package
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")                     # package -> flight step

        # The flight step offered the friendly buttons
        btn_call = WhatsAppClient.send_interactive_buttons.call_args
        assert btn_call is not None
        btn_titles = [b["title"] for b in (btn_call.kwargs.get("buttons") or btn_call[1].get("buttons") or [])]
        assert "Enter Flight Number" in btn_titles
        assert "Flight Not Confirmed" in btn_titles

        # Customer taps "Flight Not Confirmed"
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "", input_id="btn_flight_not_confirmed")
        assert res["status"] == "flight_later_selected"
        db.refresh(conv)
        assert conv.current_state == "DATE_SELECTION"
        assert conv.flight_details_json.get("flight_later") is True
        assert conv.flight_num is None

        # Continue: date -> pax -> name -> email -> phone -> notes
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "25/12/2026")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "2")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Aariz Khan")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "flightlater@shafsky-mail.com")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Same")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "None")

        # Summary shows the flight as pending confirmation
        sum_call = WhatsAppClient.send_interactive_buttons.call_args
        summary_body = sum_call.kwargs.get("body_text") or sum_call[1].get("body_text", "")
        assert "To be confirmed with our team" in summary_body

        # Confirm -> booking created, no payment block, no fabricated flight
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Confirm", input_id="btn_confirm_booking")
        db.refresh(conv)

        assert res["status"] == "payment_link_sent"
        assert conv.current_state == "WAITING_PAYMENT"
        assert conv.booking_ref
        assert conv.razorpay_payment_url

        booking = db.query(Booking).filter_by(booking_ref=conv.booking_ref).first()
        assert booking is not None
        assert booking.status.value == "PENDING"
        assert booking.flight_num == "N/A"

        # Officer notification flags the unconfirmed flight
        all_bodies = " ".join(
            str(c[0][1]) for c in WhatsAppClient.send_text_message.call_args_list
            if len(c[0]) > 1
        )
        assert "TO BE CONFIRMED with customer" in all_bodies
    finally:
        db.close()


def test_flight_later_same_day_domestic_blocked():
    """Flight-later + tomorrow (domestic 12h notice) must be rejected with guidance."""
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "START"
        db.commit()

        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Hi")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "2")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Delhi")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "", input_id="btn_flight_not_confirmed")
        assert res["status"] == "flight_later_selected"

        tomorrow = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%d/%m/%Y")
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, tomorrow)
        db.refresh(conv)
        assert res["status"] == "cutoff_violation"
        assert conv.current_state == "DATE_SELECTION"
        sent = WhatsAppClient.send_text_message.call_args
        body = sent.kwargs.get("body_text") or (sent[0][1] if len(sent[0]) > 1 else "")
        assert "hours in advance" in body
    finally:
        db.close()
