"""
Department (operations) WhatsApp notification regression + isolation tests.

These lock in the guarantees the customer-notification work must not break:

1. A booking notifies all five configured recipients, with the existing routing.
2. Department dispatch does not depend on any customer template setting.
3. A customer notification failure cannot abort department dispatch.
4. Department failures are reported per recipient.
5. The five recipients are unchanged.
6. The department template language is self-healed on 132001 instead of silently
   killing every alert.
7. Re-processing the same event does not send twice.

Database-free: the dispatcher and the WhatsApp client are exercised with recorders.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.whatsapp.client import WhatsAppClient
from app.services.notification_service import NotificationService
from app.services.whatsapp_department_service import WhatsAppDepartmentService

DEPARTMENT_RECIPIENTS = [
    "919599087957",
    "919599225778",
    "919217522660",
    "918864931247",
    "919599087959",
]


def _booking_context(ref: str | None = None) -> dict:
    return {
        "booking_ref": ref or f"SHF-DEPT-{uuid.uuid4().hex[:8].upper()}",
        "passenger_name": "Department Check",
        "passenger_email": "check@example.test",
        "passenger_phone": "+919876543210",
        "passenger_count": 2,
        "service_category": "Airport Assistance",
        "service_type": "Meet & Greet",
        "service_name": "Meet & Greet",
        "flight_num": "6E-2045",
        "origin_code": "DEL",
        "dest_code": "DEL",
        "airport_code": "DEL",
        "journey_type": "ARRIVAL",
        "departure_time": "2026-11-15T14:30:00",
        "total_amount": 5500.0,
        "currency": "INR",
        "status": "PENDING",
    }


class _Sends:
    """Collects what the dispatcher hands to the WhatsApp client."""

    def __init__(self):
        self.calls = []
        self.result = lambda index: {"success": True, "message_id": f"wamid.sim.{index}"}


@pytest.fixture()

def department_recorder(monkeypatch):
    """Force the free-form department path (no template) and record sends."""
    monkeypatch.setattr(
        WhatsAppDepartmentService,
        "_resolve_template",
        classmethod(lambda cls: ("", "")),
    )
    sends = _Sends()

    def fake_send_message(self, **kwargs):
        sends.calls.append(("template", kwargs))
        return sends.result(len(sends.calls))

    def fake_send_text(self, to_phone, message_body):
        sends.calls.append(("text", {"to_phone": to_phone, "message_body": message_body}))
        return sends.result(len(sends.calls))

    monkeypatch.setattr(WhatsAppClient, "send_message", fake_send_message)
    monkeypatch.setattr(WhatsAppClient, "send_text_message", fake_send_text)
    monkeypatch.setattr(WhatsAppClient, "is_configured", lambda self: True)
    return sends


def test_recipients_are_the_five_configured_numbers():
    """Routing config is untouched: the same five E.164 recipients, in order."""
    assert WhatsAppDepartmentService.get_recipient_phone_numbers() == DEPARTMENT_RECIPIENTS


def test_booking_notifies_all_five_recipients(department_recorder):
    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(_booking_context())

    assert result["status"] == "ACCEPTED"
    assert result["recipients_attempted"] == 5
    assert [call[1]["to_phone"] for call in department_recorder.calls] == DEPARTMENT_RECIPIENTS


def test_department_dispatch_ignores_customer_template_settings(department_recorder, monkeypatch):
    """A missing/broken customer template must not change department behaviour."""
    monkeypatch.setattr(
        "app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", ""
    )
    monkeypatch.setattr(
        "app.services.notification_service.settings.WHATSAPP_CUSTOMER_TEMPLATE_LANG", "not-a-language"
    )

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(_booking_context())

    assert result["status"] == "ACCEPTED"
    assert len(department_recorder.calls) == 5
    assert all(call[0] == "text" for call in department_recorder.calls)


def test_customer_notification_failure_does_not_block_department(department_recorder, monkeypatch):
    """`notify_booking_created` dispatches the department alert before any customer work."""
    from app.models.schema import NotificationStatus

    class _BoardingRecord:
        id = "00000000-0000-0000-0000-000000000009"
        payload = {}
        status = NotificationStatus.SENDING
        attempts = 1
        max_attempts = 3
        message_id = None
        error_log = None
        delivered_at = None
        updated_at = None
        recipient_email = None
        recipient_phone = None

    def exploding_claim(*_args, **_kwargs):
        raise RuntimeError("customer claim exploded")

    monkeypatch.setattr(
        NotificationService, "_acquire_notification_claim", classmethod(lambda cls, *a, **k: exploding_claim())
    )

    summary = NotificationService.notify_booking_created(MagicMock(), _booking_context())

    assert summary["admin"][0]["status"] == "ACCEPTED"
    assert len(department_recorder.calls) == 5

    # And the customer path, when deliberately invoked, fails without raising.
    customer = NotificationService.send_customer_whatsapp_confirmation(
        MagicMock(),
        {
            "booking_ref": "SHF-DEPT-CUST-FAIL",
            "passenger_phone": "9876543210",
            "status": "CONFIRMED",
        },
    )
    assert customer["status"] == "FAILED"
    assert len(department_recorder.calls) == 5  # no extra department send, nothing crashed


def test_department_failure_is_reported_per_recipient(department_recorder):
    department_recorder.result = lambda index: {
        "success": False,
        "status": "failed",
        "status_code": 400,
        "error_code": 131047,
        "error": "Meta API Error (400): re-engagement",
    }

    result = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(_booking_context())

    assert result["status"] == "FAILED"
    assert len(result["recipient_results"]) == 5
    assert all(entry["status"] == "WINDOW_CLOSED" for entry in result["recipient_results"])
    assert all(entry.get("remedy") for entry in result["recipient_results"])


def test_department_template_language_is_self_healed_on_132001(monkeypatch):
    """A wrong WHATSAPP_DEPARTMENT_TEMPLATE_LANG must not silence all five alerts."""
    calls = []

    def fake_send(self, **kwargs):
        calls.append(kwargs["template_language"])
        if kwargs["template_language"] == "en":
            return {
                "success": False,
                "status": "failed",
                "status_code": 404,
                "error_code": 132001,
                "error": "(#132001) Template name does not exist in the translation",
            }
        return {"success": True, "message_id": "wamid.healed", "status": "sent", "status_code": 200}

    monkeypatch.setattr(WhatsAppClient, "send_message", fake_send)

    res = WhatsAppDepartmentService._send_to_recipient(
        "919599087957", "New booking SHF-1", "shafsky_ops_alert", "en"
    )

    assert res["success"] is True
    assert calls == ["en", "en_US"]


def test_duplicate_event_is_not_sent_twice(department_recorder):
    context = _booking_context("SHF-DEPT-DUP-1")

    first = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)
    second = WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification(context)

    assert first["status"] == "ACCEPTED"
    assert second["status"] == "SKIPPED"
    assert len(department_recorder.calls) == 5  # second attempt added no send
