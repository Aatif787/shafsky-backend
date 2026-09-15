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
    from datetime import datetime, timezone
    from app.database import SessionLocal
    from app.models.payment import Invoice, InvoiceStatus, PaymentMethod, PaymentStatus, PaymentTransaction
    from app.models.schema import Booking, BookingStatus
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
        ref = f"SHF-HARDEN-{uuid.uuid4().hex[:8].upper()}"
        booking = Booking(
            id=uuid.uuid4(),
            booking_ref=ref,
            passenger_name="Harden Test",
            passenger_email="harden@shafsky.com",
            passenger_phone="919999999999",
            service_category="Airport Assistance",
            service_type="Elite Service",
            origin_code="DEL",
            dest_code="BOM",
            total_amount=5000.0,
            currency="INR",
            status=BookingStatus.PENDING,
        )
        db.add(booking)
        tx = PaymentTransaction(
            id=uuid.uuid4(),
            transaction_ref=f"PAY-{uuid.uuid4().hex[:8].upper()}",
            entity_type="AIRPORT_BOOKING",
            entity_id=ref,
            amount=5000.0,
            currency="INR",
            payment_method=PaymentMethod.CREDIT_CARD,
            status=PaymentStatus.SUCCESSFUL,
            gateway_provider="RAZORPAY",
        )
        db.add(tx)
        db.flush()
        invoice = Invoice(
            id=uuid.uuid4(),
            invoice_number=f"INV-HARDEN-{uuid.uuid4().hex[:6].upper()}",
            transaction_id=tx.id,
            customer_name="Harden Test",
            customer_email="harden@shafsky.com",
            subtotal_amount=5000.0,
            tax_amount=0.0,
            total_amount=5000.0,
            currency="INR",
            status=InvoiceStatus.PAID,
            paid_at=datetime.now(timezone.utc),
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        result = pdf_service.fulfill_paid_invoice(db, str(invoice.id), send_notifications=False)
        assert result.get("success") is True
        assert result.get("pdf_url")
        assert result.get("notifications_sent") is False
        notify.assert_not_called()
        wa.assert_not_called()
    finally:
        db.close()
