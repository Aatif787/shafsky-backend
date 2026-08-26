"""Hardening tests: reserved email blocked at booking create; PDF backfill without notifications."""

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.utils.customer_email import is_acceptable_customer_email


def test_shared_email_helper_rejects_reserved():
    ok, reason = is_acceptable_customer_email("guest@example.com")
    assert ok is False
    assert reason == "reserved_or_placeholder"


def test_booking_service_rejects_reserved_email(monkeypatch):
    from app.services.booking_service import BookingService
    from app.schemas.booking import BookingCreate
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        payload = BookingCreate(
            passenger_name="Test User",
            passenger_email="guest@example.com",
            passenger_phone="+919999999999",
            service_type="Silver",
            service_category="Airport Services",
            origin_code="DEL",
            dest_code="BOM",
            flight_num="AI101",
            currency="INR",
            totalAmount=5000.0,
            metadata_json={
                "journey_type": "DEPARTURE",
                "flight_type": "DOMESTIC",
                "service_airport": "DEL",
                "package": "silver",
            },
        )
        with pytest.raises(HTTPException) as exc:
            BookingService.create_booking(db, payload)
        assert exc.value.status_code == 422
    finally:
        db.close()


def test_fulfill_skip_notifications_sets_pdf_without_notify(monkeypatch):
    from app.database import SessionLocal
    from sqlalchemy import text
    from app.services import pdf_service

    monkeypatch.setattr(pdf_service, "upload_invoice_pdf", lambda path, pdf_bytes: True)
    monkeypatch.setattr(pdf_service, "create_signed_invoice_url", lambda path: f"https://signed.example/{path}")
    monkeypatch.setattr(
        pdf_service,
        "generate_invoice_pdf_bytes",
        lambda *a, **k: b"%PDF-1.4 test",
    )

    notify = MagicMock()
    wa = MagicMock()
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify_booking_confirmed",
        notify,
    )
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.send_whatsapp_invoice_document",
        wa,
    )

    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT cast(i.id as text) AS id
                FROM invoices i
                WHERE cast(i.status as text)='PAID' AND i.pdf_url IS NULL
                ORDER BY coalesce(i.paid_at, i.issued_at) DESC NULLS LAST
                LIMIT 1
                """
            )
        ).mappings().first()
        if not row:
            pytest.skip("no PAID invoice with null pdf_url")
        result = pdf_service.fulfill_paid_invoice(db, row["id"], send_notifications=False)
        assert result.get("success") is True
        assert result.get("pdf_url")
        assert result.get("notifications_sent") is False
        notify.assert_not_called()
        wa.assert_not_called()
        # cleanup: leave pdf_url set (good); do not reverse payment
    finally:
        db.close()
