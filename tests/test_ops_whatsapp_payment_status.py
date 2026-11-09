"""
Regression tests: the operations WhatsApp alert must reflect the verified payment status.

Bug: the creation-time alert (status PENDING) is the only ops alert ever sent. Its
booking-level idempotency key blocked any later send and the payment-confirmation path
never dispatched an ops alert, so paid bookings stayed PENDING on WhatsApp.
"""

import uuid
from unittest.mock import MagicMock

import pytest

from app.database import SessionLocal
from app.integrations.whatsapp.client import WhatsAppClient
from app.models.schema import Booking, BookingStatus
from app.services.notification_service import NotificationService
from app.services.whatsapp_department_service import WhatsAppDepartmentService


@pytest.fixture()
def ops_sends(monkeypatch):
    calls = []
    monkeypatch.setattr(WhatsAppDepartmentService, "_resolve_template", classmethod(lambda cls: ("", "")))
    monkeypatch.setattr(WhatsAppClient, "is_configured", lambda self: True)

    def fake_text(self, to_phone, message_body):
        calls.append({"to_phone": to_phone, "message_body": message_body})
        return {"success": True, "message_id": f"wamid.{uuid.uuid4().hex[:10]}"}

    monkeypatch.setattr(WhatsAppClient, "send_text_message", fake_text)
    # Customer-facing and e-mail side effects are out of scope for these tests.
    monkeypatch.setattr(
        NotificationService, "send_customer_whatsapp_confirmation", classmethod(lambda cls, db, ctx: {"status": "SKIPPED"})
    )
    monkeypatch.setattr(NotificationService, "_record_and_send", classmethod(lambda cls, db, **kw: {"status": "SKIPPED"}))
    WhatsAppDepartmentService.clear_idempotency_cache()
    return calls


def _context(ref):
    return {
        "booking_ref": ref,
        "passenger_name": "Paid Check",
        "passenger_email": "paid@example.test",
        "passenger_phone": "9876543210",
        "passenger_count": 2,
        "service_category": "Airport Assistance",
        "service_type": "Meet & Greet",
        "origin_code": "DEL",
        "dest_code": "DEL",
        "airport_code": "DEL",
        "departure_time": "2026-11-15T14:30:00",
        "total_amount": 5500.0,
        "currency": "INR",
        "status": "PENDING",
    }


def _make_booking(ref, status, paid):
    with SessionLocal() as db:
        db.add(
            Booking(
                booking_ref=ref,
                passenger_name="Paid Check",
                passenger_email="paid@example.test",
                passenger_phone="9876543210",
                service_type="Meet & Greet",
                status=status,
                total_amount=5500,
                currency="INR",
                metadata_json={"payment_status": "PAID"} if paid else {},
            )
        )
        db.commit()


def _statuses(calls):
    return {"CONFIRMED" if "CONFIRMED" in c["message_body"] else "PENDING" for c in calls}


def test_unpaid_booking_creation_alert_stays_pending(ops_sends):
    ref = f"SHF-UNPAID-{uuid.uuid4().hex[:6].upper()}"
    _make_booking(ref, BookingStatus.PENDING, paid=False)

    NotificationService.notify_booking_created(MagicMock(), _context(ref))

    assert len(ops_sends) == 5
    assert all("PENDING" in c["message_body"] for c in ops_sends)
    assert not any("CONFIRMED" in c["message_body"] for c in ops_sends)


def test_unpaid_booking_confirmation_hook_sends_no_ops_alert(ops_sends):
    ref = f"SHF-UNPAID2-{uuid.uuid4().hex[:6].upper()}"
    _make_booking(ref, BookingStatus.PENDING, paid=False)

    with SessionLocal() as db:
        res = NotificationService.notify_operations_payment_confirmed(db, {**_context(ref), "status": "CONFIRMED"})

    assert res is None  # frontend/context claiming CONFIRMED is not trusted
    assert ops_sends == []


def test_paid_booking_sends_confirmed_ops_alert_after_pending(ops_sends):
    ref = f"SHF-PAID-{uuid.uuid4().hex[:6].upper()}"
    _make_booking(ref, BookingStatus.PENDING, paid=False)

    NotificationService.notify_booking_created(MagicMock(), _context(ref))
    assert len(ops_sends) == 5
    assert _statuses(ops_sends) == {"PENDING"}

    # Payment verified: DB now shows CONFIRMED + PAID; the caller context carries no status.
    with SessionLocal() as db:
        booking = db.query(Booking).filter(Booking.booking_ref == ref).one()
        booking.status = BookingStatus.CONFIRMED
        booking.metadata_json = {"payment_status": "PAID"}
        db.commit()
        context = {k: v for k, v in _context(ref).items() if k != "status"}
        NotificationService.notify_booking_confirmed(db, context)

    confirmed = ops_sends[5:]
    assert len(confirmed) == 5
    assert all("CONFIRMED" in c["message_body"] for c in confirmed)
    assert not any("PENDING" in c["message_body"] for c in confirmed)
    assert [c["to_phone"] for c in confirmed] == [c["to_phone"] for c in ops_sends[:5]]


def test_paid_booking_confirmed_alert_is_not_duplicated(ops_sends):
    ref = f"SHF-PAID2-{uuid.uuid4().hex[:6].upper()}"
    _make_booking(ref, BookingStatus.CONFIRMED, paid=True)

    with SessionLocal() as db:
        first = NotificationService.notify_operations_payment_confirmed(db, _context(ref))
        second = NotificationService.notify_operations_payment_confirmed(db, _context(ref))
        # Webhook retry / PDF fallback re-invokes the full confirmation hook.
        NotificationService.notify_booking_confirmed(db, _context(ref))

    assert first["status"] == "ACCEPTED"
    assert second["status"] == "SKIPPED"
    assert len(ops_sends) == 5

