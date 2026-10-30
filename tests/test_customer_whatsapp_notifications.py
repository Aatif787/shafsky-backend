"""
Comprehensive test suite for Customer WhatsApp Booking Confirmation Notifications.

Verifies:
1. Dynamic customer phone retrieval & E.164 normalization from booking data/context.
2. WhatsApp notification sent to customer's own mobile number (never hardcoded, never internal phones).
3. Internal department notifications (5 universal phones) continue working independently and isolated.
4. Message content includes available details (Customer name, Ref, Airport, Service, Flight, Date/Time, Status, Support)
   without inventing missing values or rendering dummy placeholders.
5. Trigger & payment validation: only CONFIRMED/paid bookings trigger notifications; PENDING/FAILED/CANCELLED are blocked.
6. Duplicate prevention: second dispatch attempt for the same booking is suppressed via atomic idempotency claim.
7. Meta template dispatch when configured, and freeform text fallback when unconfigured.
8. Meta API failure handling (4xx/5xx, timeouts, exceptions) without crashing booking workflow.
9. Full integration with NotificationService.notify_booking_confirmed.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from app.database import SessionLocal
from app.models.schema import Booking, BookingStatus, NotificationRecord, NotificationStatus
from app.models.whatsapp_models import WhatsAppConversation
from app.services.notification_service import NotificationService
from app.services.whatsapp_department_service import WhatsAppDepartmentService
from app.integrations.whatsapp.client import whatsapp_client


def _purge_customer_notifications():
    """Purge customer WhatsApp notification records between test runs."""
    try:
        from sqlalchemy import delete
        with SessionLocal() as db:
            db.execute(
                delete(NotificationRecord).where(
                    NotificationRecord.template_type == NotificationService.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE_TYPE
                )
            )
            db.commit()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def clean_customer_notification_state():
    _purge_customer_notifications()
    yield
    _purge_customer_notifications()


# ==============================================================================
# 1. Phone Retrieval & Normalization
# ==============================================================================

def test_customer_phone_retrieval_and_normalization(monkeypatch):
    """Verifies customer phone is retrieved from booking context and normalized to E.164 without '+'."""
    mock_send = MagicMock(return_value={"success": True, "message_id": "wamid.cust.123"})
    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_send)
    monkeypatch.setattr("app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", "")

    with SessionLocal() as db:
        context = {
            "booking_ref": "SHF-CUST-PHONE-01",
            "passenger_phone": "+91 98765-43210",
            "passenger_name": "Rohan Sharma",
            "status": "CONFIRMED",
            "airport_name": "Indira Gandhi International Airport, New Delhi",
            "service_type": "Arrival Meet & Greet",
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

    assert res["status"] == "ACCEPTED"
    assert res["message_id"] == "wamid.cust.123"
    assert res["booking_ref"] == "SHF-CUST-PHONE-01"

    # Meta client called with normalized 919876543210
    mock_send.assert_called_once()
    call_args = mock_send.call_args[1]
    assert call_args["to_phone"] == "919876543210"
    assert "Rohan Sharma" in call_args["message_body"]
    assert "SHF-CUST-PHONE-01" in call_args["message_body"]


def test_customer_phone_supports_alternate_keys(monkeypatch):
    """Verifies passengerPhone, phone, or contact_phone context keys are respected."""
    mock_send = MagicMock(return_value={"success": True, "message_id": "wamid.cust.alt"})
    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_send)
    monkeypatch.setattr("app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", "")

    with SessionLocal() as db:
        context = {
            "booking_ref": "SHF-CUST-ALT-KEY",
            "passengerPhone": "09811122233",  # leading 0 format
            "passengerName": "Priya Nair",
            "status": "CONFIRMED",
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

    assert res["status"] == "ACCEPTED"
    mock_send.assert_called_once()
    assert mock_send.call_args[1]["to_phone"] == "919811122233"


# ==============================================================================
# 2. Isolation from Department Notifications
# ==============================================================================

def test_customer_notification_does_not_send_to_department_phones(monkeypatch):
    """Verifies that customer notification goes only to the customer, not internal department phones."""
    dept_phones = [
        "919599087957",
        "919599225778",
        "919217522660",
        "918864931247",
        "919599087959",
    ]
    dispatched_recipients = []

    def mock_send_text(to_phone, message_body):
        dispatched_recipients.append(to_phone)
        return {"success": True, "message_id": f"wamid.{to_phone}"}

    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_send_text)
    monkeypatch.setattr("app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", "")

    customer_phone = "9876543210"  # normalizes to 919876543210
    with SessionLocal() as db:
        context = {
            "booking_ref": "SHF-ISOLATION-01",
            "passenger_phone": customer_phone,
            "passenger_name": "Kavita Rao",
            "status": "CONFIRMED",
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

    assert res["status"] == "ACCEPTED"
    assert len(dispatched_recipients) == 1
    assert dispatched_recipients[0] == "919876543210"
    # Ensure none of the department phones were targeted by customer confirmation
    for dp in dept_phones:
        assert dp not in dispatched_recipients


# ==============================================================================
# 3. Missing and Invalid Customer Numbers
# ==============================================================================

def test_missing_customer_phone_skips_gracefully():
    """Missing customer phone skips without error or throwing exception."""
    with SessionLocal() as db:
        context = {
            "booking_ref": "SHF-NO-PHONE",
            "passenger_name": "No Phone User",
            "status": "CONFIRMED",
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

    assert res["status"] == "SKIPPED"
    assert res["reason"] == "missing_customer_phone"


def test_invalid_customer_phone_records_failed_without_crashing():
    """Malformed or short phone number fails safely without crashing."""
    with SessionLocal() as db:
        context = {
            "booking_ref": "SHF-BAD-PHONE",
            "passenger_phone": "12345",  # Too short
            "passenger_name": "Bad Phone User",
            "status": "CONFIRMED",
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

    assert res["status"] == "FAILED"
    assert res["reason"] == "invalid_customer_phone"


# ==============================================================================
# 4. Trigger & Payment Validation (Confirmed Only)
# ==============================================================================

@pytest.mark.parametrize("unconfirmed_status", ["PENDING", "FAILED", "CANCELLED", "INITIATED"])
def test_unconfirmed_booking_is_suppressed(unconfirmed_status, monkeypatch):
    """Bookings that are not confirmed/paid are rejected and not messaged."""
    mock_send = MagicMock()
    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_send)

    with SessionLocal() as db:
        context = {
            "booking_ref": f"SHF-UNCONF-{unconfirmed_status}",
            "passenger_phone": "9876543210",
            "passenger_name": "Tester",
            "status": unconfirmed_status,
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

    assert res["status"] == "SKIPPED"
    assert res["reason"] == "booking_not_confirmed"
    mock_send.assert_not_called()


def test_db_booking_status_validation(monkeypatch):
    """When DB booking record exists and is PENDING, confirmation is skipped."""
    mock_send = MagicMock()
    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_send)
    monkeypatch.setattr("app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", "")

    booking_ref = f"SHF-DB-PEND-{uuid.uuid4().hex[:6].upper()}"

    with SessionLocal() as db:
        # Create a pending booking in the database
        booking = Booking(
            booking_ref=booking_ref,
            passenger_name="Arjun Kapoor",
            passenger_email="arjun@example.com",
            passenger_phone="9876543210",
            service_type="VIP Meet & Greet",
            status=BookingStatus.PENDING,
            total_amount=5000,
            currency="INR",
        )
        db.add(booking)
        db.commit()

        # Attempt sending confirmation while booking in DB is still PENDING
        context = {
            "booking_ref": booking_ref,
            "passenger_phone": "9876543210",
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

        assert res["status"] == "SKIPPED"
        assert res["reason"] == "booking_not_confirmed"
        mock_send.assert_not_called()

        # Update to CONFIRMED and try again
        booking.status = BookingStatus.CONFIRMED
        db.commit()

        mock_send.return_value = {"success": True, "message_id": "wamid.confirmed"}
        res2 = NotificationService.send_customer_whatsapp_confirmation(db, context)
        assert res2["status"] == "ACCEPTED"
        mock_send.assert_called_once()


# ==============================================================================
# 5. Duplicate Event Suppression (Idempotency)
# ==============================================================================

def test_duplicate_customer_notification_suppression(monkeypatch):
    """Second call for the same confirmed booking returns SKIPPED (duplicate)."""
    mock_send = MagicMock(return_value={"success": True, "message_id": "wamid.cust.first"})
    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_send)
    monkeypatch.setattr("app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", "")

    ref = f"SHF-DUP-{uuid.uuid4().hex[:6].upper()}"
    context = {
        "booking_ref": ref,
        "passenger_phone": "9876543210",
        "passenger_name": "Vikram Seth",
        "status": "CONFIRMED",
    }

    with SessionLocal() as db:
        # First attempt -> SUCCESS
        res1 = NotificationService.send_customer_whatsapp_confirmation(db, context)
        assert res1["status"] == "ACCEPTED"
        assert res1["message_id"] == "wamid.cust.first"
        assert mock_send.call_count == 1

        # Second attempt -> SKIPPED (duplicate)
        res2 = NotificationService.send_customer_whatsapp_confirmation(db, context)
        assert res2["status"] == "SKIPPED"
        assert res2["reason"] == "duplicate"
        assert res2["claim_status"] == "ALREADY_DELIVERED"
        # Meta API was NOT called again
        assert mock_send.call_count == 1


# ==============================================================================
# 6. Message Content Details and Placeholders
# ==============================================================================

def test_message_content_includes_all_fields_when_present(monkeypatch):
    """Verifies that customer name, ref, airport, service, flight, date/time, and support phone are included."""
    sent_payloads = []

    def mock_send(to_phone, message_body):
        sent_payloads.append(message_body)
        return {"success": True, "message_id": "wamid.content.full"}

    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_send)
    monkeypatch.setattr("app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", "")

    with SessionLocal() as db:
        context = {
            "booking_ref": "SHF-FULL-001",
            "passenger_phone": "9876543210",
            "passenger_name": "Siddharth Malhotra",
            "airport_name": "Chhatrapati Shivaji Maharaj International Airport (BOM)",
            "service_type": "Departure VIP Service",
            "flight_num": "6E-2045",
            "departure_time": "2026-11-15T14:30:00",
            "status": "CONFIRMED",
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

    assert res["status"] == "ACCEPTED"
    assert len(sent_payloads) == 1
    body = sent_payloads[0]

    assert "Siddharth Malhotra" in body
    assert "SHF-FULL-001" in body
    assert "Chhatrapati Shivaji Maharaj International Airport (BOM)" in body
    assert "Departure VIP Service" in body
    assert "Flight:* 6E-2045" in body
    assert "Date & Time:*" in body
    assert "Status:* CONFIRMED" in body
    assert "+91 9599087959" in body  # Support phone


def test_message_content_omits_missing_flight_and_date_without_placeholders(monkeypatch):
    """When flight or date is missing or dummy, no dummy line like 'Flight: Flight' is rendered."""
    sent_payloads = []

    def mock_send(to_phone, message_body):
        sent_payloads.append(message_body)
        return {"success": True, "message_id": "wamid.content.sparse"}

    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_send)
    monkeypatch.setattr("app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", "")

    with SessionLocal() as db:
        context = {
            "booking_ref": "SHF-SPARSE-002",
            "passenger_phone": "9876543210",
            "passenger_name": "Meera Patel",
            "airport_name": "DEL",
            "service_type": "Transit Assist",
            "flight_num": "Flight",  # Dummy value to be omitted
            "departure_time": None,  # Missing
            "status": "CONFIRMED",
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

    assert res["status"] == "ACCEPTED"
    body = sent_payloads[0]

    assert "Meera Patel" in body
    assert "SHF-SPARSE-002" in body
    assert "Transit Assist" in body
    assert "Flight: Flight" not in body
    assert "Flight:*" not in body
    assert "Date & Time:*" not in body
    assert "Status:* CONFIRMED" in body


# ==============================================================================
# 7. Template Dispatch vs Freeform Fallback
# ==============================================================================

def test_template_dispatch_when_configured(monkeypatch):
    """When WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE is set, dispatches via send_message with template components."""
    mock_send_template = MagicMock(return_value={"success": True, "message_id": "wamid.tmpl.001"})
    mock_send_text = MagicMock()

    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "send_message", mock_send_template)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_send_text)
    monkeypatch.setattr(
        "app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE",
        "shafsky_booking_confirmation",
    )
    monkeypatch.setattr(
        "app.services.notification_service.settings.WHATSAPP_CUSTOMER_TEMPLATE_LANG",
        "en_US",
    )

    # No recent inbound conversation: the customer is outside the 24-hour window.
    _clear_conversation("919876543210")

    with SessionLocal() as db:
        context = {
            "booking_ref": "SHF-TMPL-TEST",
            "passenger_phone": "9876543210",
            "passenger_name": "Deepak Sharma",
            "status": "CONFIRMED",
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

    assert res["status"] == "ACCEPTED"
    assert res["template_configured"] is True

    mock_send_template.assert_called_once()
    mock_send_text.assert_not_called()

    kwargs = mock_send_template.call_args[1]
    assert kwargs["to_phone"] == "919876543210"
    assert kwargs["template_name"] == "shafsky_booking_confirmation"
    assert kwargs["template_language"] == "en_US"
    assert len(kwargs["template_components"]) == 1
    assert kwargs["template_components"][0]["type"] == "body"
    assert "Deepak Sharma" in kwargs["template_components"][0]["parameters"][0]["text"]


# ==============================================================================
# 8. Meta API Failures & Safety
# ==============================================================================

def test_meta_api_failure_does_not_raise(monkeypatch):
    """When Meta returns an error (e.g. 131047 outside 24h window), handles safely without raising."""
    error_response = {
        "success": False,
        "error": "Message failed to send because more than 24 hours have passed since the customer last replied",
        "error_code": 131047,
        "status": "failed",
    }
    mock_send = MagicMock(return_value=error_response)
    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_send)
    monkeypatch.setattr("app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", "")

    ref = f"SHF-ERR-{uuid.uuid4().hex[:6].upper()}"
    with SessionLocal() as db:
        context = {
            "booking_ref": ref,
            "passenger_phone": "9876543210",
            "passenger_name": "Sunil Varma",
            "status": "CONFIRMED",
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

    assert res["status"] == "FAILED"
    assert "24 hours" in res["error"]

    # Verify NotificationRecord is in FAILED state in database
    with SessionLocal() as db:
        from sqlalchemy import select
        record = db.scalar(
            select(NotificationRecord).where(
                NotificationRecord.template_type == NotificationService.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE_TYPE
            ).order_by(NotificationRecord.created_at.desc()).limit(1)
        )
        assert record is not None
        assert record.status in (NotificationStatus.FAILED, NotificationStatus.FAILED.value)


def test_meta_api_timeout_recorded_as_unknown(monkeypatch):
    """When Meta API times out, outcome is UNKNOWN and record is PENDING."""
    timeout_response = {
        "success": False,
        "error": "Request timed out",
        "status": "timeout_unverified",
    }
    mock_send = MagicMock(return_value=timeout_response)
    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_send)
    monkeypatch.setattr("app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", "")

    with SessionLocal() as db:
        context = {
            "booking_ref": "SHF-TIMEOUT-01",
            "passenger_phone": "9876543210",
            "passenger_name": "Ananya Roy",
            "status": "CONFIRMED",
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

    assert res["status"] == "UNKNOWN"


def test_unconfigured_whatsapp_client_bypasses(monkeypatch):
    """If Meta WhatsApp credentials are not configured, gracefully returns BYPASSED."""
    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: False)

    with SessionLocal() as db:
        context = {
            "booking_ref": "SHF-BYPASS-01",
            "passenger_phone": "9876543210",
            "passenger_name": "Test User",
            "status": "CONFIRMED",
        }
        res = NotificationService.send_customer_whatsapp_confirmation(db, context)

    assert res["status"] == "BYPASSED"
    assert res["reason"] == "whatsapp_client_unconfigured"


# ==============================================================================
# 9. notify_booking_confirmed Integration
# ==============================================================================

def test_notify_booking_confirmed_includes_customer_whatsapp(monkeypatch):
    """notify_booking_confirmed dispatches both customer email and customer WhatsApp."""
    monkeypatch.setattr(
        NotificationService,
        "admin_notification_recipients",
        classmethod(lambda cls: []),
    )
    monkeypatch.setattr(
        NotificationService,
        "send_email_resend_sync",
        classmethod(lambda cls, *a, **k: {"status": "DELIVERED", "message_id": "email_conf_123"}),
    )
    monkeypatch.setattr(
        NotificationService,
        "_acquire_notification_claim",
        classmethod(lambda *a, **k: (MagicMock(), "PROCEED")),
    )
    monkeypatch.setattr(
        NotificationService,
        "send_customer_whatsapp_confirmation",
        classmethod(lambda cls, db, ctx: {"status": "ACCEPTED", "message_id": "wamid.conf.mock"}),
    )

    db = MagicMock()
    context = {
        "booking_ref": "SHF-INTEG-001",
        "passenger_email": "guest@example.com",
        "passenger_phone": "+91 98765 43210",
        "passenger_name": "Karan Johar",
        "service_type": "Gold VIP",
        "status": "CONFIRMED",
    }
    summary = NotificationService.notify_booking_confirmed(db, context)

    assert summary["customer"]["status"] == "DELIVERED"
    assert summary["customer_whatsapp"]["status"] == "ACCEPTED"
    assert summary["customer_whatsapp"]["message_id"] == "wamid.conf.mock"


# ==============================================================================
# 10. Service-window routing: free-form inside 24h, template outside
# ==============================================================================

def _seed_conversation(phone: str, last_activity: datetime) -> None:
    from sqlalchemy import delete

    with SessionLocal() as db:
        db.execute(delete(WhatsAppConversation).where(WhatsAppConversation.phone_number == phone))
        db.add(
            WhatsAppConversation(
                phone_number=phone,
                current_state="START",
                last_user_activity_at=last_activity,
            )
        )
        db.commit()


def _clear_conversation(phone: str) -> None:
    from sqlalchemy import delete

    with SessionLocal() as db:
        db.execute(delete(WhatsAppConversation).where(WhatsAppConversation.phone_number == phone))
        db.commit()


def _configure_customer_template(monkeypatch):
    monkeypatch.setattr(whatsapp_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        "app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE",
        "shafsky_booking_confirmation",
    )
    monkeypatch.setattr(
        "app.services.notification_service.settings.WHATSAPP_CUSTOMER_TEMPLATE_LANG",
        "en",
    )


def test_open_service_window_uses_freeform_even_when_a_template_is_configured(monkeypatch):
    """A customer who messaged us inside 24h can receive free-form, which needs no billing."""
    phone = "919876500011"
    mock_template = MagicMock(return_value={"success": True, "message_id": "wamid.tmpl.win"})
    mock_text = MagicMock(return_value={"success": True, "message_id": "wamid.free.win"})
    _configure_customer_template(monkeypatch)
    monkeypatch.setattr(whatsapp_client, "send_message", mock_template)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_text)

    _seed_conversation(phone, datetime.now(timezone.utc) - timedelta(hours=2))

    try:
        with SessionLocal() as db:
            res = NotificationService.send_customer_whatsapp_confirmation(
                db,
                {
                    "booking_ref": "SHF-WINDOW-OPEN-01",
                    "passenger_phone": "9876500011",
                    "passenger_name": "Window Open",
                    "status": "CONFIRMED",
                },
            )
    finally:
        _clear_conversation(phone)

    assert res["status"] == "ACCEPTED"
    assert res["channel"] == "freeform"
    mock_text.assert_called_once()
    mock_template.assert_not_called()
    assert mock_text.call_args[1]["to_phone"] == phone


def test_closed_service_window_uses_the_approved_template(monkeypatch):
    phone = "919876500012"
    mock_template = MagicMock(return_value={"success": True, "message_id": "wamid.tmpl.closed"})
    mock_text = MagicMock()
    _configure_customer_template(monkeypatch)
    monkeypatch.setattr(whatsapp_client, "send_message", mock_template)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_text)

    _seed_conversation(phone, datetime.now(timezone.utc) - timedelta(hours=48))

    try:
        with SessionLocal() as db:
            res = NotificationService.send_customer_whatsapp_confirmation(
                db,
                {
                    "booking_ref": "SHF-WINDOW-CLOSED-01",
                    "passenger_phone": "9876500012",
                    "passenger_name": "Window Closed",
                    "status": "CONFIRMED",
                },
            )
    finally:
        _clear_conversation(phone)

    assert res["status"] == "ACCEPTED"
    assert res["channel"] == "template"
    mock_template.assert_called_once()
    mock_text.assert_not_called()


def test_no_conversation_uses_the_approved_template(monkeypatch):
    phone = "919876500013"
    mock_template = MagicMock(return_value={"success": True, "message_id": "wamid.tmpl.none"})
    mock_text = MagicMock()
    _configure_customer_template(monkeypatch)
    monkeypatch.setattr(whatsapp_client, "send_message", mock_template)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_text)

    _clear_conversation(phone)

    with SessionLocal() as db:
        res = NotificationService.send_customer_whatsapp_confirmation(
            db,
            {
                "booking_ref": "SHF-WINDOW-NONE-01",
                "passenger_phone": "9876500013",
                "passenger_name": "No Conversation",
                "status": "CONFIRMED",
            },
        )

    assert res["status"] == "ACCEPTED"
    assert res["channel"] == "template"
    mock_template.assert_called_once()
    mock_text.assert_not_called()


def test_freeform_rejected_with_131047_falls_back_to_template(monkeypatch):
    """If the window estimate is stale, Meta rejects free-form before creating a message."""
    phone = "919876500014"
    mock_template = MagicMock(return_value={"success": True, "message_id": "wamid.tmpl.fallback"})
    mock_text = MagicMock(
        return_value={
            "success": False,
            "error": "Re-engagement message",
            "error_code": 131047,
            "status": "failed",
        }
    )
    _configure_customer_template(monkeypatch)
    monkeypatch.setattr(whatsapp_client, "send_message", mock_template)
    monkeypatch.setattr(whatsapp_client, "send_text_message", mock_text)

    _seed_conversation(phone, datetime.now(timezone.utc) - timedelta(hours=1))

    try:
        with SessionLocal() as db:
            res = NotificationService.send_customer_whatsapp_confirmation(
                db,
                {
                    "booking_ref": "SHF-WINDOW-STALE-01",
                    "passenger_phone": "9876500014",
                    "passenger_name": "Stale Window",
                    "status": "CONFIRMED",
                },
            )
    finally:
        _clear_conversation(phone)

    assert res["status"] == "ACCEPTED"
    assert res["channel"] == "template"
    mock_text.assert_called_once()
    mock_template.assert_called_once()
