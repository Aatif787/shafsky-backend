"""Booking confirmation notification dispatch (Resend)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.notification_service import NotificationService
from app.services.notification_templates import NotificationTemplateEngine


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {"id": "re_msg_test"}
        self.text = text or '{"id":"re_msg_test"}'

    def json(self):
        return self._payload


def test_customer_and_admin_templates_include_booking_fields():
    payload = {
        "booking_ref": "SHF-SLV-ABC123",
        "passengerName": "Ada Lovelace",
        "passengerEmail": "ada@example.com",
        "passengerPhone": "+910000000000",
        "airportCode": "DEL",
        "journeyType": "ARRIVAL",
        "service_name": "gold",
        "flightNum": "AI101",
        "originCode": "LHR",
        "destCode": "DEL",
        "departureTime": "2026-08-21T10:00:00+00:00",
        "totalAmount": 15000,
        "currency": "INR",
        "status": "PENDING",
    }
    customer = NotificationTemplateEngine.render_template("BOOKING_CONFIRMATION", payload)
    admin = NotificationTemplateEngine.render_template("ADMIN_NEW_BOOKING", payload)
    assert "SHF-SLV-ABC123" in customer["subject"]
    assert "DEL" in customer["html"]
    assert "gold" in customer["html"]
    assert "AI101" in customer["html"]
    assert "Ada Lovelace" in admin["html"]
    assert "ada@example.com" in admin["html"]


def test_resend_accepts_message_id(monkeypatch):
    monkeypatch.setattr(
        "app.services.notification_service.settings",
        SimpleNamespace(
            RESEND_API_KEY="re_test_placeholder",
            EMAIL_FROM="bookings@example.com",
            RESEND_FROM_EMAIL="",
            EMAIL_REPLY_TO="",
        ),
    )

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json, headers):
            assert url == "https://api.resend.com/emails"
            assert "Authorization" in headers
            assert json["to"] == ["guest@example.com"]
            return _FakeResponse()

    with patch("app.services.notification_service.httpx.Client", return_value=_Client()):
        result = NotificationService.send_email_resend_sync(
            "guest@example.com",
            "Subject",
            "<p>Hello</p>",
        )
    assert result["status"] == "DELIVERED"
    assert result["message_id"] == "re_msg_test"


def test_resend_unconfigured_is_bypassed(monkeypatch):
    monkeypatch.setattr(
        "app.services.notification_service.settings",
        SimpleNamespace(RESEND_API_KEY="", EMAIL_FROM="", RESEND_FROM_EMAIL="", EMAIL_REPLY_TO=""),
    )
    result = NotificationService.send_email_resend_sync("guest@example.com", "S", "<p>x</p>")
    assert result["status"] == "BYPASSED"


def test_notify_booking_created_does_not_raise_when_provider_fails(monkeypatch):
    monkeypatch.setattr(
        NotificationService,
        "admin_notification_recipients",
        classmethod(lambda cls: ["ops@example.com"]),
    )
    monkeypatch.setattr(
        NotificationService,
        "send_email_resend_sync",
        classmethod(lambda cls, *a, **k: {"status": "FAILED", "error": "provider_rejected:401"}),
    )
    monkeypatch.setattr(NotificationService, "_already_notified", classmethod(lambda *a, **k: False))

    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    summary = NotificationService.notify_booking_created(
        db,
        {
            "booking_ref": "SHF-TEST-1",
            "passenger_email": "guest@example.com",
            "passenger_name": "Guest",
            "service_type": "gold",
        },
    )
    assert summary["customer"]["status"] == "FAILED"
    assert summary["admin"][0]["status"] == "FAILED"
    assert db.commit.called


def test_duplicate_notification_is_skipped(monkeypatch):
    monkeypatch.setattr(NotificationService, "_already_notified", classmethod(lambda *a, **k: True))
    db = MagicMock()
    result = NotificationService._record_and_send(
        db,
        recipient_email="guest@example.com",
        template_type="BOOKING_CONFIRMATION",
        payload={"booking_ref": "SHF-TEST-1"},
        booking_ref="SHF-TEST-1",
    )
    assert result == {"status": "SKIPPED", "reason": "duplicate"}
    assert not db.add.called
