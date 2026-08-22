"""
Comprehensive Unit Test Suite for Shafsky Aviation WhatsApp Booking Automation System.
Covers all core acceptance test scenarios: 4-option menu, database airport resolution,
local flight validation, 30m session expiry, back/cancel/restart, and payment-free booking creation.
"""

import os
import json
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import hmac
import hashlib

from app.main import app
from app.integrations.whatsapp.client import whatsapp_client
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine, WhatsAppService, OFFICIAL_CATEGORIES
from app.models.schema import Booking, BookingStatus
from sqlalchemy import select

client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_all_whatsapp_network_calls(monkeypatch):
    """Autouse fixture to mock all WhatsApp network requests preventing Graph API latency/timeouts."""
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient._post_payload", MagicMock(return_value={"success": True, "message_id": "wamid.mocked"}))
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.send_message", MagicMock(return_value={"success": True, "message_id": "wamid.mocked"}))
    yield


# 1. WhatsApp Webhook Verification GET
def test_scenario_01_webhook_verification_get(monkeypatch):
    monkeypatch.setenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "shafsky_wa_verify_token")
    res = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "shafsky_wa_verify_token",
            "hub.challenge": "ch_12345"
        }
    )
    assert res.status_code == 200
    assert res.text == "ch_12345"


def signed_whatsapp_post(payload: dict):
    raw = json.dumps(payload).encode("utf-8")
    whatsapp_client._load_config()
    secret = (
        whatsapp_client.app_secret
        or whatsapp_client.meta_app_secret
        or whatsapp_client.general_app_secret
        or os.getenv("WHATSAPP_APP_SECRET", "")
        or os.getenv("META_APP_SECRET", "")
        or os.getenv("APP_SECRET", "")
    )
    sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/whatsapp/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={sig}"}
    )


# 2. WhatsApp Incoming Text ("Hi") -> Welcome & 4-Option Menu List
def test_scenario_02_incoming_hi_welcome():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        test_phone = f"91999{uuid.uuid4().int % 10**7:07d}"
        unique_wamid = f"wamid.hi_{uuid.uuid4().hex[:8]}"

        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "messages": [
                                    {
                                        "from": test_phone,
                                        "id": unique_wamid,
                                        "type": "text",
                                        "text": {"body": "Hi"}
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }

        res = signed_whatsapp_post(payload)
        assert res.status_code == 200
        assert res.json()["data"]["messages_handled"] == 1
        assert res.json()["data"]["results"][0]["result"]["status"] == "category_menu_sent"

        # Also verify menu items formatting directly via state machine helper
        with patch.object(whatsapp_client, "send_interactive_list") as mock_send_list:
            mock_send_list.return_value = {"success": True, "message_id": "wamid.welcome_01"}
            conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, test_phone)
            WhatsAppBookingStateMachine._send_category_menu(db, conv)
            assert mock_send_list.called
            args, kwargs = mock_send_list.call_args
            assert "1️⃣ Airport Services" in kwargs.get("body_text", "")
            assert "4️⃣ Hotel & Transportation" in kwargs.get("body_text", "")
    finally:
        db.close()


# 3. Interactive Button Response
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
def test_scenario_03_button_response_handling(mock_send_buttons):
    mock_send_buttons.return_value = {"success": True, "message_id": "wamid.btn_01"}

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": "919999988888",
                                    "id": "wamid.btn_reply_001",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {"id": "btn_confirm_airport", "title": "Confirm"}
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    res = signed_whatsapp_post(payload)
    assert res.status_code == 200


# 4. Interactive List Response (4-Option Selection)
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
def test_scenario_04_list_response_handling(mock_send_buttons):
    mock_send_buttons.return_value = {"success": True, "message_id": "wamid.list_01"}

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": "919999988888",
                                    "id": "wamid.list_reply_001",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "list_reply",
                                        "list_reply": {"id": "cat_airport", "title": "Airport Services"}
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    res = signed_whatsapp_post(payload)
    assert res.status_code == 200


# 5. Conversation State Transition (Start -> Category Selection)
def test_scenario_05_state_transitions():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, "918888877777")
        conv.current_state = "START"
        db.commit()
        assert conv.current_state == "START"

        WhatsAppBookingStateMachine.process_incoming_event(db, "918888877777", "Hi")
        db.refresh(conv)
        assert conv.current_state == "CATEGORY_SELECTION"
    finally:
        db.close()


# 6. Database Airport Resolution (DEL, Bhubaneswar / BBI)
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_06_airport_resolution(mock_text, mock_list):
    mock_list.return_value = {"success": True, "message_id": "wamid.airport_res"}
    mock_text.return_value = {"success": True, "message_id": "wamid.airport_txt"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, "917777766666")
        conv.flight_details_json = {"journey_type": "DEPARTURE", "travel_type": "DOMESTIC"}
        conv.current_state = "AIRPORT_SELECTION"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, "917777766666", "Delhi")
        db.refresh(conv)
        assert conv.selected_airport_iata == "DEL"
        assert conv.current_state == "SERVICE_SELECTION"

        # Test BBI resolution
        conv.current_state = "AIRPORT_SELECTION"
        db.commit()
        res_bbi = WhatsAppBookingStateMachine.process_incoming_event(db, "917777766666", "Bhubaneswar")
        db.refresh(conv)
        assert conv.selected_airport_iata == "BBI"
        assert conv.current_state == "SERVICE_SELECTION"
    finally:
        db.close()


# 7. Airport Services Flow: Journey Type -> Travel Type -> Airport -> Service -> Flight Input
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_07_airport_services_flow(mock_text):
    mock_text.return_value = {"success": True, "message_id": "wamid.scen07"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, "916666655555")
        conv.selected_category = "Airport Services"
        conv.selected_airport_iata = "DEL"
        conv.selected_airport_name = "Indira Gandhi International Airport"
        conv.flight_details_json = {"journey_type": "DEPARTURE", "travel_type": "DOMESTIC"}
        conv.current_state = "SERVICE_SELECTION"
        conv.requires_airport = True
        conv.requires_flight = True
        db.commit()

        # User selects service (e.g. "Silver Meet & Assist" or "1")
        res = WhatsAppBookingStateMachine.process_incoming_event(db, "916666655555", "1")
        db.refresh(conv)
        assert conv.current_state == "FLIGHT_INPUT"
        assert conv.selected_service_name is not None
    finally:
        db.close()


# 8. Travel Services Flow (Non-Airport) -> Direct to Date Selection
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_08_travel_services_flow(mock_text):
    mock_text.return_value = {"success": True, "message_id": "wamid.scen08"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, "915555544444")
        conv.selected_category = "Travel Services"
        conv.current_state = "SERVICE_SELECTION"
        db.commit()

        # Select travel service (e.g., Visa Assistance or "1")
        WhatsAppBookingStateMachine.process_incoming_event(db, "915555544444", "1")
        db.refresh(conv)
        assert conv.requires_flight is True or conv.requires_airport is False
        assert conv.current_state == "DATE_SELECTION"
    finally:
        db.close()


# 9. Booking Summary Rendering
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
def test_scenario_09_booking_summary(mock_buttons):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, "914444433333")
        conv.selected_service_name = "Meet & Greet"
        conv.selected_airport_name = "Indira Gandhi International Airport"
        conv.selected_airport_iata = "DEL"
        conv.flight_num = "AI2424"
        conv.booking_date = "15 August 2026"
        conv.passenger_count = 2
        conv.customer_name = "Aariz Farooqui"
        conv.customer_email = "aariz@example.com"
        conv.customer_phone = "914444433333"
        conv.total_amount = 5000.0
        conv.current_state = "ADDITIONAL_REQUIREMENTS"
        db.commit()

        WhatsAppBookingStateMachine.process_incoming_event(db, "914444433333", "None")
        db.refresh(conv)
        assert conv.current_state == "BOOKING_REVIEW"
        assert mock_buttons.called
    finally:
        db.close()


# 10. Booking Request Creation (No Payment Gateway - Pending Payment Status)
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_10_booking_request_creation_no_payment_gateway(mock_text):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_service_name = "Meet & Greet"
        conv.selected_airport_iata = "DEL"
        conv.selected_airport_name = "Indira Gandhi International Airport"
        conv.total_amount = 4500.0
        conv.customer_name = "Test Guest"
        conv.customer_email = "guest@example.com"
        conv.customer_phone = phone
        conv.booking_date = "20 August 2026"
        conv.current_state = "BOOKING_REVIEW"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
        db.refresh(conv)

        assert conv.current_state == "BOOKING_CONFIRMED"
        assert conv.payment_status == "PENDING"
        assert conv.booking_ref is not None
        assert conv.booking_ref.startswith("SHF-")

        # Verify DB booking record
        db_booking = db.execute(select(Booking).where(Booking.booking_ref == conv.booking_ref)).scalar_one_or_none()
        assert db_booking is not None
        assert db_booking.status == BookingStatus.PENDING
        assert db_booking.total_amount == 4500.0
    finally:
        db.close()


# 11. Duplicate Webhook Event Idempotency
def test_scenario_14_duplicate_webhook_idempotency():
    unique_event_id = f"wamid.dup_{uuid.uuid4().hex[:8]}"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": "911111100000",
                                    "id": unique_event_id,
                                    "type": "text",
                                    "text": {"body": "Hi"}
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    res1 = signed_whatsapp_post(payload)
    assert res1.status_code == 200
    assert res1.json()["data"]["messages_handled"] == 1

    # Second dispatch with identical wamid should be skipped by idempotency
    res2 = signed_whatsapp_post(payload)
    assert res2.status_code == 200
    assert res2.json()["data"]["messages_handled"] == 0


# 12. Customer WhatsApp Confirmation on Request Created
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_15_customer_whatsapp_notification(mock_send):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_service_name = "Meet & Greet"
        conv.total_amount = 3500.0
        conv.customer_name = "John Doe"
        conv.customer_email = "john@example.com"
        conv.booking_date = "25 August 2026"
        conv.current_state = "BOOKING_REVIEW"
        db.commit()

        mock_send.reset_mock()
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Confirm", input_id="btn_confirm_booking")
        assert mock_send.called
        # Check confirmation message mentions team will contact regarding payment
        sent_text = mock_send.call_args_list[0][0][1]
        assert "Our team will contact you regarding payment" in sent_text
    finally:
        db.close()


# 13. Team WhatsApp Notification on Request Created
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_16_team_whatsapp_notification(mock_send):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_service_name = "Gold VIP Package"
        conv.total_amount = 6500.0
        conv.customer_name = "Team Tester"
        conv.customer_email = "team@example.com"
        conv.booking_date = "26 August 2026"
        conv.current_state = "BOOKING_REVIEW"
        db.commit()

        mock_send.reset_mock()
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Confirm", input_id="btn_confirm_booking")
        # Ensure at least 2 messages sent (customer + team)
        assert mock_send.call_count >= 2
    finally:
        db.close()


# 14. Invalid Email Re-prompting
def test_scenario_19_invalid_input_reprompting():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, "919000033333")
        conv.current_state = "CUSTOMER_EMAIL"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, "919000033333", "not_an_email")
        db.refresh(conv)
        assert res["status"] == "invalid_email"
        assert conv.current_state == "CUSTOMER_EMAIL"
    finally:
        db.close()


# 15. WhatsApp Flight Local Validation - Valid Formats
@pytest.mark.parametrize("flight_input, expected_normalized", [
    ("EK501", "EK501"),
    ("AI2424", "AI2424"),
    ("6E224", "6E224"),
    ("UK955", "UK955"),
    ("ek 501", "EK501"),
    ("ai 2424", "AI2424"),
    ("6e 224", "6E224"),
    ("uk 955", "UK955"),
    ("BA1", "BA1"),
])
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
def test_scenario_21_whatsapp_flight_pure_local_validation_valid_formats(mock_buttons, mock_text, flight_input, expected_normalized):
    mock_buttons.return_value = {"success": True, "message_id": "wamid.test_01"}
    mock_text.return_value = {"success": True, "message_id": "wamid.test_02"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_airport_iata = "DEL"
        conv.selected_airport_name = "Indira Gandhi International Airport"
        conv.current_state = "FLIGHT_INPUT"
        conv.requires_flight = True
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, flight_input)
        db.refresh(conv)

        assert res["status"] == "flight_number_received"
        assert conv.flight_num == expected_normalized
        assert conv.current_state == "FLIGHT_CONFIRMATION"
        assert isinstance(conv.flight_details_json, dict)
        assert conv.flight_details_json["flight_number"] == expected_normalized
        assert conv.flight_details_json["verification_status"] == "not_verified"
        assert mock_buttons.called
    finally:
        db.close()


# 16. WhatsApp Flight Local Validation - Invalid Formats Rejected
@pytest.mark.parametrize("invalid_input", [
    ("invalid_code_123"),
    ("12345"),
    ("ABC"),
    (""),
    ("   "),
    ("!@#$%"),
    ("FLIGHT"),
    ("A"),
    ("1A234567"),
])
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_22_whatsapp_flight_local_validation_invalid_formats(mock_text, invalid_input):
    mock_text.reset_mock()
    mock_text.return_value = {"success": True, "message_id": "wamid.text_invalid"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "FLIGHT_INPUT"
        conv.requires_flight = True
        conv.flight_num = None
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, invalid_input)
        db.refresh(conv)

        assert res["status"] in ["invalid_flight_format", "empty_input"]
        assert conv.current_state == "FLIGHT_INPUT"
        assert conv.flight_num is None
    finally:
        db.close()


# 17. ZERO External HTTP API Calls when WhatsApp Receives Flight Number
@patch("httpx.Client.get")
@patch("httpx.AsyncClient.get")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
def test_scenario_23_whatsapp_flight_no_external_api_calls(mock_buttons, mock_text, mock_async_http, mock_sync_http):
    mock_buttons.return_value = {"success": True, "message_id": "wamid.test_03"}
    mock_text.return_value = {"success": True, "message_id": "wamid.test_04"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_airport_iata = "BOM"
        conv.selected_airport_name = "Chhatrapati Shivaji Maharaj International Airport"
        conv.current_state = "FLIGHT_INPUT"
        conv.requires_flight = True
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "EK501")
        db.refresh(conv)

        assert not mock_sync_http.called
        assert not mock_async_http.called
        assert res["status"] == "flight_number_received"
        assert conv.flight_num == "EK501"
        assert conv.current_state == "FLIGHT_CONFIRMATION"
    finally:
        db.close()


# 18. Response Time for WhatsApp Flight Number Input (< 10ms local validation)
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
def test_scenario_24_whatsapp_flight_response_time_under_500ms(mock_buttons, mock_text):
    mock_buttons.return_value = {"success": True, "message_id": "wamid.test_05"}
    mock_text.return_value = {"success": True, "message_id": "wamid.test_06"}
    import time
    from app.database import SessionLocal

    t0 = time.perf_counter()
    for _ in range(100):
        val = WhatsAppBookingStateMachine._validate_flight_number_local("AI2424")
    val_time_ms = ((time.perf_counter() - t0) / 100.0) * 1000.0
    assert val_time_ms < 5.0, f"Pure local validation took {val_time_ms:.4f}ms, expected < 5ms"
    assert val is not None
    assert val["flight_number"] == "AI2424"


# 19. Flight Confirmation to Date Selection State Transition
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_25_whatsapp_flight_confirmation_to_date_transition(mock_text):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_airport_iata = "DEL"
        conv.flight_num = "6E224"
        conv.current_state = "FLIGHT_CONFIRMATION"
        conv.requires_flight = True
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Confirm Flight", input_id="btn_confirm_flight")
        db.refresh(conv)

        assert res["status"] == "date_prompt_sent"
        assert conv.current_state == "DATE_SELECTION"
        assert conv.flight_num == "6E224"
    finally:
        db.close()


# 20. Session Expiry After 30 Minutes of Inactivity
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
def test_scenario_26_session_expiry_30_minutes(mock_list):
    mock_list.return_value = {"success": True, "message_id": "wamid.exp_01"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "CUSTOMER_EMAIL"
        conv.selected_service_name = "Meet & Greet"
        # Simulate 35 minutes ago
        conv.updated_at = datetime.now(timezone.utc) - timedelta(minutes=35)
        db.commit()

        # Inbound message after 35 minutes
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Hello")
        db.refresh(conv)

        # State should be reset to CATEGORY_SELECTION with expiry notice
        assert conv.current_state == "CATEGORY_SELECTION"
        assert mock_list.called
        args, kwargs = mock_list.call_args
        assert "Your previous session has expired" in kwargs.get("body_text", "")
    finally:
        db.close()


# 21. Non-Configured Airport Rejection
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_27_unsupported_airport_rejection(mock_text):
    mock_text.return_value = {"success": True, "message_id": "wamid.unsupp_01"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "AIRPORT_SELECTION"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Atlantis International")
        db.refresh(conv)

        assert res["status"] == "unsupported_airport"
        assert conv.current_state == "AIRPORT_SELECTION"
        assert conv.selected_airport_iata is None
        assert mock_text.called
        sent_msg = mock_text.call_args[0][1]
        assert "unavailable" in sent_msg
    finally:
        db.close()


# 22. Cancel Command Handling
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_28_cancel_command(mock_text):
    mock_text.return_value = {"success": True, "message_id": "wamid.cancel_01"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "DATE_SELECTION"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CANCEL")
        db.refresh(conv)

        assert res["status"] == "cancelled"
        assert conv.current_state == "CANCELLED"
        assert mock_text.called
    finally:
        db.close()


# 23. Restart Command Handling
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
def test_scenario_29_restart_command(mock_list):
    mock_list.return_value = {"success": True, "message_id": "wamid.restart_01"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "CUSTOMER_PHONE"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "RESTART")
        db.refresh(conv)

        assert conv.current_state == "CATEGORY_SELECTION"
        assert conv.customer_phone is None
        assert mock_list.called
    finally:
        db.close()


# 24. Hotel & Transportation Submenu Flow
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
def test_scenario_30_hotel_transportation_flow(mock_buttons):
    mock_buttons.return_value = {"success": True, "message_id": "wamid.sub_01"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "CATEGORY_SELECTION"
        db.commit()

        # Select option 4 (Hotel & Transportation)
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "4")
        db.refresh(conv)

        assert conv.selected_category == "Hotel & Transportation"
        assert conv.current_state == "HOTEL_TRANSPORT_SUBMENU"
        assert mock_buttons.called
    finally:
        db.close()


# 25. Explicit Global Restart Verification When in FLIGHT_INPUT State ("Hi" MUST ALWAYS WIN)
@pytest.mark.parametrize("restart_word", ["hi", "Hi", "HI", "hello", "Hello", "HEY", "hey", "START", "start", "menu", "Menu", "restart", "Restart", "main menu", "0"])
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
def test_scenario_31_global_restart_from_flight_input_state(mock_list, restart_word):
    """
    PRIORITY BUG TEST:
    When a customer is in FLIGHT_INPUT state and sends 'Hi' or any restart command,
    the bot MUST NOT respond with 'Please enter a valid flight number'.
    Instead, it MUST reset the session and present the Main Menu immediately.
    """
    mock_list.return_value = {"success": True, "message_id": "wamid.menu_01"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_category = "Airport Services"
        conv.selected_airport_iata = "DEL"
        conv.selected_airport_name = "Indira Gandhi International Airport"
        conv.selected_service_name = "Silver Meet & Assist"
        conv.requires_airport = True
        conv.requires_flight = True
        conv.current_state = "FLIGHT_INPUT"
        db.commit()

        # Send restart command
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, restart_word)
        db.refresh(conv)

        # Must be in CATEGORY_SELECTION with all temporary booking state cleared
        assert conv.current_state == "CATEGORY_SELECTION"
        assert conv.flight_num is None
        assert conv.selected_airport_iata is None
        assert conv.selected_service_name is None
        assert mock_list.called
        args, kwargs = mock_list.call_args
        assert "Welcome to Shafsky Aviation ✈️" in kwargs.get("body_text", "")
        assert "1️⃣ Airport Services" in kwargs.get("body_text", "")
    finally:
        db.close()


# 26. Global Restart From Every Other Active State
@pytest.mark.parametrize("active_state", [
    "JOURNEY_TYPE_SELECTION",
    "AIRPORT_SELECTION",
    "AIRPORT_CONFIRMATION",
    "SERVICE_SELECTION",
    "HOTEL_TRANSPORT_SUBMENU",
    "CHARTER_ORIGIN",
    "CHARTER_DESTINATION",
    "TRANSPORT_PICKUP",
    "TRANSPORT_DROPOFF",
    "HOTEL_CITY",
    "HOTEL_NIGHTS",
    "FLIGHT_CONFIRMATION",
    "DATE_SELECTION",
    "PASSENGER_COUNT",
    "CUSTOMER_NAME",
    "CUSTOMER_EMAIL",
    "CUSTOMER_PHONE",
    "ADDITIONAL_REQUIREMENTS",
    "BOOKING_REVIEW",
])
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
def test_scenario_32_global_restart_from_any_state(mock_list, active_state):
    mock_list.return_value = {"success": True, "message_id": "wamid.menu_02"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = active_state
        conv.selected_airport_iata = "DEL"
        conv.flight_num = "AI2424"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "hi")
        db.refresh(conv)

        assert conv.current_state == "CATEGORY_SELECTION"
        assert conv.flight_num is None
        assert conv.selected_airport_iata is None
        assert mock_list.called
    finally:
        db.close()


# ── SECTION 15 TESTS: DOMESTIC / INTERNATIONAL SEPARATION ──

# 27. Lucknow Arrival Domestic: Shows ONLY 2 domestic packages (₹2,420 & ₹4,400)
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
def test_scenario_33_lucknow_arrival_domestic_packages(mock_list):
    mock_list.return_value = {"success": True, "message_id": "wamid.lko_arr_dom"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_category = "Airport Services"
        conv.selected_airport_iata = "LKO"
        conv.selected_airport_name = "Chaudhary Charan Singh International Airport"
        conv.flight_details_json = {"journey_type": "ARRIVAL", "travel_type": "DOMESTIC", "flight_type": "DOMESTIC"}
        conv.current_state = "SERVICE_SELECTION"
        db.commit()

        res = WhatsAppBookingStateMachine._send_airport_services_menu(db, conv)
        assert res["status"] == "services_menu_sent"
        assert mock_list.called

        args, kwargs = mock_list.call_args
        sections = kwargs.get("sections", [])
        assert len(sections) > 0
        rows = sections[0].get("rows", [])
        
        # Must only return Domestic packages
        descriptions = [r["description"] for r in rows]
        assert any("2,420" in d for d in descriptions)  # Platinum
        assert any("4,400" in d for d in descriptions)  # Elite
        assert not any("3,300" in d for d in descriptions) # Must NOT contain International Departure Platinum
        assert not any("4,950" in d for d in descriptions) # Must NOT contain International Departure Elite
        assert not any("2,750" in d for d in descriptions) # Must NOT contain International Arrival Platinum
    finally:
        db.close()


# 28. Lucknow Arrival International: Shows ONLY 1 international package (₹2,750)
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
def test_scenario_34_lucknow_arrival_international_packages(mock_list):
    mock_list.return_value = {"success": True, "message_id": "wamid.lko_arr_intl"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_category = "Airport Services"
        conv.selected_airport_iata = "LKO"
        conv.selected_airport_name = "Chaudhary Charan Singh International Airport"
        conv.flight_details_json = {"journey_type": "ARRIVAL", "travel_type": "INTERNATIONAL", "flight_type": "INTERNATIONAL"}
        conv.current_state = "SERVICE_SELECTION"
        db.commit()

        res = WhatsAppBookingStateMachine._send_airport_services_menu(db, conv)
        assert res["status"] == "services_menu_sent"
        assert mock_list.called

        args, kwargs = mock_list.call_args
        sections = kwargs.get("sections", [])
        rows = sections[0].get("rows", [])

        # Must only return International Arrival package (₹2,750)
        descriptions = [r["description"] for r in rows]
        assert any("2,750" in d for d in descriptions)
        assert not any("2,420" in d for d in descriptions) # No domestic
        assert not any("4,400" in d for d in descriptions) # No domestic
    finally:
        db.close()


# 29. Lucknow Departure Domestic: Shows ONLY domestic departure packages (₹2,420 & ₹4,400)
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
def test_scenario_35_lucknow_departure_domestic_packages(mock_list):
    mock_list.return_value = {"success": True, "message_id": "wamid.lko_dep_dom"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_category = "Airport Services"
        conv.selected_airport_iata = "LKO"
        conv.selected_airport_name = "Chaudhary Charan Singh International Airport"
        conv.flight_details_json = {"journey_type": "DEPARTURE", "travel_type": "DOMESTIC", "flight_type": "DOMESTIC"}
        conv.current_state = "SERVICE_SELECTION"
        db.commit()

        res = WhatsAppBookingStateMachine._send_airport_services_menu(db, conv)
        assert res["status"] == "services_menu_sent"

        args, kwargs = mock_list.call_args
        sections = kwargs.get("sections", [])
        rows = sections[0].get("rows", [])

        descriptions = [r["description"] for r in rows]
        assert any("2,420" in d for d in descriptions)
        assert any("4,400" in d for d in descriptions)
        assert not any("3,300" in d for d in descriptions)
        assert not any("4,950" in d for d in descriptions)
    finally:
        db.close()


# 30. Lucknow Departure International: Shows ONLY international departure packages (₹3,300 & ₹4,950)
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
def test_scenario_36_lucknow_departure_international_packages(mock_list):
    mock_list.return_value = {"success": True, "message_id": "wamid.lko_dep_intl"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_category = "Airport Services"
        conv.selected_airport_iata = "LKO"
        conv.selected_airport_name = "Chaudhary Charan Singh International Airport"
        conv.flight_details_json = {"journey_type": "DEPARTURE", "travel_type": "INTERNATIONAL", "flight_type": "INTERNATIONAL"}
        conv.current_state = "SERVICE_SELECTION"
        db.commit()

        res = WhatsAppBookingStateMachine._send_airport_services_menu(db, conv)
        assert res["status"] == "services_menu_sent"

        args, kwargs = mock_list.call_args
        sections = kwargs.get("sections", [])
        rows = sections[0].get("rows", [])

        descriptions = [r["description"] for r in rows]
        assert any("3,300" in d for d in descriptions)
        assert any("4,950" in d for d in descriptions)
        assert not any("2,420" in d for d in descriptions)
        assert not any("4,400" in d for d in descriptions)
    finally:
        db.close()


# 31. Transit Combinations at Delhi (DEL)
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
def test_scenario_37_delhi_transit_combinations(mock_list):
    mock_list.return_value = {"success": True, "message_id": "wamid.del_transit"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_category = "Airport Services"
        conv.selected_airport_iata = "DEL"
        conv.selected_airport_name = "Indira Gandhi International Airport"
        conv.flight_details_json = {
            "journey_type": "TRANSIT",
            "travel_type": "DOMESTIC_DOMESTIC",
            "transit_type": "DOMESTIC_DOMESTIC",
            "flight_type": "DOMESTIC_DOMESTIC"
        }
        conv.current_state = "SERVICE_SELECTION"
        db.commit()

        res = WhatsAppBookingStateMachine._send_airport_services_menu(db, conv)
        assert res["status"] == "services_menu_sent"

        args, kwargs = mock_list.call_args
        sections = kwargs.get("sections", [])
        rows = sections[0].get("rows", [])
        descriptions = [r["description"] for r in rows]
        assert any("5,500" in d for d in descriptions)
    finally:
        db.close()


# 32. Empty Result Handling (Airport without configured services)
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
def test_scenario_38_empty_services_handling(mock_buttons):
    mock_buttons.return_value = {"success": True, "message_id": "wamid.empty_01"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_category = "Airport Services"
        # BBI currently has no Transit services configured
        conv.selected_airport_iata = "BBI"
        conv.selected_airport_name = "Biju Patnaik Airport"
        conv.flight_details_json = {"journey_type": "TRANSIT", "travel_type": "INTERNATIONAL_INTERNATIONAL"}
        conv.current_state = "SERVICE_SELECTION"
        db.commit()

        res = WhatsAppBookingStateMachine._send_airport_services_menu(db, conv)
        assert res["status"] == "no_services_found"
        assert mock_buttons.called
        args, kwargs = mock_buttons.call_args
        assert "no *International → International Transit* services available" in kwargs.get("body_text", "")
        button_ids = [b["id"] for b in kwargs.get("buttons", [])]
        assert "btn_change_travel_type" in button_ids
        assert "btn_change_airport" in button_ids
        assert "btn_main_menu" in button_ids
    finally:
        db.close()


# 33. Non-Configured / Unsupported Airport Rejection
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_39_unsupported_airport_rejection(mock_text):
    mock_text.return_value = {"success": True, "message_id": "wamid.unsupp_01"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.current_state = "AIRPORT_SELECTION"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "London Heathrow")
        assert res["status"] == "unsupported_airport"
        assert mock_text.called
        args, kwargs = mock_text.call_args
        assert "unavailable" in args[1]
    finally:
        db.close()


# 34. Dynamic Travel Type Switch During Package Selection
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
def test_scenario_40_dynamic_travel_type_switch_during_service_selection(mock_list):
    mock_list.return_value = {"success": True, "message_id": "wamid.switch_01"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        conv.selected_category = "Airport Services"
        conv.selected_airport_iata = "LKO"
        conv.selected_airport_name = "Chaudhary Charan Singh International Airport"
        conv.flight_details_json = {"journey_type": "DEPARTURE", "travel_type": "DOMESTIC", "flight_type": "DOMESTIC"}
        conv.current_state = "SERVICE_SELECTION"
        db.commit()

        # User types "International" while at service selection
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "International")
        db.refresh(conv)

        # Travel type updated to INTERNATIONAL
        assert conv.flight_details_json["travel_type"] == "INTERNATIONAL"
        assert mock_list.called
    finally:
        db.close()


# 35. Complete Multi-Step Flow: Journey -> Travel Type -> Airport -> Package -> Flight -> Customer Info
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_41_complete_separated_airport_service_booking(mock_text, mock_list, mock_buttons):
    mock_text.return_value = {"success": True, "message_id": "wamid.txt"}
    mock_list.return_value = {"success": True, "message_id": "wamid.lst"}
    mock_buttons.return_value = {"success": True, "message_id": "wamid.btn"}
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"

        # 1. Main menu
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Hi")
        conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
        assert conv.current_state == "CATEGORY_SELECTION"

        # 2. Select Airport Services
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")
        db.refresh(conv)
        assert conv.current_state == "JOURNEY_TYPE_SELECTION"

        # 3. Select Departure
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "2")
        db.refresh(conv)
        assert conv.current_state == "AIRPORT_TRAVEL_TYPE"
        assert conv.flight_details_json["journey_type"] == "DEPARTURE"

        # 4. Select Domestic
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")
        db.refresh(conv)
        assert conv.current_state == "AIRPORT_SELECTION"
        assert conv.flight_details_json["travel_type"] == "DOMESTIC"

        # 5. Enter Lucknow
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Lucknow")
        db.refresh(conv)
        assert conv.current_state == "SERVICE_SELECTION"
        assert conv.selected_airport_iata == "LKO"

        # 6. Select Package (1 -> Platinum Service ₹2,420)
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "1")
        db.refresh(conv)
        assert conv.current_state == "FLIGHT_INPUT"
        assert conv.total_amount == 2420.0

        # 7. Enter Flight EK501 (local validation)
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "EK501")
        db.refresh(conv)
        assert conv.current_state == "FLIGHT_CONFIRMATION"
        assert conv.flight_num == "EK501"

        # 8. Confirm Flight
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Confirm")
        db.refresh(conv)
        assert conv.current_state == "DATE_SELECTION"

        # 9. Date
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "25/12/2026")
        db.refresh(conv)
        assert conv.current_state == "PASSENGER_COUNT"

        # 10. Passengers
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "2")
        db.refresh(conv)
        assert conv.current_state == "CUSTOMER_NAME"

        # 11. Name
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Aariz Khan")
        db.refresh(conv)
        assert conv.current_state == "CUSTOMER_EMAIL"

        # 12. Email
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "aariz@example.com")
        db.refresh(conv)
        assert conv.current_state == "CUSTOMER_PHONE"

        # 13. Phone
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Same")
        db.refresh(conv)
        assert conv.current_state == "ADDITIONAL_REQUIREMENTS"

        # 14. Requirements
        WhatsAppBookingStateMachine.process_incoming_event(db, phone, "None")
        db.refresh(conv)
        assert conv.current_state == "BOOKING_REVIEW"

        # 15. Confirm Booking Request
        res = WhatsAppBookingStateMachine.process_incoming_event(db, phone, "Confirm")
        db.refresh(conv)
        assert conv.current_state == "BOOKING_CONFIRMED"
        assert conv.booking_ref is not None
        assert conv.payment_status == "PENDING"
    finally:
        db.close()

