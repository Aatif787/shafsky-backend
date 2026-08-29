"""Focused tests for WhatsApp customer-email capture validation."""

import uuid
from unittest.mock import patch, MagicMock

import pytest

from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.utils.customer_email import (
    is_acceptable_customer_email as is_acceptable_whatsapp_customer_email,
    REAL_EMAIL_HELP as _REAL_EMAIL_HELP,
    EMAIL_FORMAT_HELP as _EMAIL_FORMAT_HELP,
)


@pytest.fixture(autouse=True)
def mock_whatsapp_network(monkeypatch):
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient._post_payload",
        MagicMock(return_value={"success": True, "message_id": "wamid.mocked"}),
    )
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_message",
        MagicMock(return_value={"success": True, "message_id": "wamid.mocked"}),
    )
    yield


@pytest.mark.parametrize(
    "email",
    [
        "traveler@gmail.com",
        "ops.desk@shafskyaviation.com",
    ],
)
def test_acceptable_customer_emails(email):
    ok, reason = is_acceptable_whatsapp_customer_email(email)
    assert ok is True
    assert reason == "ok"


@pytest.mark.parametrize(
    "email",
    [
        "user@example.com",
        "user@example.org",
        "user@example.net",
        "guest@example.com",
        "test@example.com",
        "demo@example.com",
        "name@example.com",
        "Guest@Example.COM",
    ],
)
def test_reserved_or_placeholder_emails_rejected(email):
    ok, reason = is_acceptable_whatsapp_customer_email(email)
    assert ok is False
    assert reason == "reserved_or_placeholder"


@pytest.mark.parametrize(
    "email",
    [
        "not_an_email",
        "missing-at-sign.com",
        "@nodomain.com",
        "a@",
        "",
    ],
)
def test_invalid_email_syntax_rejected(email):
    ok, reason = is_acceptable_whatsapp_customer_email(email)
    assert ok is False
    assert reason == "invalid_syntax"


@patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message")
def test_state_accepts_gmail_style_and_advances(mock_text):
    from app.database import SessionLocal

    mock_text.return_value = {"success": True, "message_id": "wamid.ok"}
    phone = f"9198{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "CUSTOMER_EMAIL"
        conv.customer_name = "Ada Lovelace"
        conv.selected_service_name = "Meet & Greet"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "traveler@gmail.com")
        db.refresh(conv)
        assert res["success"] is True
        assert res["status"] == "phone_prompt_sent"
        assert conv.customer_email == "traveler@gmail.com"
        assert conv.current_state == "CUSTOMER_PHONE"
        assert conv.customer_name == "Ada Lovelace"
        assert conv.selected_service_name == "Meet & Greet"
    finally:
        db.close()


@patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message")
def test_state_accepts_custom_domain_and_advances(mock_text):
    from app.database import SessionLocal

    mock_text.return_value = {"success": True, "message_id": "wamid.ok"}
    phone = f"9198{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "CUSTOMER_EMAIL"
        conv.customer_name = "Grace Hopper"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "ops.desk@shafskyaviation.com")
        db.refresh(conv)
        assert res["success"] is True
        assert conv.customer_email == "ops.desk@shafskyaviation.com"
        assert conv.current_state == "CUSTOMER_PHONE"
    finally:
        db.close()


@pytest.mark.parametrize(
    "bad_email,expected_help",
    [
        ("user@example.com", _REAL_EMAIL_HELP),
        ("user@example.org", _REAL_EMAIL_HELP),
        ("user@example.net", _REAL_EMAIL_HELP),
        ("guest@example.com", _REAL_EMAIL_HELP),
        ("name@example.com", _REAL_EMAIL_HELP),
        ("not_an_email", _EMAIL_FORMAT_HELP),
    ],
)
@patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message")
def test_state_rejects_and_stays_in_email_capture(mock_text, bad_email, expected_help):
    from app.database import SessionLocal

    mock_text.return_value = {"success": True, "message_id": "wamid.ok"}
    phone = f"9198{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "CUSTOMER_EMAIL"
        conv.customer_name = "Kept Name"
        conv.selected_airport_iata = "DEL"
        conv.flight_num = "AI101"
        conv.customer_email = None
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, bad_email)
        db.refresh(conv)
        assert res["success"] is False
        assert res["status"] == "invalid_email"
        assert conv.current_state == "CUSTOMER_EMAIL"
        assert conv.customer_email is None
        assert conv.customer_name == "Kept Name"
        assert conv.selected_airport_iata == "DEL"
        assert conv.flight_num == "AI101"
        mock_text.assert_called()
        assert mock_text.call_args[0][1] == expected_help
    finally:
        db.close()
