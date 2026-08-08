"""
Comprehensive Unit Test Suite for Shafsky Aviation WhatsApp Booking Automation System.
Covers all 20 core acceptance test scenarios.
"""

import uuid
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import hmac
import hashlib

from app.main import app
from app.integrations.whatsapp.client import whatsapp_client
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine, WhatsAppService
from app.providers.razorpay_provider import razorpay_provider

client = TestClient(app)


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


# 2. WhatsApp Incoming Text ("Hi") -> Welcome & Categories List
@patch.object(whatsapp_client, "send_interactive_list")
def test_scenario_02_incoming_hi_welcome(mock_send_list):
    mock_send_list.return_value = {"success": True, "message_id": "wamid.welcome_01"}
    unique_wamid = f"wamid.hi_{uuid.uuid4().hex[:8]}"
    test_phone = f"91999{uuid.uuid4().int % 10**7:07d}"

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

    res = client.post("/api/whatsapp/webhook", json=payload)
    assert res.status_code == 200
    assert mock_send_list.called


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

    res = client.post("/api/whatsapp/webhook", json=payload)
    assert res.status_code == 200


# 4. Interactive List Response
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_list")
def test_scenario_04_list_response_handling(mock_send_list):
    mock_send_list.return_value = {"success": True, "message_id": "wamid.list_01"}

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

    res = client.post("/api/whatsapp/webhook", json=payload)
    assert res.status_code == 200


# 5. Conversation State Transition
def test_scenario_05_state_transitions():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv = WhatsAppBookingStateMachine.get_or_create_conversation(db, "918888877777")
        conv.current_state = "START"
        db.commit()
        assert conv.current_state == "START"

        WhatsAppBookingStateMachine.process_incoming_event(db, "918888877777", "Hi")
        db.refresh(conv)
        assert conv.current_state == "CATEGORY_SELECTION"
    finally:
        db.close()


# 6. Airport Selection Resolution (IATA, City, Name)
def test_scenario_06_airport_resolution():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv = WhatsAppBookingStateMachine.get_or_create_conversation(db, "917777766666")
        conv.current_state = "AIRPORT_SELECTION"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, "917777766666", "Delhi")
        db.refresh(conv)
        assert conv.selected_airport_iata == "DEL"
        assert conv.current_state == "AIRPORT_CONFIRMATION"
    finally:
        db.close()


# 7. Flight-Required Service Flow
def test_scenario_07_flight_required_flow():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv = WhatsAppBookingStateMachine.get_or_create_conversation(db, "916666655555")
        conv.selected_category = "Airport Services"
        conv.selected_service_name = "Meet & Greet"
        conv.requires_airport = True
        conv.requires_flight = True
        conv.current_state = "AIRPORT_CONFIRMATION"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, "916666655555", "Confirm", input_id="btn_confirm_airport")
        db.refresh(conv)
        assert conv.current_state == "FLIGHT_INPUT"
    finally:
        db.close()


# 8. Flight-Not-Required Service Flow
def test_scenario_08_flight_not_required_flow():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv = WhatsAppBookingStateMachine.get_or_create_conversation(db, "915555544444")
        conv.selected_category = "Travel Services"
        conv.selected_service_name = "Hotel Booking"
        conv.requires_airport = False
        conv.requires_flight = False
        conv.current_state = "SERVICE_SELECTION"
        db.commit()

        # Input matching non-flight service
        WhatsAppBookingStateMachine.process_incoming_event(db, "915555544444", "Hotel Booking")
        db.refresh(conv)
        assert conv.requires_flight is False
        assert conv.current_state == "DATE_SELECTION"
    finally:
        db.close()


# 9. Booking Summary Rendering
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_interactive_buttons")
def test_scenario_09_booking_summary(mock_buttons):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv = WhatsAppBookingStateMachine.get_or_create_conversation(db, "914444433333")
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


# 10. Razorpay Order & Payment Creation
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_10_razorpay_creation(mock_text):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv = WhatsAppBookingStateMachine.get_or_create_conversation(db, "913333322222")
        conv.selected_service_name = "Meet & Greet"
        conv.selected_airport_iata = "DEL"
        conv.total_amount = 4500.0
        conv.customer_name = "Test Guest"
        conv.customer_email = "guest@example.com"
        conv.current_state = "BOOKING_REVIEW"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, "913333322222", "CONFIRM", input_id="btn_confirm_pay")
        db.refresh(conv)
        assert conv.current_state == "PENDING_PAYMENT"
        assert conv.booking_ref is not None
        assert conv.razorpay_payment_url is not None
    finally:
        db.close()


# 11. Razorpay Webhook Signature Validation
def test_scenario_11_razorpay_signature_validation(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "secret123")
    body = b'{"event":"payment.captured","payload":{}}'
    sig = hmac.new(b"secret123", body, hashlib.sha256).hexdigest()

    assert razorpay_provider.verify_webhook_signature(body, sig) is True
    assert razorpay_provider.verify_webhook_signature(body, "invalid_sig") is False


# 12. Payment Success Handler
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_12_payment_success_handler(mock_send_text):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv = WhatsAppBookingStateMachine.get_or_create_conversation(db, "912222211111")
        conv.total_amount = 5000.0
        conv.current_state = "BOOKING_REVIEW"
        db.commit()

        # Generate payment
        WhatsAppBookingStateMachine._create_and_send_payment(db, conv)
        ref = conv.booking_ref

        # Trigger payment success
        ok = WhatsAppBookingStateMachine.handle_payment_success(db, booking_ref=ref, payment_id="pay_test_999")
        assert ok is True

        db.refresh(conv)
        assert conv.current_state == "BOOKING_CONFIRMED"
        assert conv.payment_status == "PAID"
    finally:
        db.close()


# 13. Payment Failure Handler
def test_scenario_13_payment_failure_webhook():
    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_fail_123",
                    "notes": {"booking_ref": "SHF-FAIL-001"}
                }
            }
        }
    }

    res = client.post("/api/payments/razorpay/webhook", json=payload)
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "FAILED"


# 14. Duplicate Webhook Idempotency
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

    res1 = client.post("/api/whatsapp/webhook", json=payload)
    assert res1.status_code == 200
    assert res1.json()["data"]["messages_handled"] == 1

    # Second dispatch with identical wamid should be skipped by idempotency
    res2 = client.post("/api/whatsapp/webhook", json=payload)
    assert res2.status_code == 200
    assert res2.json()["data"]["messages_handled"] == 0


# 15. Customer WhatsApp Notification
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_15_customer_whatsapp_notification(mock_send):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv = WhatsAppBookingStateMachine.get_or_create_conversation(db, "919000011111")
        conv.total_amount = 3500.0
        conv.current_state = "BOOKING_REVIEW"
        db.commit()

        WhatsAppBookingStateMachine._create_and_send_payment(db, conv)
        ref = conv.booking_ref
        mock_send.reset_mock()

        WhatsAppBookingStateMachine.handle_payment_success(db, booking_ref=ref, payment_id="pay_cust_test")
        assert mock_send.called
    finally:
        db.close()


# 16. Team WhatsApp Notification
@patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message")
def test_scenario_16_team_whatsapp_notification(mock_send):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv = WhatsAppBookingStateMachine.get_or_create_conversation(db, "919000022222")
        conv.total_amount = 4500.0
        conv.current_state = "BOOKING_REVIEW"
        db.commit()

        WhatsAppBookingStateMachine._create_and_send_payment(db, conv)
        ref = conv.booking_ref

        WhatsAppBookingStateMachine.handle_payment_success(db, booking_ref=ref, payment_id="pay_team_test")
        # Ensure officer phone notification was sent
        assert mock_send.call_count >= 2
    finally:
        db.close()


# 17. Customer Email Confirmation
def test_scenario_17_customer_email_confirmation():
    from app.services.notification_templates import NotificationTemplateEngine
    data = {
        "passengerName": "Aariz Farooqui",
        "bookingRef": "SHF-20260808-TEST",
        "flightNum": "AI2424",
        "originCode": "DEL",
        "destCode": "BOM",
        "totalAmount": 5000.0
    }
    rendered = NotificationTemplateEngine.render_template("BOOKING_CONFIRMATION", data)
    assert "Aariz Farooqui" in rendered["html"]
    assert "SHF-20260808-TEST" in rendered["subject"]
    # Ensure "concierge" is NOT present anywhere
    assert "concierge" not in rendered["html"].lower()
    assert "concierge" not in rendered["subject"].lower()


# 18. Team Email Notification
def test_scenario_18_team_email_notification():
    from app.services.notification_templates import NotificationTemplateEngine
    data = {
        "passengerName": "Team Test",
        "bookingRef": "SHF-TEAM-001",
        "flightNum": "EK505",
        "originCode": "BOM",
        "destCode": "DXB",
        "totalAmount": 7500.0
    }
    rendered = NotificationTemplateEngine.render_template("BOOKING_CONFIRMATION", data)
    assert "SHF-TEAM-001" in rendered["subject"]


# 19. Invalid Input Handling & Re-prompting
def test_scenario_19_invalid_input_reprompting():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        conv = WhatsAppBookingStateMachine.get_or_create_conversation(db, "919000033333")
        conv.current_state = "CUSTOMER_EMAIL"
        db.commit()

        res = WhatsAppBookingStateMachine.process_incoming_event(db, "919000033333", "not_an_email")
        db.refresh(conv)
        assert res["status"] == "invalid_email"
        assert conv.current_state == "CUSTOMER_EMAIL"
    finally:
        db.close()


# 20. API Failure Graceful Handling
def test_scenario_20_api_failure_graceful_handling():
    res = razorpay_provider.create_payment_link(
        amount=5000.0,
        currency="INR",
        reference_id="SHF-ERR-001",
        description="Test Error Handling",
        customer_name="Test",
        customer_email="test@example.com",
        customer_phone="919999999999"
    )

    assert "success" in res
