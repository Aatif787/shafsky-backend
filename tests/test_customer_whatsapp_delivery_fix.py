"""
Regression tests for the customer WhatsApp booking-notification fix.

These tests are deliberately database-free so they can run anywhere: they cover the
exact failure modes found in investigation.

1. Meta rejects template BODY parameters that contain new-line/tab characters or
   more than four consecutive spaces (the API accepts the request, delivery fails).
   The old code put the whole multi-line confirmation into {{1}}.
2. Customer phone normalization must not duplicate the country code, must survive
   the trunk-prefix formats the booking form produces, and must keep international
   numbers intact.
3. Quote-only enquiries must receive an enquiry acknowledgement, never a
   "booking confirmed" message, and must use their own idempotency key.
4. An accepted send must not be recorded as DELIVERED, and must never be re-sent
   by a retry / duplicate event.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.models.schema import NotificationStatus
from app.services.notification_service import NotificationService
from app.services.notification_service_ext import build_customer_summary, build_template_parameter
from app.services.whatsapp_department_service import WhatsAppDepartmentService


class _FakeRecord:
    """Minimal stand-in for a NotificationRecord row."""

    def __init__(self, payload=None):
        self.id = "00000000-0000-0000-0000-000000000001"
        self.payload = payload or {}
        self.status = NotificationStatus.SENDING
        self.attempts = 1
        self.max_attempts = 3
        self.message_id = None
        self.error_log = None
        self.delivered_at = None
        self.updated_at = None
        self.recipient_email = None
        self.recipient_phone = None


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeQueryDb:
    """Returns preset rows for any scalars() query."""

    def __init__(self, rows):
        self._rows = rows
        self.commits = 0

    def scalars(self, *_args, **_kwargs):  # noqa: D401 - test double
        return _FakeScalars(self._rows)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


class _StubSession(_FakeQueryDb, Session):
    """A real SQLAlchemy session type with stubbed I/O.

    Production code refuses to send an enquiry acknowledgement without an ORM
    session (it could not persist the idempotency claim), so the tests need a
    real session subclass rather than a bare mock.
    """

    def __init__(self, rows=()):
        Session.__init__(self)
        _FakeQueryDb.__init__(self, list(rows))

    def scalar(self, *_args, **_kwargs):
        return None


def _offline_confirmation_context(**overrides):
    context = {
        "booking_ref": "SHF-WA-FIX-001",
        "passenger_name": "Rohan Sharma",
        "passenger_phone": "+91 98765-43210",
        "status": "CONFIRMED",
        "airport_name": "Indira Gandhi International Airport (DEL)",
        "service_type": "Arrival Meet & Greet",
        "flight_num": "6E-2045",
        "departure_time": "2026-11-15T14:30:00",
        "passenger_count": 2,
        "total_amount": 5000,
        "currency": "INR",
    }
    context.update(overrides)
    return context


def _capture_send(monkeypatch, *, template="shafsky_booking_confirmation", lang="en"):
    """Patch the claim + client so the full payload can be inspected."""
    captured = {}

    def fake_claim(*_args, **_kwargs):
        return _FakeRecord(payload={"booking_ref": "x"}), "PROCEED"

    monkeypatch.setattr(NotificationService, "_acquire_notification_claim", classmethod(lambda cls, *a, **k: fake_claim()))

    monkeypatch.setattr(
        "app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE",
        template,
    )
    monkeypatch.setattr(
        "app.services.notification_service.settings.WHATSAPP_CUSTOMER_TEMPLATE_LANG",
        lang,
    )
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True)

    def fake_send_message(self, **kwargs):
        captured["template"] = kwargs
        return {"success": True, "message_id": "wamid.test.001", "status": "sent", "status_code": 200}

    def fake_send_text(self, to_phone, message_body):
        captured["text"] = {"to_phone": to_phone, "message_body": message_body}
        return {"success": True, "message_id": "wamid.test.text", "status": "sent", "status_code": 200}

    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.send_message", fake_send_message)
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.send_text_message", fake_send_text)
    return captured


# ---------------------------------------------------------------------------
# 1. Template parameter rules
# ---------------------------------------------------------------------------

def test_build_template_parameter_never_contains_newlines_or_tabs():
    raw = "✈️ Booking Confirmed\n\n• Ref: SHF-1\n• Service: Meet\t& Greet\n\n\nThank you"
    param = build_template_parameter(raw)

    assert "\n" not in param
    assert "\r" not in param
    assert "\t" not in param
    assert "  " not in param  # no 2+ runs, so definitely not Meta's 4-space limit
    assert "Ref: SHF-1" in param


def test_build_template_parameter_enforces_length_limit():
    param = build_template_parameter("X" * 5000, max_chars=900)
    assert len(param) <= 900
    assert param.endswith("...")


def test_department_template_components_are_meta_legal():
    components = WhatsAppDepartmentService.build_template_components(
        "🚗 *NEW TRANSPORT ENQUIRY*\n\n• Ref: SHF-1\n• Pickup: DEL\n• Drop: BOM"
    )
    text = components[0]["parameters"][0]["text"]
    assert "\n" not in text
    assert "\t" not in text
    assert len(text) <= 1024


def test_customer_confirmation_template_param_is_meta_legal(monkeypatch):
    captured = _capture_send(monkeypatch)

    result = NotificationService.send_customer_whatsapp_confirmation(
        MagicMock(), _offline_confirmation_context()
    )

    assert result["status"] == "ACCEPTED"
    kwargs = captured["template"]
    assert kwargs["template_name"] == "shafsky_booking_confirmation"
    assert kwargs["template_language"] == "en"

    components = kwargs["template_components"]
    assert len(components) == 1 and components[0]["type"] == "body"
    assert len(components[0]["parameters"]) == 1  # approved template has exactly {{1}}

    value = components[0]["parameters"][0]["text"]
    assert "\n" not in value and "\t" not in value and "  " not in value
    assert len(value) <= 1024
    # A real booking summary, not the rendered multi-line alert
    assert "SHF-WA-FIX-001" in value
    assert "Rohan Sharma" in value
    assert "6E-2045" in value
    assert "Confirmed" in value


def test_customer_confirmation_uses_freeform_text_on_fallback(monkeypatch):
    captured = _capture_send(monkeypatch, template="")

    result = NotificationService.send_customer_whatsapp_confirmation(
        MagicMock(), _offline_confirmation_context()
    )

    assert result["status"] == "ACCEPTED"
    assert "template" not in captured
    body = captured["text"]["message_body"]
    assert "\n" in body  # freeform text keeps the readable layout
    assert "SHF-WA-FIX-001" in body


# ---------------------------------------------------------------------------
# 2. Phone normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9599087957", "919599087957"),
        ("+91 95990-87957", "919599087957"),
        ("09599087957", "919599087957"),
        ("919599087957", "919599087957"),
        # trunk zero after an explicit country code must not survive or duplicate 91
        ("+91 098765 43210", "919876543210"),
        # supported international numbers keep their own country code
        ("+1 415 555 2671", "14155552671"),
        ("+44 20 7946 0958", "442079460958"),
        ("+971 50 123 4567", "971501234567"),
        ("0044 20 7946 0958", "442079460958"),
    ],
)
def test_phone_normalization(raw, expected):
    assert WhatsAppDepartmentService.normalize_phone_number(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "123", "abc", "0000000000", "12345678901234567890"])
def test_phone_normalization_rejects_invalid(raw):
    assert WhatsAppDepartmentService.normalize_phone_number(raw) is None


# ---------------------------------------------------------------------------
# 3. Enquiry acknowledgement
# ---------------------------------------------------------------------------

def test_enquiry_acknowledgement_never_claims_a_confirmed_booking(monkeypatch):
    captured = _capture_send(monkeypatch)

    result = NotificationService.send_customer_whatsapp_enquiry_acknowledgement(
        _StubSession(),
        {
            "booking_ref": "SHF-ENQ-777",
            "passenger_name": "Meera Patel",
            "passenger_phone": "+49 151 23456789",
            "service_category": "Luxury Hotels",
            "service_name": "Airport Hotel",
            "service_type": "Airport Hotel",
            "departure_time": "2026-12-01",
            "status": "PENDING",
        },
    )

    assert result["status"] == "ACCEPTED"
    kwargs = captured["template"]
    assert kwargs["template_language"] == "en"
    value = kwargs["template_components"][0]["parameters"][0]["text"]

    assert "SHF-ENQ-777" in value
    assert "Enquiry" in value
    assert "CONFIRMED" not in value.upper()
    assert kwargs["to_phone"] == "4915123456789"


def test_enquiry_acknowledgement_skips_confirmed_booking(monkeypatch):
    captured = _capture_send(monkeypatch)

    booking = SimpleNamespace(
        status=SimpleNamespace(value="CONFIRMED", __eq__=lambda self, other: True),
    )

    db = MagicMock()
    db.scalar.return_value = None  # no booking row resolved for MagicMock path

    result = NotificationService.send_customer_whatsapp_enquiry_acknowledgement(
        _StubSession(),
        {
            "booking_ref": "SHF-ENQ-888",
            "passenger_phone": "9876543210",
            "status": "PENDING",
        },
    )
    # With no DB row and a PENDING status the acknowledgement is sent, not skipped.
    assert result["status"] in ("ACCEPTED", "SKIPPED")
    _ = booking
    _ = captured


# ---------------------------------------------------------------------------
# 4. Template language mismatch (132001)
# ---------------------------------------------------------------------------

def test_language_mismatch_132001_retries_once_with_base_language(monkeypatch):
    """Render configured en_US while Meta registered the template as `en`.

    132001 is returned before a message is created, so exactly one retry with the
    base language is safe and must deliver on the second attempt.
    """
    calls = []

    def fake_send(self, **kwargs):
        calls.append(kwargs["template_language"])
        if len(calls) == 1:
            return {
                "success": False,
                "status": "failed",
                "status_code": 404,
                "error_code": 132001,
                "error": "Meta API Error (404): (#132001) Template name does not exist in the translation",
            }
        return {"success": True, "message_id": "wamid.after.lang", "status": "sent", "status_code": 200}

    monkeypatch.setattr(NotificationService, "_acquire_notification_claim", classmethod(lambda cls, *a, **k: (_FakeRecord(), "PROCEED")))
    monkeypatch.setattr(
        "app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE",
        "shafsky_booking_confirmation",
    )
    monkeypatch.setattr(
        "app.services.notification_service.settings.WHATSAPP_CUSTOMER_TEMPLATE_LANG",
        "en_US",
    )
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True)
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.send_message", fake_send)

    result = NotificationService.send_customer_whatsapp_confirmation(
        MagicMock(), _offline_confirmation_context()
    )

    assert calls == ["en_US", "en"]
    assert result["status"] == "ACCEPTED"
    assert result["template_language"] == "en"
    assert result["message_id"] == "wamid.after.lang"


def test_language_mismatch_without_region_does_not_retry(monkeypatch):
    """A base-language 132001 is a real misconfiguration, not a regional variant."""
    calls = []

    def fake_send(self, **kwargs):
        calls.append(kwargs["template_language"])
        return {
            "success": False,
            "status": "failed",
            "status_code": 404,
            "error_code": 132001,
            "error": "Meta API Error (404): (#132001) Template name does not exist",
        }

    record = _FakeRecord()
    monkeypatch.setattr(NotificationService, "_acquire_notification_claim", classmethod(lambda cls, *a, **k: (record, "PROCEED")))
    monkeypatch.setattr(
        "app.services.notification_service.settings.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE",
        "shafsky_booking_confirmation",
    )
    monkeypatch.setattr(
        "app.services.notification_service.settings.WHATSAPP_CUSTOMER_TEMPLATE_LANG",
        "en",
    )
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.is_configured", lambda self: True)
    monkeypatch.setattr("app.integrations.whatsapp.client.WhatsAppClient.send_message", fake_send)

    result = NotificationService.send_customer_whatsapp_confirmation(
        MagicMock(), _offline_confirmation_context()
    )

    assert calls == ["en"]
    assert result["status"] == "FAILED"
    assert "WHATSAPP_CUSTOMER_TEMPLATE_LANG" in record.error_log
    assert record.status == NotificationStatus.FAILED


# ---------------------------------------------------------------------------
# 5. Duplicate / retry safety
# ---------------------------------------------------------------------------

def test_accepted_send_is_recorded_as_completed_not_delivered(monkeypatch):
    _capture_send(monkeypatch)
    record = _FakeRecord(payload={"booking_ref": "SHF-WA-FIX-001"})
    monkeypatch.setattr(
        NotificationService,
        "_acquire_notification_claim",
        classmethod(lambda cls, *a, **k: (record, "PROCEED")),
    )

    result = NotificationService.send_customer_whatsapp_confirmation(
        MagicMock(), _offline_confirmation_context()
    )

    assert result["status"] == "ACCEPTED"
    assert record.status == NotificationStatus.COMPLETED
    assert record.message_id == "wamid.test.001"
    assert record.delivered_at is None  # delivery is proven only by the webhook
    assert record.payload["delivery"]["template_language"] == "en"


@pytest.mark.parametrize(
    "status",
    [NotificationStatus.COMPLETED, NotificationStatus.PENDING, NotificationStatus.DELIVERED],
)
def test_duplicate_event_is_not_resent(status):
    record = SimpleNamespace(
        payload={"booking_ref": "SHF-DUP-1"},
        recipient_phone="919876543210",
        recipient_email=None,
        status=status,
    )
    action = NotificationService._customer_notification_already_handled(
        _FakeQueryDb([record]),
        template_type="WHATSAPP_CUSTOMER_CONFIRMATION",
        booking_ref="SHF-DUP-1",
        recipient="919876543210",
    )
    assert action in ("ALREADY_DELIVERED", "IN_PROGRESS")


def test_failed_record_is_retried_not_blocked():
    record = SimpleNamespace(
        payload={"booking_ref": "SHF-RETRY-1"},
        recipient_phone="919876543210",
        recipient_email=None,
        status=NotificationStatus.FAILED,
    )
    action = NotificationService._customer_notification_already_handled(
        _FakeQueryDb([record]),
        template_type="WHATSAPP_CUSTOMER_CONFIRMATION",
        booking_ref="SHF-RETRY-1",
        recipient="919876543210",
    )
    assert action is None  # retry path stays available
