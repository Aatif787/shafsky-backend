"""
Test suite for universal 5-recipient WhatsApp notification system.
Validates:
- Conversion of Indian mobile numbers to international format (91XXXXXXXXXX)
- Every booking and enquiry reaches all five configured recipient numbers
- All service categories covered: Meet & Greet, Lounge, Ticketing, Transport,
  Hotels, Air Charter, Cargo & Logistics, and Special Services
- Individual recipient failure does NOT block other recipients
- Duplicate events do NOT cause duplicate notifications (idempotency check)
- Graceful failure isolation and customer notification suppression
"""

from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

from app.services.whatsapp_department_service import (
    Department,
    WhatsAppDepartmentService,
)
from app.services.notification_service import NotificationService


@pytest.fixture(autouse=True)
def reset_idempotency_state():
    """Clear in-memory idempotency state between tests."""
    WhatsAppDepartmentService.clear_idempotency_cache()
    yield
    WhatsAppDepartmentService.clear_idempotency_cache()


# ==========================================
# 1. Phone Normalization Tests
# ==========================================

def test_indian_phone_number_normalization():
    # 10-digit raw numbers
    assert WhatsAppDepartmentService.normalize_phone_number("9599087957") == "919599087957"
    assert WhatsAppDepartmentService.normalize_phone_number("9599225778") == "919599225778"
    assert WhatsAppDepartmentService.normalize_phone_number("9217522660") == "919217522660"
    assert WhatsAppDepartmentService.normalize_phone_number("8864931247") == "918864931247"
    assert WhatsAppDepartmentService.normalize_phone_number("9599087959") == "919599087959"

    # With formatting, dashes, spaces, +91
    assert WhatsAppDepartmentService.normalize_phone_number("+91 95990-87957") == "919599087957"
    assert WhatsAppDepartmentService.normalize_phone_number("09599087957") == "919599087957"
    assert WhatsAppDepartmentService.normalize_phone_number("919599087957") == "919599087957"
    assert WhatsAppDepartmentService.normalize_phone_number("") is None


def test_get_recipient_phone_numbers_loads_all_five(monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9599087957",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="9599225778",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="9217522660",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="8864931247",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="9599087959",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
        ),
    )

    recipients = WhatsAppDepartmentService.get_recipient_phone_numbers()
    assert len(recipients) == 5
    assert recipients == [
        "919599087957",
        "919599225778",
        "919217522660",
        "918864931247",
        "919599087959",
    ]


# ==========================================
# 2. Every Category Dispatches to ALL Five Recipients
# ==========================================

@pytest.mark.parametrize(
    "category,service_type",
    [
        ("Meet & Greet", "Arrival Assist"),
        ("Lounge", "Domestic Lounge"),
        ("Ticketing", "Flight Ticket"),
        ("Ground Transport", "Luxury Sedan"),
        ("Hotels", "5-Star Suite"),
        ("Air Charter", "Midsize Jet"),
        ("Cargo & Logistics", "Express Air Freight"),
        ("Special Services", "Medical Assistance"),
    ],
)
def test_all_five_recipients_attempted_for_every_category(category, service_type, monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9599087957",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="9599225778",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="9217522660",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="8864931247",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="9599087959",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="",
        ),
    )

    sent_phones = []

    def mock_send_text(phone, msg):
        sent_phones.append(phone)
        return {"success": True, "message_id": f"wamid.{phone}"}

    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client.is_configured", lambda: True)
    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client.send_text_message", mock_send_text)

    ref = f"SHF-{category[:3].upper()}-001"
    context = {
        "booking_ref": ref,
        "passenger_name": "Test Customer",
        "passenger_phone": "+919876543210",
        "passenger_email": "test@example.com",
        "service_category": category,
        "service_type": service_type,
    }

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)

    assert result["status"] == "DELIVERED"
    assert result["recipients_attempted"] == 5
    assert result["recipients_delivered"] == 5
    assert len(sent_phones) == 5
    assert set(sent_phones) == {
        "919599087957",
        "919599225778",
        "919217522660",
        "918864931247",
        "919599087959",
    }


# ==========================================
# 3. Individual Recipient Failure Does NOT Block Others
# ==========================================

def test_individual_recipient_failure_does_not_block_remaining(monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9599087957",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="9599225778",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="9217522660",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="8864931247",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="9599087959",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="",
        ),
    )

    attempted_phones = []

    def mock_send_text(phone, msg):
        attempted_phones.append(phone)
        # Recipient #2 (919599225778) fails with Meta API error
        if phone == "919599225778":
            return {"success": False, "error": "Meta 500 Temporary Server Error"}
        # Recipient #4 (918864931247) raises network exception
        if phone == "918864931247":
            raise RuntimeError("Connection dropped")
        return {"success": True, "message_id": f"wamid.{phone}"}

    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client.is_configured", lambda: True)
    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client.send_text_message", mock_send_text)

    context = {
        "booking_ref": "SHF-PARTIAL-FAIL-1",
        "service_category": "Transport",
        "service_type": "SUV Transfer",
    }

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)

    # All 5 were attempted despite failure on #2 and #4
    assert len(attempted_phones) == 5
    assert result["recipients_attempted"] == 5
    assert result["recipients_delivered"] == 3
    assert result["status"] == "PARTIALLY_DELIVERED"

    # Status details
    failures = [r for r in result["recipient_results"] if r["status"] == "FAILED"]
    successes = [r for r in result["recipient_results"] if r["status"] == "DELIVERED"]
    assert len(failures) == 2
    assert len(successes) == 3


# ==========================================
# 4. Duplicate Event Deduplication (Idempotency)
# ==========================================

def test_duplicate_booking_event_prevented(monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9599087957",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="9599225778",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="9217522660",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="8864931247",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="9599087959",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="",
        ),
    )

    send_call_count = 0

    def mock_send_text(phone, msg):
        nonlocal send_call_count
        send_call_count += 1
        return {"success": True, "message_id": f"wamid.{phone}"}

    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client.is_configured", lambda: True)
    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client.send_text_message", mock_send_text)

    context = {
        "booking_ref": "SHF-DUP-CHECK-101",
        "service_category": "Meet & Greet",
        "service_type": "Gold Package",
    }

    # First dispatch -> DELIVERED
    res1 = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)
    assert res1["status"] == "DELIVERED"
    assert send_call_count == 5  # 5 numbers called once

    # Second dispatch with identical booking_ref -> SKIPPED (Duplicate)
    res2 = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)
    assert res2["status"] == "SKIPPED"
    assert res2["reason"] == "already_dispatched"
    assert send_call_count == 5  # Zero extra calls made


# ==========================================
# 5. Air Charter Notification (All 5 Recipients & Deduplication)
# ==========================================

def test_charter_notification_dispatches_to_all_five_and_deduplicates(monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9599087957",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="9599225778",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="9217522660",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="8864931247",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="9599087959",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="",
        ),
    )

    sent_charter_phones = []

    def mock_send_text(phone, msg):
        sent_charter_phones.append(phone)
        return {"success": True, "message_id": f"wamid.charter.{phone}"}

    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client.is_configured", lambda: True)
    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client.send_text_message", mock_send_text)

    charter_obj = SimpleNamespace(
        request_reference="SHF-CH-ALL5-999",
        customer_name="Royal Highness",
        country_code="91",
        phone="9876543210",
        email="vip@royal.aero",
        trip_type="ONE_WAY",
        origin="DEL",
        destination="LHR",
        departure_date="2026-12-25",
        departure_time="10:00",
        aircraft_preference="Heavy Jet",
        passengers={"total": 4},
        special_requests="Catering and lounge",
    )

    # 1. First send -> all 5 delivered
    res1 = WhatsAppDepartmentService.dispatch_charter_notification(charter_obj)
    assert res1["status"] == "DELIVERED"
    assert res1["recipients_delivered"] == 5
    assert len(sent_charter_phones) == 5

    # 2. Duplicate send -> SKIPPED
    res2 = WhatsAppDepartmentService.dispatch_charter_notification(charter_obj)
    assert res2["status"] == "SKIPPED"
    assert res2["reason"] == "already_dispatched"
    assert len(sent_charter_phones) == 5  # No extra calls


# ==========================================
# 6. NotificationService Integration & Customer Suppression
# ==========================================

def test_notify_booking_created_integration_with_universal_dispatch(monkeypatch):
    mock_dispatch = MagicMock(
        return_value={
            "status": "DELIVERED",
            "recipients_attempted": 5,
            "recipients_delivered": 5,
        }
    )
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification",
        mock_dispatch,
    )

    db = MagicMock()
    context = {
        "booking_ref": "SHF-INTG-888",
        "passenger_name": "Valued Guest",
        "passenger_email": "guest@shafsky.aero",
        "service_category": "Lounge",
        "service_type": "Arrival Lounge",
    }

    summary = NotificationService.notify_booking_created(db, context)

    # Customer suppression remains intact
    assert summary["customer"]["status"] == "SKIPPED"
    assert summary["customer"]["reason"] == "pending_payment_suppressed"

    # Admin dispatch completed
    assert summary["admin"][0]["status"] == "DELIVERED"
    mock_dispatch.assert_called_once_with(context)


# ==========================================
# 7. WhatsApp Template Support Tests
# ==========================================

def test_charter_notification_uses_approved_template_when_configured(monkeypatch):
    """
    Verifies that Air Charter notifications use the configured approved Meta
    WhatsApp template when WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE is set,
    dispatched across all 5 recipient numbers.
    """
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9599087957",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="9599225778",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="9217522660",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="8864931247",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="9599087959",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="shafsky_charter_alert_template",
        ),
    )

    template_calls = []

    def mock_send_message(to_phone, template_name, template_components=None, **kwargs):
        template_calls.append({
            "to_phone": to_phone,
            "template_name": template_name,
            "template_components": template_components,
        })
        return {"success": True, "message_id": f"wamid.tpl.{to_phone}"}

    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client.is_configured", lambda: True)
    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client.send_message", mock_send_message)

    charter_obj = SimpleNamespace(
        request_reference="SHF-CH-TPL-777",
        customer_name="Sheikh Mansoor",
        country_code="91",
        phone="9876543210",
        email="mansoor@emirates.aero",
        trip_type="ROUND_TRIP",
        origin="DXB",
        destination="BOM",
        departure_date="2026-11-15",
        departure_time="14:00",
        aircraft_preference="Global 6000",
        passengers={"total": 6},
        special_requests="Halal catering and private customs clearance",
    )

    result = WhatsAppDepartmentService.dispatch_charter_notification(charter_obj)

    assert result["status"] == "DELIVERED"
    assert result["recipients_attempted"] == 5
    assert result["recipients_delivered"] == 5

    # Verify template was used for all 5 numbers
    assert len(template_calls) == 5
    dispatched_recipients = {call["to_phone"] for call in template_calls}
    assert dispatched_recipients == {
        "919599087957",
        "919599225778",
        "919217522660",
        "918864931247",
        "919599087959",
    }

    for call in template_calls:
        assert call["template_name"] == "shafsky_charter_alert_template"
        assert len(call["template_components"]) == 1
        body_comp = call["template_components"][0]
        assert body_comp["type"] == "body"
        text_param = body_comp["parameters"][0]["text"]
        assert "NEW AIR CHARTER REQUEST" in text_param
        assert "SHF-CH-TPL-777" in text_param
        assert "Sheikh Mansoor" in text_param
        assert "DXB ➔ BOM" in text_param


def test_booking_notification_uses_approved_template_when_configured(monkeypatch):
    """
    Verifies that booking and enquiry notifications use the configured approved Meta
    WhatsApp template when WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE is set.
    """
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9599087957",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="9599225778",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="9217522660",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="8864931247",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="9599087959",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="shafsky_booking_alert_template",
        ),
    )

    template_calls = []

    def mock_send_message(to_phone, template_name, template_components=None, **kwargs):
        template_calls.append({
            "to_phone": to_phone,
            "template_name": template_name,
            "template_components": template_components,
        })
        return {"success": True, "message_id": f"wamid.tpl.{to_phone}"}

    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client.is_configured", lambda: True)
    monkeypatch.setattr("app.services.whatsapp_department_service.whatsapp_client.send_message", mock_send_message)

    context = {
        "booking_ref": "SHF-BKG-TPL-444",
        "passenger_name": "Aariz Khan",
        "passenger_phone": "+919599087957",
        "passenger_email": "aariz@shafsky.com",
        "service_category": "Meet & Greet",
        "service_type": "VIP Fast Track",
        "total_amount": 5500.0,
        "currency": "INR",
        "status": "CONFIRMED",
    }

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)

    assert result["status"] == "DELIVERED"
    assert result["recipients_attempted"] == 5
    assert result["recipients_delivered"] == 5
    assert len(template_calls) == 5

    for call in template_calls:
        assert call["template_name"] == "shafsky_booking_alert_template"
        body_comp = call["template_components"][0]
        assert body_comp["type"] == "body"
        text_param = body_comp["parameters"][0]["text"]
        assert "SHF-BKG-TPL-444" in text_param
        assert "VIP Fast Track" in text_param

