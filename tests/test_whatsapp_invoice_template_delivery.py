"""Regression tests for WhatsApp invoice document-template delivery and diagnostics."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

import pytest

from app.config import settings
from app.database import SessionLocal
from app.integrations.whatsapp.client import WhatsAppClient
from app.models.schema import (
    Booking,
    BookingStatus,
    NotificationRecord,
    NotificationStatus,
)
from app.services.notification_service import NotificationService


def _unique_ref(prefix: str = "SHF-INV") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def _purge_booking_and_notifications(booking_ref: str) -> None:
    db = SessionLocal()
    try:
        rows = db.query(NotificationRecord).all()
        for row in rows:
            payload = row.payload if isinstance(row.payload, dict) else {}
            if payload.get("booking_ref") == booking_ref:
                db.delete(row)
        db.query(Booking).filter(Booking.booking_ref == booking_ref).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


def _find_notification_record(
    db: Any, *, booking_ref: str, template_type: str
) -> NotificationRecord | None:
    rows = (
        db.query(NotificationRecord)
        .filter(NotificationRecord.template_type == template_type)
        .all()
    )
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        if payload.get("booking_ref") == booking_ref:
            return row
    return None


def _create_booking(booking_ref: str, phone: str = "+919876543210") -> None:
    db = SessionLocal()
    try:
        booking = Booking(
            booking_ref=booking_ref,
            passenger_name="Aariz Test",
            passenger_email="aariz.test@example.com",
            passenger_phone=phone,
            service_category="Airport Assistance",
            origin_code="ATQ",
            service_type="Silver Domestic",
            selected_services={"airport": "ATQ", "package_name": "Silver Domestic"},
            total_amount=2500.0,
            currency="INR",
            status=BookingStatus.CONFIRMED,
        )
        db.add(booking)
        db.commit()
    finally:
        db.close()


def test_send_document_template_message_uploads_pdf_and_builds_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = WhatsAppClient()
    monkeypatch.setenv("WHATSAPP_INVOICE_ENABLED", "1")
    monkeypatch.setattr(client, "phone_number_id", "1234567890")
    monkeypatch.setattr(client, "access_token", "test_token")

    uploaded_calls: List[Dict[str, Any]] = []
    sent_calls: List[Dict[str, Any]] = []

    def fake_upload(pdf_bytes: bytes, filename: str) -> str:
        uploaded_calls.append({"pdf_bytes": pdf_bytes, "filename": filename})
        return "media_doc_999"

    def fake_send_message(**kwargs: Any) -> Dict[str, Any]:
        sent_calls.append(kwargs)
        return {
            "success": True,
            "message_id": "wamid.tpl.doc.1",
            "status": "sent",
        }

    monkeypatch.setattr(client, "upload_media_document", fake_upload)
    monkeypatch.setattr(client, "send_message", fake_send_message)

    result = client.send_document_template_message(
        "+919876543210",
        template_name="shafsky_invoice_pdf",
        template_language="en",
        filename="INV-2026-001.pdf",
        body_parameters=["INV-2026-001", "SHF-TEST-001"],
        pdf_bytes=b"%PDF-1.4\n%test\n",
    )

    assert result["success"] is True
    assert len(uploaded_calls) == 1
    assert uploaded_calls[0]["filename"] == "INV-2026-001.pdf"
    assert len(sent_calls) == 1
    sent = sent_calls[0]
    assert sent["template_name"] == "shafsky_invoice_pdf"
    assert sent["template_language"] == "en"
    assert sent["template_components"] == [
        {
            "type": "header",
            "parameters": [
                {
                    "type": "document",
                    "document": {
                        "id": "media_doc_999",
                        "filename": "INV-2026-001.pdf",
                    },
                }
            ],
        },
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": "INV-2026-001"},
                {"type": "text", "text": "SHF-TEST-001"},
            ],
        },
    ]


def test_invoice_uses_document_template_directly_when_window_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    booking_ref = _unique_ref()
    _purge_booking_and_notifications(booking_ref)
    _create_booking(booking_ref)

    monkeypatch.setenv("WHATSAPP_INVOICE_ENABLED", "1")
    monkeypatch.setattr(WhatsAppClient, "is_configured", lambda self: True)
    monkeypatch.setattr(settings, "WHATSAPP_INVOICE_PDF_TEMPLATE", "shafsky_invoice_pdf")
    monkeypatch.setenv("WHATSAPP_INVOICE_TEMPLATE", "")
    monkeypatch.setattr(settings, "WHATSAPP_INVOICE_TEMPLATE_LANG", "en")
    monkeypatch.setenv(
        "WHATSAPP_INVOICE_TEMPLATE_BODY_FIELDS", "invoice_number,booking_ref"
    )

    monkeypatch.setattr(
        NotificationService,
        "_customer_service_window_open",
        classmethod(lambda cls, db, phone: False),
    )

    session_doc_calls: List[Dict[str, Any]] = []
    template_doc_calls: List[Dict[str, Any]] = []

    monkeypatch.setattr(
        WhatsAppClient,
        "send_document_message",
        lambda self, *args, **kwargs: (
            session_doc_calls.append(kwargs)
            or {"success": True, "message_id": "wamid.session.unused"}
        ),
    )

    def fake_template_send(self: WhatsAppClient, to_phone: str, **kwargs: Any) -> Dict[str, Any]:
        template_doc_calls.append({"to_phone": to_phone, **kwargs})
        return {
            "success": True,
            "message_id": "wamid.tpl.invoice.1",
            "status": "sent",
        }

    monkeypatch.setattr(
        WhatsAppClient,
        "send_document_template_message",
        fake_template_send,
    )

    db = SessionLocal()
    try:
        res = NotificationService.send_whatsapp_invoice_document(
            db,
            booking_ref=booking_ref,
            recipient_phone="+919876543210",
            invoice_number="INV-ATQ-1001",
            pdf_bytes=b"%PDF-1.4\n%invoice\n",
        )
        assert res["status"] == "DELIVERED"
        assert res["channel"] == "document_template"
        assert len(session_doc_calls) == 0
        assert len(template_doc_calls) == 1
        assert template_doc_calls[0]["template_name"] == "shafsky_invoice_pdf"
        assert template_doc_calls[0]["template_language"] == "en"
        assert template_doc_calls[0]["body_parameters"] == [
            "INV-ATQ-1001",
            booking_ref,
        ]
    finally:
        db.close()
        _purge_booking_and_notifications(booking_ref)


def test_invoice_falls_back_to_template_on_131047_and_retries_base_lang_on_132001(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    booking_ref = _unique_ref()
    _purge_booking_and_notifications(booking_ref)
    _create_booking(booking_ref)

    monkeypatch.setenv("WHATSAPP_INVOICE_ENABLED", "1")
    monkeypatch.setattr(WhatsAppClient, "is_configured", lambda self: True)
    monkeypatch.setattr(settings, "WHATSAPP_INVOICE_PDF_TEMPLATE", "shafsky_invoice_pdf")
    monkeypatch.setattr(settings, "WHATSAPP_INVOICE_TEMPLATE_LANG", "en_GB")
    monkeypatch.setenv(
        "WHATSAPP_INVOICE_TEMPLATE_BODY_FIELDS", "invoice_number,booking_ref"
    )

    monkeypatch.setattr(
        NotificationService,
        "_customer_service_window_open",
        classmethod(lambda cls, db, phone: True),
    )

    session_doc_calls: List[Dict[str, Any]] = []
    template_langs_tried: List[str] = []

    monkeypatch.setattr(
        WhatsAppClient,
        "send_document_message",
        lambda self, *args, **kwargs: (
            session_doc_calls.append(kwargs)
            or {
                "success": False,
                "error": "Re-engagement message",
                "error_code": 131047,
                "status": "failed",
            }
        ),
    )

    def fake_template_send(self: WhatsAppClient, to_phone: str, **kwargs: Any) -> Dict[str, Any]:
        lang = str(kwargs.get("template_language"))
        template_langs_tried.append(lang)
        if lang == "en_GB":
            return {
                "success": False,
                "error": "Template translation does not exist",
                "error_code": 132001,
                "status": "failed",
            }
        return {
            "success": True,
            "message_id": "wamid.tpl.fallback.1",
            "status": "sent",
        }

    monkeypatch.setattr(
        WhatsAppClient,
        "send_document_template_message",
        fake_template_send,
    )

    db = SessionLocal()
    try:
        res = NotificationService.send_whatsapp_invoice_document(
            db,
            booking_ref=booking_ref,
            recipient_phone="+919876543210",
            invoice_number="INV-ATQ-1002",
            pdf_bytes=b"%PDF-1.4\n%invoice\n",
        )
        assert res["status"] == "DELIVERED"
        assert res["channel"] == "document_template"
        assert len(session_doc_calls) == 1
        assert template_langs_tried == ["en_GB", "en"]
    finally:
        db.close()
        _purge_booking_and_notifications(booking_ref)


def test_resolve_invoice_template_settings_normalizes_en_us(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "WHATSAPP_INVOICE_PDF_TEMPLATE", "shafsky_invoice_pdf")
    monkeypatch.setattr(settings, "WHATSAPP_INVOICE_TEMPLATE_LANG", "en_US")
    tpl_name, tpl_lang = NotificationService._resolve_invoice_template_settings()
    assert tpl_name == "shafsky_invoice_pdf"
    assert tpl_lang == "en"


def test_invoice_timeout_unverified_records_pending_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    booking_ref = _unique_ref()
    _purge_booking_and_notifications(booking_ref)
    _create_booking(booking_ref)

    monkeypatch.setenv("WHATSAPP_INVOICE_ENABLED", "1")
    monkeypatch.setattr(WhatsAppClient, "is_configured", lambda self: True)
    monkeypatch.setattr(settings, "WHATSAPP_INVOICE_PDF_TEMPLATE", "")
    monkeypatch.setenv("WHATSAPP_INVOICE_TEMPLATE", "")

    monkeypatch.setattr(
        WhatsAppClient,
        "send_document_message",
        lambda self, *args, **kwargs: {
            "success": False,
            "error": "Request timed out before response",
            "status": "timeout_unverified",
        },
    )

    db = SessionLocal()
    try:
        res = NotificationService.send_whatsapp_invoice_document(
            db,
            booking_ref=booking_ref,
            recipient_phone="+919876543210",
            invoice_number="INV-ATQ-1004",
            pdf_bytes=b"%PDF-1.4\n%invoice\n",
        )
        assert res["status"] == "UNKNOWN"
        record = _find_notification_record(
            db, booking_ref=booking_ref, template_type="WHATSAPP_INVOICE_PDF"
        )
        assert record is not None
        assert record.status == NotificationStatus.PENDING
    finally:
        db.close()
        _purge_booking_and_notifications(booking_ref)


def test_invoice_131047_without_template_records_actionable_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    booking_ref = _unique_ref()
    _purge_booking_and_notifications(booking_ref)
    _create_booking(booking_ref)

    monkeypatch.setenv("WHATSAPP_INVOICE_ENABLED", "1")
    monkeypatch.setattr(WhatsAppClient, "is_configured", lambda self: True)
    monkeypatch.setattr(settings, "WHATSAPP_INVOICE_PDF_TEMPLATE", "")
    monkeypatch.setenv("WHATSAPP_INVOICE_TEMPLATE", "")

    monkeypatch.setattr(
        WhatsAppClient,
        "send_document_message",
        lambda self, *args, **kwargs: {
            "success": False,
            "error": "Re-engagement message",
            "error_code": 131047,
            "status": "failed",
        },
    )

    db = SessionLocal()
    try:
        res = NotificationService.send_whatsapp_invoice_document(
            db,
            booking_ref=booking_ref,
            recipient_phone="+919876543210",
            invoice_number="INV-ATQ-1003",
            pdf_bytes=b"%PDF-1.4\n%invoice\n",
        )
        assert res["status"] == "FAILED"
        record = _find_notification_record(
            db, booking_ref=booking_ref, template_type="WHATSAPP_INVOICE_PDF"
        )
        assert record is not None
        assert record.error_log is not None
        assert "WHATSAPP_INVOICE_PDF_TEMPLATE" in record.error_log
    finally:
        db.close()
        _purge_booking_and_notifications(booking_ref)


def test_customer_confirmation_persists_invoice_metadata_in_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    booking_ref = _unique_ref("SHF-CONF")
    _purge_booking_and_notifications(booking_ref)
    _create_booking(booking_ref)

    monkeypatch.setattr(WhatsAppClient, "is_configured", lambda self: True)
    monkeypatch.setattr(
        settings, "WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE", "shafsky_booking_confirmation"
    )
    monkeypatch.setattr(settings, "WHATSAPP_CUSTOMER_TEMPLATE_LANG", "en")
    monkeypatch.setattr(
        WhatsAppClient,
        "send_message",
        lambda self, **kwargs: {
            "success": True,
            "message_id": "wamid.conf.1",
            "status": "sent",
            "status_code": 200,
        },
    )

    db = SessionLocal()
    try:
        context = {
            "booking_ref": booking_ref,
            "passenger_name": "Aariz Test",
            "passenger_phone": "+919876543210",
            "status": "CONFIRMED",
            "airport_name": "Sri Guru Ram Dass Jee International Airport (ATQ)",
            "service_type": "Silver Domestic",
            "total_amount": 2500.0,
            "currency": "INR",
            "invoice_number": "INV-CONF-777",
            "invoice_url": "https://api.example.com/api/bookings/SHF-CONF/invoice",
        }
        NotificationService.send_customer_whatsapp_confirmation(db, context)
        record = _find_notification_record(
            db,
            booking_ref=booking_ref,
            template_type=NotificationService.WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE_TYPE,
        )
        assert record is not None
        assert isinstance(record.payload, dict)
        assert record.payload.get("invoice_number") == "INV-CONF-777"
        assert (
            record.payload.get("invoice_url")
            == "https://api.example.com/api/bookings/SHF-CONF/invoice"
        )
    finally:
        db.close()
        _purge_booking_and_notifications(booking_ref)
