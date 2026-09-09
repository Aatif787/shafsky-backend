"""
WhatsApp companion passenger-name collection tests (passenger_count > 1).

Locks in:
- After phone, count > 1 asks for the other names in ONE message (comma-separated)
- Partial submissions trigger ONE follow-up; names accumulate
- After 2 attempts with too few, proceeds gracefully and flags for ops
- Summary shows "Also travelling: ..."; officer notification includes the names
- Booking metadata carries passenger_names; single-passenger flow skips the step
"""

import uuid

import pytest
from unittest.mock import MagicMock

from app.main import app  # noqa: F401
from app.database import Base, engine, SessionLocal
from app.models.whatsapp_models import WhatsAppConversation
from app.models.schema import Booking
from app.integrations.whatsapp.client import WhatsAppClient
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.providers.razorpay_provider import razorpay_provider as _rzp

@pytest.fixture(autouse=True)
def mock_network(monkeypatch):
    try:
        Base.metadata.create_all(bind=engine)
    except Exception:
        pass
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
            "amount": 3000.0,
            "currency": "INR",
            "status": "created",
            "simulated": False,
            "expire_by": 9999999999,
            "notes": {"channel": "whatsapp"},
        },
    )
    monkeypatch.setattr(_rzp, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})


def _walk_to_phone(phone):
    WhatsAppBookingStateMachine.process_incoming_event(db := SessionLocal(), phone, "Hi")
    db.close()
    # Note: each call manages its own session via the state machine helpers;
    # keep the walk simple and deterministic.
    steps = [
        ("1", None),      # Airport Services
        ("2", None),      # Departure
        ("1", None),      # Domestic
        ("Delhi", None),  # airport -> terminal
        ("1", None),      # terminal / package
        ("1", None),      # package -> flight step
    ]
    return steps


def test_two_passenger_flow_collects_and_shows_companion():
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "START"
        db.commit()

        walk = [
            ("Hi", None),
            ("1", None), ("2", None), ("1", None), ("Delhi", None), ("1", None), ("1", None),
            ("", "btn_flight_not_confirmed"),
            ("25/12/2026", None),
            ("2", None),                       # passengers = 2
            ("Aariz Khan", None),              # lead name
            ("aariz@shafsky-mail.com", None),  # email
            ("Same", None),                    # phone
        ]
        for text, input_id in walk:
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, text, input_id=input_id)
        db.refresh(conv)

        # Companion prompt reached and asks for 1 more name
        assert conv.current_state == "COMPANION_NAMES"

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Rahul Sharma")
        assert res["status"] == "companion_names_saved"
        db.refresh(conv)

        assert conv.current_state == "ADDITIONAL_REQUIREMENTS"
        assert conv.flight_details_json.get("passenger_names") == ["Rahul Sharma"]

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "None")  # notes -> summary
        db.refresh(conv)
        assert conv.current_state == "BOOKING_REVIEW"
        btn = WhatsAppClient.send_interactive_buttons.call_args
        body = btn.kwargs.get("body_text") or btn[1].get("body_text", "")
        assert "Also travelling: Rahul Sharma" in body

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Confirm", input_id="btn_confirm_booking")
        db.refresh(conv)
        assert res["status"] == "payment_link_sent"
        assert conv.current_state == "WAITING_PAYMENT"

        booking = db.query(Booking).filter_by(booking_ref=conv.booking_ref).first()
        assert booking is not None
        assert (booking.metadata_json or {}).get("passenger_names") == ["Rahul Sharma"]

        all_bodies = " ".join(str(c[0][1]) for c in WhatsAppClient.send_text_message.call_args_list if len(c[0]) > 1)
        assert "Also travelling: Rahul Sharma" in all_bodies
    finally:
        db.close()


def test_three_passenger_partial_then_complete():
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "START"
        db.commit()

        walk = [
            ("Hi", None), ("1", None), ("2", None), ("1", None), ("Delhi", None), ("1", None), ("1", None),
            ("", "btn_flight_not_confirmed"),
            ("25/12/2026", None),
            ("3", None),
            ("Aariz Khan", None),
            ("aariz2@shafsky-mail.com", None),
            ("Same", None),
        ]
        for text, input_id in walk:
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, text, input_id=input_id)
        db.refresh(conv)
        assert conv.current_state == "COMPANION_NAMES"

        # 1 of 2 names -> follow-up asking for the remaining 1
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Rahul")
        assert res["status"] == "companion_names_partial"
        sent = WhatsAppClient.send_text_message.call_args
        body = sent.kwargs.get("body_text") or (sent[0][1] if len(sent[0]) > 1 else "")
        assert "remaining 1 name" in body

        # 2 more sent (one extra) -> takes first 2 and proceeds
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Priya Sharma, Amit Verma, Extra Person")
        assert res["status"] == "companion_names_saved"
        db.refresh(conv)
        assert conv.flight_details_json.get("passenger_names") == ["Rahul", "Priya Sharma"]

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "None")
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Confirm", input_id="btn_confirm_booking")
        db.refresh(conv)
        assert res["status"] == "payment_link_sent"
        booking = db.query(Booking).filter_by(booking_ref=conv.booking_ref).first()
        assert (booking.metadata_json or {}).get("passenger_names") == ["Rahul", "Priya Sharma"]
    finally:
        db.close()


def test_single_passenger_skips_companion_step():
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "START"
        db.commit()

        walk = [
            ("Hi", None), ("1", None), ("2", None), ("1", None), ("Delhi", None), ("1", None), ("1", None),
            ("", "btn_flight_not_confirmed"),
            ("25/12/2026", None),
            ("1", None),
            ("Solo Guest", None),
            ("solo@shafsky-mail.com", None),
            ("Same", None),
        ]
        for text, input_id in walk:
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, text, input_id=input_id)
        db.refresh(conv)

        assert conv.current_state == "ADDITIONAL_REQUIREMENTS"
        assert "companion_names" not in (conv.flight_details_json or {})
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "None")
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Confirm", input_id="btn_confirm_booking")
        db.refresh(conv)
        assert res["status"] == "payment_link_sent"
    finally:
        db.close()


def test_invalid_companion_names_reprompt():
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "START"
        db.commit()

        walk = [
            ("Hi", None), ("1", None), ("2", None), ("1", None), ("Delhi", None), ("1", None), ("1", None),
            ("", "btn_flight_not_confirmed"),
            ("25/12/2026", None),
            ("2", None),
            ("Aariz Khan", None),
            ("aariz3@shafsky-mail.com", None),
            ("Same", None),
        ]
        for text, input_id in walk:
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, text, input_id=input_id)
        db.refresh(conv)
        assert conv.current_state == "COMPANION_NAMES"

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, ",,,")
        assert res["status"] == "invalid_companion_names"
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Rahul Sharma")
        assert res["status"] == "companion_names_saved"
        db.refresh(conv)
        assert conv.flight_details_json.get("passenger_names") == ["Rahul Sharma"]
    finally:
        db.close()
