"""
Authoritative Tax Invoice PDF flow after successful Razorpay payment.

PDF/storage/email failures must never reverse payment confirmation.
Webhook replay must not duplicate invoice rows, invoice numbers, or emails.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import Base, engine, get_db
from app.main import app
from app.models.payment import Invoice, InvoiceStatus, PaymentStatus, PaymentTransaction
from app.models.schema import Booking, BookingStatus, NotificationRecord, NotificationStatus
from app.models.whatsapp_models import WhatsAppConversation
from app.providers.razorpay_provider import razorpay_provider
from app.services.notification_templates import NotificationTemplateEngine
from app.services.payment_service import PaymentService
from app.services.pdf_service import (
    STORAGE_BUCKET,
    build_invoice_storage_path,
    generate_invoice_pdf_bytes,
    generate_tax_invoice_pdf,
    invoice_pdf_data_from_records,
)

client = TestClient(app)

AMOUNT = 12500.0
AMOUNT_PAISE = 1250000


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def setup_webhook_secret(monkeypatch):
    test_secret = "shafsky_test_wh_secret"
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", test_secret)
    razorpay_provider.webhook_secret = test_secret
    yield


def _pdf_text(pdf_bytes: bytes) -> str:
    return pdf_bytes.decode("latin-1", errors="ignore")


def generate_webhook_signature(body_bytes: bytes, secret: str = "shafsky_test_wh_secret") -> str:
    return hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


def create_paid_ready_booking(db_session, *, amount: float = AMOUNT):
    booking_ref = f"SHF-INV-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking = Booking(
        id=uuid.uuid4(),
        booking_ref=booking_ref,
        passenger_name="Arthur Dent",
        passenger_email="arthur@galaxy.com",
        passenger_phone="+919876543210",
        service_category="Airport Assistance",
        service_type="Meet & Assist",
        origin_code="DEL",
        dest_code="DXB",
        flight_num="AI101",
        departure_time=datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc),
        total_amount=amount,
        currency="INR",
        status=BookingStatus.PENDING,
        metadata_json={
            "service_airport": "DEL",
            "journey_type": "DEPARTURE",
            "flight_type": "INTERNATIONAL",
            "package": "Meet & Assist",
            "terminal": "T3",
        },
    )
    db_session.add(booking)
    tx = PaymentTransaction(
        id=uuid.uuid4(),
        transaction_ref=booking_ref,
        entity_type="AIRPORT_BOOKING",
        entity_id=booking_ref,
        amount=amount,
        currency="INR",
        gateway_provider="RAZORPAY",
        gateway_payment_id=order_id,
        status=PaymentStatus.PENDING,
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(booking)
    db_session.refresh(tx)
    return booking, tx, order_id, payment_id


def post_payment_captured(booking_ref: str, order_id: str, payment_id: str, event_id: str | None = None):
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": AMOUNT_PAISE,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"booking_ref": booking_ref, "channel": "web"},
                }
            }
        },
    }
    if event_id:
        payload["event_id"] = event_id
    body_bytes = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Razorpay-Signature": generate_webhook_signature(body_bytes),
        "Content-Type": "application/json",
    }
    if event_id:
        headers["X-Razorpay-Event-Id"] = event_id
    return client.post("/api/payments/razorpay/webhook", content=body_bytes, headers=headers), payload


def confirm_via_service(db, booking, tx, order_id, payment_id):
    return PaymentService.handle_verified_payment(
        db,
        event_name="payment.captured",
        gateway_provider="RAZORPAY",
        order_id=order_id,
        payment_id=payment_id,
        booking_ref=booking.booking_ref,
        raw_payload={
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order_id,
                        "amount": AMOUNT_PAISE,
                        "currency": "INR",
                    }
                }
            }
        },
    )


def test_pdf_library_is_reportlab():
    from reportlab.pdfgen import canvas  # noqa: F401

    pdf = generate_tax_invoice_pdf(
        {
            "invoice_number": "INV-TEST-000001",
            "invoice_date": "26 Aug 2026",
            "booking_ref": "SHF-INV-UNIT",
            "customer_name": "Unit Guest",
            "customer_email": "unit@example.com",
            "customer_phone": "+910000000000",
            "origin": "DEL",
            "destination": "DXB",
            "airport": "DEL",
            "journey_type": "DEPARTURE",
            "flight_type": "INTERNATIONAL",
            "flight_num": "AI101",
            "travel_date": "26 Aug 2026",
            "service_name": "Meet & Assist",
            "subtotal_amount": 10593.22,
            "tax_amount": 1906.78,
            "total_amount": 12500.00,
            "currency": "INR",
            "razorpay_payment_id": "pay_unit",
            "razorpay_order_id": "order_unit",
            "payment_status": "PAID",
        }
    )
    assert pdf.startswith(b"%PDF")
    text = _pdf_text(pdf)
    assert "TAX INVOICE" in text
    assert "SHAFSKY AVIATION SERVICES" in text
    assert "INV-TEST-000001" in text
    assert "ops@shafskyaviation.com" not in text
    assert "26 Aug 2026" in text


def test_successful_payment_creates_paid_invoice_row():
    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    res = confirm_via_service(db, booking, tx, order_id, payment_id)
    assert res["status"] == "CONFIRMED"

    db.expire_all()
    db.refresh(booking)
    db.refresh(tx)
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert inv is not None
    assert inv.status == InvoiceStatus.PAID
    assert inv.invoice_number.startswith("INV-")
    assert inv.total_amount == AMOUNT
    assert booking.status == BookingStatus.CONFIRMED
    assert tx.status == PaymentStatus.SUCCESSFUL


def test_successful_payment_generates_pdf_bytes_with_required_fields():
    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    confirm_via_service(db, booking, tx, order_id, payment_id)
    db.expire_all()
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    db.refresh(tx)
    db.refresh(booking)

    pdf_bytes = generate_invoice_pdf_bytes(inv, booking, tx)
    assert pdf_bytes.startswith(b"%PDF")
    text = _pdf_text(pdf_bytes)

    assert inv.invoice_number in text
    assert booking.booking_ref in text
    assert "Arthur Dent" in text
    assert "arthur@galaxy.com" in text
    assert "+919876543210" in text
    assert "Meet & Assist" in text
    assert "Subtotal" in text
    assert "GST" in text
    assert "CGST" in text
    assert f"{inv.subtotal_amount:,.2f}" in text
    assert f"{inv.tax_amount:,.2f}" in text
    assert f"{inv.total_amount:,.2f}" in text
    assert payment_id in text
    assert order_id in text
    assert "PAID" in text
    assert "TAX INVOICE" in text


def test_invoice_pdf_data_uses_backend_totals_not_frontend():
    invoice = SimpleNamespace(
        invoice_number="INV-AUTH-1",
        customer_name="Backend Customer",
        customer_email="backend@example.com",
        subtotal_amount=8474.58,
        tax_amount=1525.42,
        total_amount=10000.0,
        currency="INR",
        issued_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
        paid_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
    )
    booking = SimpleNamespace(
        booking_ref="SHF-AUTH-1",
        passenger_name="Backend Customer",
        passenger_email="backend@example.com",
        passenger_phone="+911111111111",
        origin_code="BOM",
        dest_code="LHR",
        flight_num="BA142",
        service_type="gold",
        departure_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        metadata_json={"package": "gold", "service_airport": "BOM", "journey_type": "ARRIVAL"},
        total_amount=10000.0,
        currency="INR",
    )
    transaction = SimpleNamespace(
        gateway_payment_id="pay_auth_1",
        gateway_response={"payload": {"payment": {"entity": {"id": "pay_auth_1", "order_id": "order_auth_1"}}}},
        updated_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
        entity_id="SHF-AUTH-1",
    )
    data = invoice_pdf_data_from_records(invoice, booking, transaction)
    assert data["total_amount"] == 10000.0
    assert data["service_name"] == "gold"
    assert data["razorpay_payment_id"] == "pay_auth_1"
    assert data["razorpay_order_id"] == "order_auth_1"


def test_pdf_uploads_and_sets_invoice_pdf_url(monkeypatch):
    uploaded = {}

    def fake_upload(path, pdf_bytes):
        uploaded["path"] = path
        uploaded["bytes"] = pdf_bytes
        return True

    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", fake_upload)
    monkeypatch.setattr(
        "app.services.pdf_service.create_signed_invoice_url",
        lambda path: f"https://storage.example/signed/{path}",
    )

    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    confirm_via_service(db, booking, tx, order_id, payment_id)

    db.expire_all()
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    expected = build_invoice_storage_path(booking.booking_ref, inv.invoice_number)
    assert inv.pdf_url == expected
    assert uploaded["path"] == expected
    assert uploaded["bytes"].startswith(b"%PDF")
    assert STORAGE_BUCKET == "booking-docs"
    assert inv.pdf_url.startswith("invoices/")
    assert not str(inv.pdf_url).startswith("http")


def test_email_attaches_invoice_pdf(monkeypatch):
    captured = {}

    def fake_upload(path, pdf_bytes):
        return True

    def fake_send(cls, recipient_email, subject, html_content, attachments=None):
        captured["to"] = recipient_email
        captured["html"] = html_content
        captured["attachments"] = attachments
        return {"status": "DELIVERED", "message_id": "re_invoice_test"}

    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", fake_upload)
    monkeypatch.setattr(
        "app.services.pdf_service.create_signed_invoice_url",
        lambda path: "https://storage.example/invoice.pdf",
    )
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.send_email_resend_sync",
        classmethod(fake_send),
    )

    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    confirm_via_service(db, booking, tx, order_id, payment_id)

    db.expire_all()
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert captured["to"] == "arthur@galaxy.com"
    assert captured["attachments"]
    att = captured["attachments"][0]
    assert str(att["filename"]).endswith(".pdf")
    assert inv.invoice_number in att["filename"]
    assert att["content"].startswith(b"%PDF")
    assert "invoice attached" in captured["html"].lower() or "tax invoice" in captured["html"].lower()


def test_duplicate_webhook_does_not_create_second_invoice_or_pdf(monkeypatch):
    upload_calls = []

    def fake_upload(path, pdf_bytes):
        upload_calls.append(path)
        return True

    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", fake_upload)
    monkeypatch.setattr(
        "app.services.pdf_service.create_signed_invoice_url",
        lambda path: f"https://storage.example/{path}",
    )

    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    res1, _ = post_payment_captured(booking.booking_ref, order_id, payment_id, event_id=event_id)
    res2, _ = post_payment_captured(booking.booking_ref, order_id, payment_id, event_id=event_id)
    assert res1.status_code == 200
    assert res2.status_code == 200
    assert res2.json()["data"]["status"] == "DUPLICATE_IGNORED"

    db.expire_all()
    invoices = list(db.scalars(select(Invoice).where(Invoice.transaction_id == tx.id)).all())
    assert len(invoices) == 1
    assert len(set(inv.invoice_number for inv in invoices)) == 1
    assert len(upload_calls) == 1


def test_pdf_failure_does_not_undo_successful_payment(monkeypatch):
    monkeypatch.setattr(
        "app.services.pdf_service.generate_invoice_pdf_bytes",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pdf exploded")),
    )
    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    res = confirm_via_service(db, booking, tx, order_id, payment_id)
    assert res["status"] == "CONFIRMED"
    db.expire_all()
    db.refresh(booking)
    db.refresh(tx)
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert booking.status == BookingStatus.CONFIRMED
    assert tx.status == PaymentStatus.SUCCESSFUL
    assert inv is not None
    assert inv.status == InvoiceStatus.PAID
    assert inv.pdf_url is None


def test_storage_failure_does_not_undo_successful_payment(monkeypatch):
    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: False)
    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    res = confirm_via_service(db, booking, tx, order_id, payment_id)
    assert res["status"] == "CONFIRMED"
    db.expire_all()
    db.refresh(booking)
    db.refresh(tx)
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert booking.status == BookingStatus.CONFIRMED
    assert tx.status == PaymentStatus.SUCCESSFUL
    assert inv is not None
    assert inv.pdf_url is None


def test_email_failure_does_not_undo_successful_payment(monkeypatch):
    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify_booking_confirmed",
        classmethod(lambda cls, *a, **k: (_ for _ in ()).throw(RuntimeError("resend down"))),
    )
    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    res = confirm_via_service(db, booking, tx, order_id, payment_id)
    assert res["status"] == "CONFIRMED"
    db.expire_all()
    db.refresh(booking)
    db.refresh(tx)
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert booking.status == BookingStatus.CONFIRMED
    assert tx.status == PaymentStatus.SUCCESSFUL
    assert inv is not None
    assert inv.pdf_url == build_invoice_storage_path(booking.booking_ref, inv.invoice_number)


def test_confirmation_template_invoice_link_only_when_https():
    with_link = NotificationTemplateEngine.render_template(
        "BOOKING_CONFIRMATION",
        {
            "booking_ref": "SHF-INV-LINK",
            "passengerName": "Ada",
            "invoice_url": "https://storage.example/invoices/SHF-INV-LINK/INV-1.pdf",
            "invoice_attached": False,
        },
    )
    assert "Your tax invoice is available" in with_link["html"]
    assert "https://storage.example/invoices/SHF-INV-LINK/INV-1.pdf" in with_link["whatsapp_text"]

    attached = NotificationTemplateEngine.render_template(
        "BOOKING_CONFIRMATION",
        {
            "booking_ref": "SHF-INV-ATT",
            "passengerName": "Ada",
            "invoice_number": "INV-ATT-1",
            "invoice_attached": True,
        },
    )
    assert "attached to this email" in attached["html"]
    assert "Your tax invoice is available" not in attached["whatsapp_text"]

    broken = NotificationTemplateEngine.render_template(
        "BOOKING_CONFIRMATION",
        {
            "booking_ref": "SHF-INV-BAD",
            "passengerName": "Ada",
            "invoice_url": "not-a-url",
            "invoice_attached": False,
        },
    )
    assert "Your tax invoice is available" not in broken["html"]
    assert "not-a-url" not in broken["whatsapp_text"]


def test_email_does_not_claim_attachment_when_pdf_failed(monkeypatch):
    captured = {}

    def fake_send(cls, recipient_email, subject, html_content, attachments=None):
        captured["html"] = html_content
        captured["attachments"] = attachments
        return {"status": "DELIVERED", "message_id": "re_no_pdf"}

    monkeypatch.setattr(
        "app.services.pdf_service.generate_invoice_pdf_bytes",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pdf exploded")),
    )
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.send_email_resend_sync",
        classmethod(fake_send),
    )

    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    confirm_via_service(db, booking, tx, order_id, payment_id)
    assert captured.get("attachments") in (None, [])
    assert "attached to this email" not in (captured.get("html") or "").lower()


def test_storage_path_is_deterministic_and_collision_safe():
    path = build_invoice_storage_path("SHF-DEL-ABC123", "INV-20260826-ABCDEF")
    assert path == "invoices/SHF-DEL-ABC123/INV-20260826-ABCDEF.pdf"
    dirty = build_invoice_storage_path("../etc/passwd", "INV-1")
    assert ".." not in dirty
    assert dirty.startswith("invoices/")


def _add_whatsapp_conversation(db_session, booking_ref: str, phone: str | None = None):
    unique_phone = phone or f"91{uuid.uuid4().int % 10000000000:010d}"
    conv = WhatsAppConversation(
        id=uuid.uuid4(),
        phone_number=unique_phone,
        customer_name="WhatsApp Traveller",
        customer_phone=unique_phone,
        current_state="WAITING_PAYMENT",
        booking_ref=booking_ref,
        selected_service_name="Meet & Assist",
        payment_status="PENDING",
    )
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)
    return conv


def test_whatsapp_origin_sends_invoice_pdf_document(monkeypatch):
    calls = []

    def fake_send_doc(self, to_phone, *, filename, caption="", document_url=None, pdf_bytes=None):
        calls.append({
            "to": to_phone,
            "filename": filename,
            "caption": caption,
            "has_bytes": bool(pdf_bytes),
            "type": "document",
        })
        return {"success": True, "message_id": "wamid.invoice_doc_1", "status": "sent"}

    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_document_message",
        fake_send_doc,
    )
    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: True)

    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    conv = _add_whatsapp_conversation(db, booking.booking_ref)
    res = confirm_via_service(db, booking, tx, order_id, payment_id)
    assert res["status"] == "CONFIRMED"
    assert len(calls) == 1
    assert calls[0]["type"] == "document"
    assert calls[0]["to"] == conv.customer_phone
    assert calls[0]["has_bytes"] is True
    assert calls[0]["filename"].startswith("Shafsky-Aviation-Tax-Invoice-")
    assert calls[0]["filename"].endswith(".pdf")
    assert "Shafsky Aviation Tax Invoice" in calls[0]["caption"]

    db.expire_all()
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert inv.invoice_number in calls[0]["filename"]
    assert inv.invoice_number in calls[0]["caption"]
    assert booking.booking_ref in calls[0]["caption"]

    rec = db.scalar(
        select(NotificationRecord).where(
            NotificationRecord.template_type == "WHATSAPP_INVOICE_PDF",
            NotificationRecord.message_id == "wamid.invoice_doc_1",
        )
    )
    assert rec is not None
    assert rec.status == NotificationStatus.DELIVERED


def test_website_origin_sends_whatsapp_invoice_pdf_to_submitted_phone(monkeypatch):
    calls = []

    def fake_send_doc(self, to_phone, *, filename, caption="", document_url=None, pdf_bytes=None):
        calls.append({
            "to": to_phone,
            "filename": filename,
            "caption": caption,
            "has_bytes": bool(pdf_bytes),
        })
        return {"success": True, "message_id": "wamid.web_invoice_doc_1", "status": "sent"}

    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_document_message",
        fake_send_doc,
    )
    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: True)
    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    # Website booking with a submitted phone number must receive the WhatsApp invoice PDF document.
    res = confirm_via_service(db, booking, tx, order_id, payment_id)
    assert res["status"] == "CONFIRMED"
    assert len(calls) == 1
    assert calls[0]["to"] == "919876543210"
    assert calls[0]["has_bytes"] is True
    assert calls[0]["filename"].endswith(".pdf")
    assert booking.booking_ref in calls[0]["caption"]


def test_website_origin_without_phone_skips_whatsapp_invoice_pdf(monkeypatch):
    calls = []

    def fake_send_doc(self, *a, **k):
        calls.append(k)
        return {"success": True, "message_id": "wamid.should_not_send", "status": "sent"}

    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_document_message",
        fake_send_doc,
    )
    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: True)
    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    booking.passenger_phone = ""
    db.commit()
    confirm_via_service(db, booking, tx, order_id, payment_id)
    assert calls == []


def test_duplicate_webhook_does_not_resend_whatsapp_invoice_pdf(monkeypatch):
    calls = []

    def fake_send_doc(self, to_phone, *, filename, caption="", document_url=None, pdf_bytes=None):
        calls.append(filename)
        return {"success": True, "message_id": f"wamid.{len(calls)}", "status": "sent"}

    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_document_message",
        fake_send_doc,
    )
    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: True)

    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    _add_whatsapp_conversation(db, booking.booking_ref)
    confirm_via_service(db, booking, tx, order_id, payment_id)
    confirm_via_service(db, booking, tx, order_id, payment_id)
    assert len(calls) == 1

    invoices = list(db.scalars(select(Invoice).where(Invoice.transaction_id == tx.id)).all())
    assert len(invoices) == 1


def test_whatsapp_document_failure_does_not_undo_payment(monkeypatch):
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_document_message",
        lambda *a, **k: {"success": False, "error": "meta_rejected", "status": "failed"},
    )
    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: True)
    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    _add_whatsapp_conversation(db, booking.booking_ref)
    res = confirm_via_service(db, booking, tx, order_id, payment_id)
    assert res["status"] == "CONFIRMED"
    db.expire_all()
    db.refresh(booking)
    db.refresh(tx)
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert booking.status == BookingStatus.CONFIRMED
    assert tx.status == PaymentStatus.SUCCESSFUL
    assert inv is not None
    recs = list(
        db.scalars(
            select(NotificationRecord).where(NotificationRecord.template_type == "WHATSAPP_INVOICE_PDF")
        ).all()
    )
    matching = [r for r in recs if (r.payload or {}).get("booking_ref") == booking.booking_ref]
    assert matching
    assert all(r.status == NotificationStatus.FAILED for r in matching)


def test_localhost_document_url_is_rejected_by_whatsapp_client():
    from app.integrations.whatsapp.client import WhatsAppClient
    assert WhatsAppClient._is_meta_fetchable_url("https://storage.example/invoices/a.pdf") is True
    assert WhatsAppClient._is_meta_fetchable_url("http://storage.example/invoices/a.pdf") is False
    assert WhatsAppClient._is_meta_fetchable_url("https://localhost/invoices/a.pdf") is False
    assert WhatsAppClient._is_meta_fetchable_url("https://127.0.0.1/invoices/a.pdf") is False


def test_full_refund_cancels_invoice_not_second_invoice():
    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    confirm_via_service(db, booking, tx, order_id, payment_id)
    res = PaymentService.handle_verified_payment(
        db,
        event_name="refund.processed",
        gateway_provider="RAZORPAY",
        order_id=order_id,
        payment_id=payment_id,
        booking_ref=booking.booking_ref,
        raw_payload={
            "payload": {
                "refund": {"entity": {"id": f"rfnd_{uuid.uuid4().hex[:8]}", "amount": AMOUNT_PAISE, "status": "processed"}},
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order_id,
                        "amount": AMOUNT_PAISE,
                        "amount_refunded": AMOUNT_PAISE,
                        "refund_status": "full",
                    }
                },
            }
        },
    )
    assert res["status"] == "REFUNDED"
    db.expire_all()
    db.refresh(booking)
    invoices = list(db.scalars(select(Invoice).where(Invoice.transaction_id == tx.id)).all())
    assert len(invoices) == 1
    assert invoices[0].status == InvoiceStatus.CANCELLED
    assert booking.status == BookingStatus.CANCELLED


def test_partial_refund_marks_invoice_partially_paid():
    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    confirm_via_service(db, booking, tx, order_id, payment_id)
    res = PaymentService.handle_verified_payment(
        db,
        event_name="refund.processed",
        gateway_provider="RAZORPAY",
        order_id=order_id,
        payment_id=payment_id,
        booking_ref=booking.booking_ref,
        raw_payload={
            "payload": {
                "refund": {"entity": {"id": f"rfnd_{uuid.uuid4().hex[:8]}", "amount": 250000, "status": "processed"}},
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order_id,
                        "amount": AMOUNT_PAISE,
                        "amount_refunded": 250000,
                        "refund_status": "partial",
                    }
                },
            }
        },
    )
    assert res["status"] == "PARTIALLY_REFUNDED"
    db.expire_all()
    db.refresh(booking)
    invoices = list(db.scalars(select(Invoice).where(Invoice.transaction_id == tx.id)).all())
    assert len(invoices) == 1
    assert invoices[0].status == InvoiceStatus.PARTIALLY_PAID
    assert booking.status == BookingStatus.CONFIRMED


def test_website_paid_booking_resolves_email_from_metadata_when_placeholder_and_delivers_all(monkeypatch):
    """
    Website booking where passenger_email on Booking was a placeholder ('guest@shafsky.com')
    while the customer's submitted email was stored in metadata_json.passengers[0].email:
    - Generates invoice with customer's real email
    - Sends BOOKING_CONFIRMATION email with PDF attachment to customer's real email
    - Sends WHATSAPP_INVOICE_PDF document to customer's submitted phone number
    """
    email_calls = []
    wa_doc_calls = []

    def fake_send_email(cls, recipient_email, subject, html_content, attachments=None):
        email_calls.append({
            "to": recipient_email,
            "subject": subject,
            "html": html_content,
            "attachments": attachments,
        })
        return {"status": "DELIVERED", "message_id": "re_web_paid_1"}

    def fake_send_doc(self, to_phone, *, filename, caption="", document_url=None, pdf_bytes=None):
        wa_doc_calls.append({
            "to": to_phone,
            "filename": filename,
            "caption": caption,
            "has_bytes": bool(pdf_bytes),
        })
        return {"success": True, "message_id": "wamid.web_paid_doc_1", "status": "sent"}

    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.send_email_resend_sync",
        classmethod(fake_send_email),
    )
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_document_message",
        fake_send_doc,
    )

    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    booking.passenger_email = "guest@shafsky.com"
    booking.passenger_phone = "9876501234"
    meta = dict(booking.metadata_json or {})
    meta["channel"] = "web"
    meta["passengers"] = [{"full_name": "Trillian Astra", "email": "trillian@heartofgold.org", "phone": "9876501234"}]
    booking.metadata_json = meta
    db.commit()

    res = confirm_via_service(db, booking, tx, order_id, payment_id)
    assert res["status"] == "CONFIRMED"

    db.expire_all()
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert inv is not None
    assert inv.customer_email == "trillian@heartofgold.org"

    customer_emails = [c for c in email_calls if c["to"] == "trillian@heartofgold.org"]
    assert len(customer_emails) == 1
    assert customer_emails[0]["attachments"]
    assert customer_emails[0]["attachments"][0]["content"].startswith(b"%PDF")

    assert len(wa_doc_calls) == 1
    assert wa_doc_calls[0]["to"] == "919876501234"
    assert wa_doc_calls[0]["has_bytes"] is True


def test_website_airport_booking_model_paid_generates_invoice_and_sends_email_and_whatsapp(monkeypatch):
    """
    Website booking created via POST /api/airport/bookings (AirportBooking + AirportPassenger):
    - Generates Invoice row on verified payment
    - Sends BOOKING_CONFIRMATION email with PDF invoice attached to AirportPassenger.contact_email
    - Sends WHATSAPP_INVOICE_PDF to AirportPassenger.contact_phone
    """
    from app.models.airport import AirportBooking, AirportFlightDetail, AirportPassenger

    email_calls = []
    wa_doc_calls = []

    def fake_send_email(cls, recipient_email, subject, html_content, attachments=None):
        email_calls.append({"to": recipient_email, "attachments": attachments})
        return {"status": "DELIVERED", "message_id": "re_airport_model_1"}

    def fake_send_doc(self, to_phone, *, filename, caption="", document_url=None, pdf_bytes=None):
        wa_doc_calls.append({"to": to_phone, "filename": filename, "has_bytes": bool(pdf_bytes)})
        return {"success": True, "message_id": "wamid.airport_model_doc_1", "status": "sent"}

    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.send_email_resend_sync",
        classmethod(fake_send_email),
    )
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_document_message",
        fake_send_doc,
    )

    db = next(get_db())
    booking_ref = f"SHF-APT-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"

    ab = AirportBooking(
        id=uuid.uuid4(),
        booking_reference=booking_ref,
        customer_id="cust-web-001",
        service_package="STANDARD_MEET_GREET",
        total_price=AMOUNT,
        currency="INR",
        status="PENDING",
    )
    db.add(ab)
    db.flush()
    pax = AirportPassenger(
        id=uuid.uuid4(),
        booking_id=ab.id,
        full_name="Ford Prefect",
        contact_email="ford@betelgeuse.org",
        contact_phone="9811122233",
        is_primary=True,
    )
    db.add(pax)
    flight = AirportFlightDetail(
        id=uuid.uuid4(),
        booking_id=ab.id,
        airline="Air India",
        flight_number="AI202",
        departure_airport="DEL",
        arrival_airport="LHR",
        terminal="T3",
        scheduled_time=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
        flight_type="DEPARTURE",
    )
    db.add(flight)
    tx = PaymentTransaction(
        id=uuid.uuid4(),
        transaction_ref=booking_ref,
        entity_type="AIRPORT_BOOKING",
        entity_id=booking_ref,
        amount=AMOUNT,
        currency="INR",
        gateway_provider="RAZORPAY",
        gateway_payment_id=order_id,
        status=PaymentStatus.PENDING,
    )
    db.add(tx)
    db.commit()

    res = PaymentService.handle_verified_payment(
        db,
        event_name="VERIFY_ENDPOINT",
        gateway_provider="RAZORPAY",
        order_id=order_id,
        payment_id=payment_id,
        booking_ref=booking_ref,
        amount=AMOUNT_PAISE,
        currency="INR",
        channel="Web Checkout",
    )
    assert res["status"] == "CONFIRMED"

    db.expire_all()
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert inv is not None
    assert inv.customer_email == "ford@betelgeuse.org"
    assert inv.customer_name == "Ford Prefect"

    customer_emails = [c for c in email_calls if c["to"] == "ford@betelgeuse.org"]
    assert len(customer_emails) == 1
    assert customer_emails[0]["attachments"]
    assert customer_emails[0]["attachments"][0]["content"].startswith(b"%PDF")

    assert len(wa_doc_calls) == 1
    assert wa_doc_calls[0]["to"] == "919811122233"
    assert wa_doc_calls[0]["has_bytes"] is True


def test_website_missing_email_does_not_crash_and_still_delivers_whatsapp_invoice(monkeypatch):
    email_calls = []
    wa_doc_calls = []

    def fake_send_email(cls, recipient_email, subject, html_content, attachments=None):
        email_calls.append(recipient_email)
        return {"status": "DELIVERED", "message_id": "re_missing_email"}

    def fake_send_doc(self, to_phone, *, filename, caption="", document_url=None, pdf_bytes=None):
        wa_doc_calls.append(to_phone)
        return {"success": True, "message_id": "wamid.missing_email_doc", "status": "sent"}

    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.send_email_resend_sync",
        classmethod(fake_send_email),
    )
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_document_message",
        fake_send_doc,
    )

    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    booking.passenger_email = "guest@shafsky.com"
    db.commit()

    res = confirm_via_service(db, booking, tx, order_id, payment_id)
    assert res["status"] == "CONFIRMED"

    db.expire_all()
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert inv is not None
    assert "guest@shafsky.com" not in email_calls
    assert wa_doc_calls == ["919876543210"]


def test_verify_endpoint_then_webhook_replay_sends_single_email_and_single_whatsapp_invoice(monkeypatch):
    email_calls = []
    wa_doc_calls = []

    def fake_send_email(cls, recipient_email, subject, html_content, attachments=None):
        email_calls.append(recipient_email)
        return {"status": "DELIVERED", "message_id": f"re_dedup_{len(email_calls)}"}

    def fake_send_doc(self, to_phone, *, filename, caption="", document_url=None, pdf_bytes=None):
        wa_doc_calls.append(to_phone)
        return {"success": True, "message_id": f"wamid.dedup_{len(wa_doc_calls)}", "status": "sent"}

    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.send_email_resend_sync",
        classmethod(fake_send_email),
    )
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_document_message",
        fake_send_doc,
    )

    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)

    # 1. Website frontend calls /verify (VERIFY_ENDPOINT)
    res1 = PaymentService.handle_verified_payment(
        db,
        event_name="VERIFY_ENDPOINT",
        gateway_provider="RAZORPAY",
        order_id=order_id,
        payment_id=payment_id,
        booking_ref=booking.booking_ref,
        amount=AMOUNT_PAISE,
        currency="INR",
        channel="Web Checkout",
    )
    assert res1["status"] == "CONFIRMED"

    # 2. Razorpay webhook arrives afterwards (payment.captured)
    res2, _ = post_payment_captured(booking.booking_ref, order_id, payment_id)
    assert res2.status_code == 200

    # 3. Duplicate webhook retry arrives
    res3, _ = post_payment_captured(booking.booking_ref, order_id, payment_id)
    assert res3.status_code == 200

    db.expire_all()
    invoices = list(db.scalars(select(Invoice).where(Invoice.transaction_id == tx.id)).all())
    assert len(invoices) == 1
    customer_emails = [e for e in email_calls if e == "arthur@galaxy.com"]
    assert len(customer_emails) == 1
    assert len(wa_doc_calls) == 1


def test_website_webhook_without_notes_booking_ref_generates_invoice_and_delivers_all(monkeypatch):
    """
    When Razorpay fires payment.authorized followed by payment.captured for a website
    checkout where payment.entity.notes is empty, the backend resolves booking_ref via
    PaymentTransaction, generates the paid invoice, sends the customer email with PDF,
    and sends the WhatsApp invoice PDF.
    """
    email_calls = []
    wa_doc_calls = []

    def fake_send_email(cls, recipient_email, subject, html_content, attachments=None):
        email_calls.append({"to": recipient_email, "attachments": attachments})
        return {"status": "DELIVERED", "message_id": f"re_nonotes_{len(email_calls)}"}

    def fake_send_doc(self, to_phone, *, filename, caption="", document_url=None, pdf_bytes=None):
        wa_doc_calls.append({"to": to_phone, "filename": filename, "has_bytes": bool(pdf_bytes)})
        return {"success": True, "message_id": f"wamid.nonotes_{len(wa_doc_calls)}", "status": "sent"}

    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.send_email_resend_sync",
        classmethod(fake_send_email),
    )
    monkeypatch.setattr(
        "app.integrations.whatsapp.client.WhatsAppClient.send_document_message",
        fake_send_doc,
    )

    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)

    # 1. payment.authorized with empty notes
    auth_payload = {
        "event": "payment.authorized",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": AMOUNT_PAISE,
                    "currency": "INR",
                    "status": "authorized",
                    "notes": {},
                }
            }
        },
    }
    b_auth = json.dumps(auth_payload).encode("utf-8")
    r_auth = client.post(
        "/api/payments/razorpay/webhook",
        content=b_auth,
        headers={"X-Razorpay-Signature": generate_webhook_signature(b_auth)},
    )
    assert r_auth.status_code == 200

    # 2. payment.captured with empty notes
    cap_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": AMOUNT_PAISE,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {},
                }
            }
        },
    }
    b_cap = json.dumps(cap_payload).encode("utf-8")
    r_cap = client.post(
        "/api/payments/razorpay/webhook",
        content=b_cap,
        headers={"X-Razorpay-Signature": generate_webhook_signature(b_cap)},
    )
    assert r_cap.status_code == 200
    assert r_cap.json()["data"]["status"] == "CONFIRMED"

    db.expire_all()
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert inv is not None
    assert inv.status == InvoiceStatus.PAID
    customer_emails = [c for c in email_calls if c["to"] == "arthur@galaxy.com"]
    assert len(customer_emails) == 1
    assert customer_emails[0]["attachments"]
    assert len(wa_doc_calls) == 1
    assert wa_doc_calls[0]["to"] == "919876543210"


def test_public_invoice_pdf_download_endpoint(monkeypatch):
    from app.security.payment_token import mint_payment_token
    from app.services.pdf_service import mint_invoice_pdf_token

    monkeypatch.setattr("app.services.pdf_service.upload_invoice_pdf", lambda *a, **k: True)
    db = next(get_db())
    booking, tx, order_id, payment_id = create_paid_ready_booking(db)
    confirm_via_service(db, booking, tx, order_id, payment_id)

    db.expire_all()
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert inv is not None

    # 1. Valid invoice HMAC token
    inv_token = mint_invoice_pdf_token(booking.booking_ref, inv.invoice_number)
    res_ok = client.get(
        f"/api/payments/invoices/{booking.booking_ref}/pdf?invoice={inv.invoice_number}&token={inv_token}"
    )
    assert res_ok.status_code == 200
    assert res_ok.headers["content-type"].startswith("application/pdf")
    assert res_ok.content.startswith(b"%PDF")

    # 2. Valid payment_token
    pay_token = mint_payment_token(booking.booking_ref)
    res_pt = client.get(
        f"/api/payments/invoices/{booking.booking_ref}/pdf?payment_token={pay_token}"
    )
    assert res_pt.status_code == 200
    assert res_pt.content.startswith(b"%PDF")

    # 3. Invalid token -> 403
    res_bad = client.get(
        f"/api/payments/invoices/{booking.booking_ref}/pdf?token=invalid_token"
    )
    assert res_bad.status_code == 403

