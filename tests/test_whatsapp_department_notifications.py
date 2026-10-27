"""
Comprehensive test suite for the Shafsky Aviation WhatsApp 5-Recipient Notification System.

Verifies:
1. Phone normalization (Indian 10-digit mobile numbers to E.164 91XXXXXXXXXX without duplication).
2. Universal 5-recipient dispatch across all 8 service categories:
   - Meet & Greet, Lounge, Ticketing, Transport, Hotels, Air Charter, Cargo & Logistics, Special Services.
3. Accurate Meta API status distinction:
   - ACCEPTED (only when Meta returns success=True AND message_id).
   - UNKNOWN (timeout_unverified states without blind resending).
   - FAILED (API errors, missing IDs, or network exceptions).
4. Error isolation: One recipient failing never prevents attempts to remaining recipients.
5. Two-phase idempotency lifecycle:
   - Single event duplicate prevention across 24 hours.
   - Transient failure releases lock so retries are NOT permanently suppressed.
   - Unknown timeout holds lock for safety.
   - Two different bookings within 24h are never confused.
6. Template parameters stay strictly <= 1000 characters (respecting Meta's 1024-character maximum).
7. Direct Air Ticketing engine (TicketingService.create_booking) triggers notification dispatch.
8. Delivery status webhooks update NotificationRecord upon Meta handset delivery.
"""

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from app.services.whatsapp_department_service import (
    Department,
    WhatsAppDepartmentService,
)
from app.services.notification_service import NotificationService
from app.services.ticketing_service import TicketingService
from app.schemas.ticketing import AirTicketBookingCreateRequest
from app.models.schema import NotificationRecord, NotificationStatus
from app.integrations.whatsapp.service import WhatsAppService


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
    # 10-digit raw Indian numbers
    assert WhatsAppDepartmentService.normalize_phone_number("9599087957") == "919599087957"
    assert WhatsAppDepartmentService.normalize_phone_number("9599225778") == "919599225778"
    assert WhatsAppDepartmentService.normalize_phone_number("9217522660") == "919217522660"
    assert WhatsAppDepartmentService.normalize_phone_number("8864931247") == "918864931247"
    assert WhatsAppDepartmentService.normalize_phone_number("9599087959") == "919599087959"

    # With formatting, dashes, spaces, +91, leading 0
    assert WhatsAppDepartmentService.normalize_phone_number("+91 95990-87957") == "919599087957"
    assert WhatsAppDepartmentService.normalize_phone_number("09599087957") == "919599087957"
    assert WhatsAppDepartmentService.normalize_phone_number("919599087957") == "919599087957"
    assert WhatsAppDepartmentService.normalize_phone_number("") is None
    assert WhatsAppDepartmentService.normalize_phone_number("123") is None


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
            WHATSAPP_DEPARTMENT_TEMPLATE_LANG="en_US",
        ),
    )

    sent_phones = []

    def mock_send_text(phone, msg):
        sent_phones.append(phone)
        return {"success": True, "message_id": f"wamid.{phone}"}

    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True)
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.send_text_message", lambda self, phone, msg: mock_send_text(phone, msg))

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

    assert result["status"] == "ACCEPTED"
    assert result["recipients_attempted"] == 5
    assert result["recipients_accepted"] == 5
    assert len(sent_phones) == 5
    assert set(sent_phones) == {
        "919599087957",
        "919599225778",
        "919217522660",
        "918864931247",
        "919599087959",
    }


# ==========================================
# 3. Accurate Distinction of ACCEPTED, FAILED, and UNKNOWN States
# ==========================================

def test_distinguish_accepted_failed_and_unknown_timeout(monkeypatch):
    """
    Verifies that:
    - Only responses with success=True AND message_id are ACCEPTED.
    - Responses with success=True but missing message_id are FAILED.
    - Timeouts (timeout_unverified) are distinguished as UNKNOWN.
    - API errors (e.g. 500) are FAILED.
    - The remaining recipients are still attempted.
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
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="",
            WHATSAPP_DEPARTMENT_TEMPLATE_LANG="en_US",
        ),
    )

    attempted_phones = []

    def mock_send_text(phone, msg):
        attempted_phones.append(phone)
        if phone == "919599087957":
            # Recipient 1: Valid Meta acceptance
            return {"success": True, "message_id": "wamid.VALID_1"}
        elif phone == "919599225778":
            # Recipient 2: Valid Meta acceptance
            return {"success": True, "message_id": "wamid.VALID_2"}
        elif phone == "919217522660":
            # Recipient 3: success=True BUT missing message_id (should be marked FAILED)
            return {"success": True, "message_id": None}
        elif phone == "918864931247":
            # Recipient 4: Ambiguous network timeout (should be UNKNOWN)
            return {"success": False, "status": "timeout_unverified", "error": "Timeout connecting to Meta"}
        else:
            # Recipient 5: Explicit Meta API 500 error
            return {"success": False, "error": "Meta API Error (500): Internal server error"}

    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True)
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.send_text_message", lambda self, phone, msg: mock_send_text(phone, msg))

    context = {
        "booking_ref": "SHF-STATUS-AUDIT-101",
        "service_category": "Transport",
        "service_type": "SUV Transfer",
    }

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)

    # All 5 recipients were attempted
    assert len(attempted_phones) == 5
    assert result["recipients_attempted"] == 5
    assert result["recipients_accepted"] == 2
    assert result["status"] == "PARTIALLY_ACCEPTED"

    statuses = {r["phone"]: r["status"] for r in result["recipient_results"]}
    # Phone 1 & 2 -> ACCEPTED
    assert any("9599087957" in p for p in statuses if statuses[p] == "ACCEPTED")
    assert any("9599225778" in p for p in statuses if statuses[p] == "ACCEPTED")
    # Phone 3 -> FAILED (missing message_id)
    assert any("9217522660" in p for p in statuses if statuses[p] == "FAILED")
    # Phone 4 -> UNKNOWN (timeout)
    assert any("8864931247" in p for p in statuses if statuses[p] == "UNKNOWN")
    # Phone 5 -> FAILED (API error)
    assert any("9599087959" in p for p in statuses if statuses[p] == "FAILED")


# ==========================================
# 4. Idempotency Lifecycle & Safe Retries
# ==========================================

def test_temporary_failure_releases_lock_allowing_immediate_retry(monkeypatch):
    """
    Proves that when a dispatch fails due to temporary network/API error,
    the idempotency lock is released immediately, allowing retry.
    """
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9599087957",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="",
            WHATSAPP_DEPARTMENT_TEMPLATE_LANG="en_US",
        ),
    )

    should_succeed = False
    call_count = 0

    def mock_send_text(phone, msg):
        nonlocal call_count
        call_count += 1
        if not should_succeed:
            return {"success": False, "error": "Meta 503 Service Unavailable"}
        return {"success": True, "message_id": "wamid.RETRY_SUCCESS"}

    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True)
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.send_text_message", lambda self, phone, msg: mock_send_text(phone, msg))

    context = {"booking_ref": "SHF-RETRY-TEST-1", "service_category": "Lounge"}

    # Attempt 1: Fails with 503
    res1 = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)
    assert res1["status"] == "FAILED"
    assert call_count == 1

    # Attempt 2: Immediate retry should NOT be suppressed as duplicate because attempt 1 failed
    should_succeed = True
    res2 = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)
    assert res2["status"] == "ACCEPTED"
    assert call_count == 2
    assert res2["recipients_accepted"] == 1

    # Attempt 3: Now that attempt 2 SUCCEEDED, attempt 3 MUST be suppressed as duplicate
    res3 = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)
    assert res3["status"] == "SKIPPED"
    assert res3["reason"] == "duplicate"
    assert call_count == 2  # No third HTTP call made


def test_unknown_timeout_holds_lock_to_prevent_blind_resending(monkeypatch):
    """
    Proves that an ambiguous network timeout puts the key into UNKNOWN_TIMEOUT hold,
    preventing blind duplicate re-posting.
    """
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9599087957",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="",
            WHATSAPP_DEPARTMENT_TEMPLATE_LANG="en_US",
        ),
    )

    call_count = 0

    def mock_send_text(phone, msg):
        nonlocal call_count
        call_count += 1
        return {"success": False, "status": "timeout_unverified", "error": "Timeout"}

    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True)
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.send_text_message", lambda self, phone, msg: mock_send_text(phone, msg))

    context = {"booking_ref": "SHF-TIMEOUT-HOLD-99", "service_category": "Meet & Greet"}

    # First attempt encounters timeout
    res1 = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)
    assert res1["status"] == "UNKNOWN"
    assert call_count == 1

    # Immediate second attempt is held to prevent blind duplicate message
    res2 = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)
    assert res2["status"] == "SKIPPED"
    assert res2["reason"] == "unknown_timeout"
    assert call_count == 1  # No duplicate call made


def test_two_different_bookings_within_24h_never_confused(monkeypatch):
    """
    Verifies that separate bookings within 24h are never suppressed by each other.
    """
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9599087957",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="",
            WHATSAPP_DEPARTMENT_TEMPLATE_LANG="en_US",
        ),
    )

    sent_refs = []

    def mock_send_text(phone, msg):
        sent_refs.append(msg)
        return {"success": True, "message_id": f"wamid.{len(sent_refs)}"}

    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True)
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.send_text_message", lambda self, phone, msg: mock_send_text(phone, msg))

    b1 = {"booking_ref": "SHF-BKG-ALPHA", "service_category": "Meet & Greet"}
    b2 = {"booking_ref": "SHF-BKG-BETA", "service_category": "Meet & Greet"}

    res1 = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(b1)
    res2 = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(b2)

    assert res1["status"] == "ACCEPTED"
    assert res2["status"] == "ACCEPTED"
    assert len(sent_refs) == 2


# ==========================================
# 5. Template Parameter Sanitization (<1024 Chars)
# ==========================================

def test_template_parameter_truncated_under_1024_chars(monkeypatch):
    """
    Meta Cloud API strictly rejects template parameters longer than 1024 characters.
    Verifies build_template_components truncates safe text to <= 1000 characters.
    """
    monkeypatch.setattr(
        "app.services.whatsapp_department_service.settings",
        SimpleNamespace(
            WHATSAPP_BOOKING_NOTIFY_PHONE_1="9599087957",
            WHATSAPP_BOOKING_NOTIFY_PHONE_2="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_3="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_4="",
            WHATSAPP_BOOKING_NOTIFY_PHONE_5="",
            WHATSAPP_OFFICER_NOTIFY_PHONE="",
            WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE="shafsky_test_template",
            WHATSAPP_DEPARTMENT_TEMPLATE_LANG="en_US",
        ),
    )

    sent_params = []

    def mock_send_message(to_phone, template_name, template_components=None, **kwargs):
        for comp in (template_components or []):
            for param in comp.get("parameters", []):
                sent_params.append(param.get("text", ""))
        return {"success": True, "message_id": "wamid.tpl_1024"}

    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True)
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.send_message", lambda self, **kwargs: mock_send_message(**kwargs))

    # Create very long context with 3000 characters of notes
    context = {
        "booking_ref": "SHF-LONG-NOTE-1",
        "service_category": "Meet & Greet",
        "notes": "X" * 3000,
    }

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)
    assert result["status"] == "ACCEPTED"
    assert len(sent_params) == 1
    assert len(sent_params[0]) <= 1000
    assert sent_params[0].endswith("...")


# ==========================================
# 6. Direct Ticketing Engine Integration
# ==========================================

def test_direct_ticketing_booking_invokes_notifications(monkeypatch):
    """
    Verifies that direct air ticket booking creation via TicketingService.create_booking
    triggers notification dispatch to the operations team.
    """
    mock_notify = MagicMock(return_value={"status": "ACCEPTED", "admin": []})
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify_booking_created",
        mock_notify,
    )

    db = MagicMock()
    payload = AirTicketBookingCreateRequest(
        contact_name="Aariz Khan",
        contact_email="aariz@shafsky.com",
        contact_phone="+919599087957",
        airline_name="Air India",
        flight_number="AI101",
        cabin_class="BUSINESS",
        origin_iata="DEL",
        destination_iata="JFK",
        departure_time=datetime(2026, 11, 15, 10, 30),
        base_fare=125000.0,
        taxes_amount=15000.0,
        currency="INR",
        notes="VIP passenger",
    )

    booking = TicketingService.create_booking(db, payload)

    assert booking.booking_ref.startswith("TKT-")
    mock_notify.assert_called_once()
    called_ctx = mock_notify.call_args[0][1]
    assert called_ctx["service_category"] == "Ticketing"
    assert called_ctx["passenger_name"] == "Aariz Khan"
    assert called_ctx["flight_num"] == "AI101"
    assert called_ctx["origin_code"] == "DEL"
    assert called_ctx["dest_code"] == "JFK"


# ==========================================
# 7. Webhook Delivery Status Integration
# ==========================================

def test_webhook_delivery_status_updates_notification_record():
    """
    Verifies that when Meta sends a delivery receipt status webhook ('delivered'),
    the matching NotificationRecord in the database is updated to DELIVERED with timestamp.
    """
    db = MagicMock()
    rec = NotificationRecord(
        id=uuid.uuid4(),
        template_type="WHATSAPP_OPS_NOTIFICATION",
        message_id="wamid.RECEIPT_TEST_123",
        status=NotificationStatus.COMPLETED,
    )
    db.scalars.return_value = [rec]

    webhook_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "biz_123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "statuses": [
                                {
                                    "id": "wamid.RECEIPT_TEST_123",
                                    "status": "delivered",
                                    "timestamp": "1730000000",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }

    result = WhatsAppService.handle_incoming_webhook(db, webhook_payload)
    assert result["statuses_handled"] == 1
    assert rec.status == NotificationStatus.DELIVERED
    assert rec.delivered_at is not None
    db.commit.assert_called()


# ==========================================
# 8. Air Charter Dedicated Notification Tests
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
            WHATSAPP_DEPARTMENT_TEMPLATE_LANG="en_US",
        ),
    )

    sent_charter_phones = []

    def mock_send_text(phone, msg):
        sent_charter_phones.append(phone)
        return {"success": True, "message_id": f"wamid.charter.{phone}"}

    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True)
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.send_text_message", lambda self, phone, msg: mock_send_text(phone, msg))

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

    # 1. First send -> all 5 accepted
    res1 = WhatsAppDepartmentService.dispatch_charter_notification(charter_obj)
    assert res1["status"] == "ACCEPTED"
    assert res1["recipients_accepted"] == 5
    assert len(sent_charter_phones) == 5

    # 2. Duplicate send -> SKIPPED
    res2 = WhatsAppDepartmentService.dispatch_charter_notification(charter_obj)
    assert res2["status"] == "SKIPPED"
    assert res2["reason"] == "duplicate"
    assert len(sent_charter_phones) == 5  # No extra calls
