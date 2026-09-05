"""
WhatsApp Private Charter Enquiry flow tests.

Locks in the enterprise enquiry-only behavior:
- Charter service menu shows NO prices (no Rs 0 rows) for unpriced services
- Booking summary shows "Custom Quote" and a "Submit Enquiry" button
- Confirm creates a PrivateCharterRequest (SC reference, status REQUESTED)
  in the dedicated charter pipeline - NO Booking row, NO Razorpay link
- Priced services are unaffected (prices still shown)
"""

import uuid

import pytest
from unittest.mock import MagicMock

from app.main import app  # noqa: F401
from app.database import Base, engine, SessionLocal
from app.models.whatsapp_models import WhatsAppConversation
from app.models.charter_models import PrivateCharterRequest
from app.models.schema import Booking
from app.integrations.whatsapp.client import WhatsAppClient
from app.integrations.whatsapp import copy as wa_copy
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.services.service_config_service import ServiceConfigService

Base.metadata.create_all(bind=engine)

CHARTER_CATALOG = [
    {
        "id": "svc_charter_jet",
        "title": "Private Jet Charter",
        "name": "Private Jet Charter",
        "category": "Private Charter",
        "price": 0,
        "base_price": 0,
        "description": "Whole aircraft charter with tailored itinerary",
    }
]


@pytest.fixture(autouse=True)
def mock_network_and_catalog(monkeypatch):
    """No real WhatsApp network calls; deterministic charter catalog."""
    monkeypatch.setattr(WhatsAppClient, "_post_payload", MagicMock(return_value={"success": True, "message_id": "wamid.mock"}))
    monkeypatch.setattr(WhatsAppClient, "send_message", MagicMock(return_value={"success": True, "message_id": "wamid.m"}))
    monkeypatch.setattr(WhatsAppClient, "send_text_message", MagicMock(return_value={"success": True, "message_id": "wamid.t"}))
    monkeypatch.setattr(WhatsAppClient, "send_interactive_buttons", MagicMock(return_value={"success": True, "message_id": "wamid.b"}))
    monkeypatch.setattr(WhatsAppClient, "send_interactive_list", MagicMock(return_value={"success": True, "message_id": "wamid.l"}))
    monkeypatch.setattr(
        ServiceConfigService,
        "get_admin_catalog",
        classmethod(lambda cls, db: [dict(s) for s in CHARTER_CATALOG]),
    )


def test_charter_menu_hides_zero_prices():
    body = wa_copy.numbered_service_lines(CHARTER_CATALOG)
    assert "₹0" not in body
    assert "Private Jet Charter" in body
    priced = wa_copy.numbered_service_lines([{"title": "Airport Meet & Greet", "price": 4500}])
    assert "₹4,500" in priced
    # list rows: zero price -> title only; priced -> title + price
    assert wa_copy.list_row_title("Private Jet Charter", 0) == "Private Jet Charter"
    assert "₹4,500" in wa_copy.list_row_title("Airport Meet & Greet", 4500)


def test_charter_enquiry_flow_end_to_end():
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "START"
        db.commit()

        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Hi")                        # category menu
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "3")                         # Private Charter menu
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")                         # select jet -> origin prompt
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Delhi")                     # destination prompt
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Mumbai")                    # date prompt
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "25/12/2026")                # passengers prompt
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "2")                         # name prompt
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Aariz Khan")                # email prompt
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "aariz@shafsky-mail.com")    # phone prompt
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Same")                      # notes prompt
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Rahul Sharma")  # companion name
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "None")                      # booking summary

        # Summary shows Custom Quote pricing and the Submit Enquiry button
        buttons_call = WhatsAppClient.send_interactive_buttons.call_args
        assert buttons_call is not None
        summary_body = buttons_call.kwargs.get("body_text") or buttons_call[1].get("body_text", "")
        assert "Custom Quote / Team Assistance" in summary_body
        summary_buttons = buttons_call.kwargs.get("buttons") or buttons_call[1].get("buttons")
        assert any(b["title"] == "Submit Enquiry" for b in summary_buttons)

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Confirm", input_id="btn_confirm_booking")
        db.refresh(conv)

        # Enquiry registered in the dedicated charter pipeline
        assert res["status"] == "charter_enquiry_registered"
        assert conv.current_state == "AWAITING_QUOTE"
        assert conv.payment_status == "QUOTE_REQUESTED"
        assert conv.razorpay_payment_url is None
        assert conv.booking_ref and conv.booking_ref.startswith("SC-")

        req = db.query(PrivateCharterRequest).filter_by(request_reference=conv.booking_ref).first()
        assert req is not None
        assert req.origin == "Delhi"
        assert req.destination == "Mumbai"
        assert (req.passengers or {}).get("total") == 2
        assert req.status.value == "REQUESTED"
        assert req.preferred_contact_method == "PHONE_WHATSAPP"

        # NO Booking row may be created for charter enquiries
        # The enquiry must NOT have spawned a Booking row (no SHF booking for an SC reference)
        assert db.query(Booking).filter_by(booking_ref=conv.booking_ref).first() is None

        # Customer received the charter acknowledgement with the SC reference
        sent = WhatsAppClient.send_text_message.call_args_list
        bodies = " ".join(str(c[0][1]) for c in sent if len(c[0]) > 1)
        assert "charter enquiry" in bodies
        assert conv.booking_ref in bodies
        assert "Razorpay" not in bodies
    finally:
        db.close()
