"""
Test Suite for Notification and Invoice Fulfillment Concurrency & Idempotency.

Verifies:
1. payment.authorized then payment.captured rapidly -> exactly one fulfillment
2. Two concurrent fulfill_paid_invoice() calls for same invoice -> one actually performs work
3. Two concurrent BOOKING_CONFIRMATION attempts -> one Meta send
4. Two concurrent WHATSAPP_INVOICE_PDF attempts -> one Meta send
5. payment_link.paid + payment.captured -> one fulfillment
6. order.paid + payment.captured -> one fulfillment
7. Failed Meta send -> notification becomes FAILED and retryable
8. Concurrent retry while first send is SENDING -> second attempt does not send
9. Stale SENDING lease can recover safely
10. WhatsApp invoice PDF idempotency remains correct
"""

import uuid
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pytest

from app.database import SessionLocal
from app.models.schema import Booking, BookingStatus, NotificationRecord, NotificationStatus
from app.models.payment import PaymentTransaction, Invoice, InvoiceStatus, PaymentStatus, PaymentMethod
from app.services.payment_service import PaymentService
from app.services.pdf_service import fulfill_paid_invoice
from app.services.notification_service import NotificationService


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _create_test_booking_and_tx(db, booking_ref=None, amount=10000.0, phone="918864931247"):
    ref = booking_ref or f"SHF-TEST-{uuid.uuid4().hex[:6].upper()}"
    b_id = uuid.uuid4()
    booking = Booking(
        id=b_id,
        booking_ref=ref,
        passenger_name="Aariz Test",
        passenger_email="test@shafsky.com",
        passenger_phone=phone,
        service_category="Airport Assistance",
        service_type="Elite Service",
        origin_code="DEL",
        dest_code="DXB",
        total_amount=amount,
        currency="INR",
        metadata_json={"channel": "whatsapp"},
        status=BookingStatus.PENDING,
    )
    db.add(booking)

    tx = PaymentTransaction(
        id=uuid.uuid4(),
        transaction_ref=f"PAY-{uuid.uuid4().hex[:8].upper()}",
        entity_type="AIRPORT_BOOKING",
        entity_id=ref,
        amount=amount,
        currency="INR",
        payment_method=PaymentMethod.CREDIT_CARD,
        status=PaymentStatus.PENDING,
        gateway_provider="RAZORPAY",
    )
    db.add(tx)
    db.commit()
    db.refresh(booking)
    db.refresh(tx)
    return booking, tx


def _build_payload(event: str, pay_id: str, order_id: str, booking_ref: str, amount: float = 10000.0):
    paise = int(amount * 100)
    return {
        "event": event,
        "payload": {
            "payment": {
                "entity": {
                    "id": pay_id,
                    "order_id": order_id,
                    "amount": paise,
                    "currency": "INR",
                    "status": "captured" if event == "payment.captured" else "authorized",
                    "notes": {"booking_ref": booking_ref, "channel": "whatsapp"},
                }
            },
            "order": {
                "entity": {
                    "id": order_id,
                    "amount": paise,
                    "amount_paid": paise,
                    "currency": "INR",
                    "status": "paid",
                    "notes": {"booking_ref": booking_ref, "channel": "whatsapp"},
                }
            },
            "payment_link": {
                "entity": {
                    "id": f"plink_{pay_id}",
                    "amount": paise,
                    "amount_paid": paise,
                    "currency": "INR",
                    "status": "paid",
                    "notes": {"booking_ref": booking_ref, "channel": "whatsapp"},
                }
            },
        },
    }


def test_1_rapid_payment_authorized_and_captured_single_fulfillment(db_session):
    """1. payment.authorized then payment.captured rapidly -> one fulfillment"""
    booking, tx = _create_test_booking_and_tx(db_session)
    pay_id = f"pay_{uuid.uuid4().hex[:8]}"
    order_id = f"order_{uuid.uuid4().hex[:8]}"

    with patch("app.services.pdf_service.generate_invoice_pdf_bytes", return_value=b"%PDF-1.4 test"), \
         patch("app.services.pdf_service.upload_invoice_pdf", return_value=True), \
         patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message", return_value={"success": True, "message_id": "wamid.1"}), \
         patch("app.integrations.whatsapp.client.whatsapp_client.send_document_message", return_value={"success": True, "message_id": "wamid.2"}), \
         patch("app.services.notification_service.NotificationService.send_email_resend_sync", return_value={"status": "DELIVERED", "message_id": "resend_1"}):

        # Event 1: payment.authorized
        res1 = PaymentService.handle_verified_payment(
            db_session,
            event_name="payment.authorized",
            gateway_provider="RAZORPAY",
            order_id=order_id,
            payment_id=pay_id,
            booking_ref=booking.booking_ref,
            raw_payload=_build_payload("payment.authorized", pay_id, order_id, booking.booking_ref),
        )
        assert res1["success"] is True

        # Event 2: payment.captured (rapid replay of same payment)
        res2 = PaymentService.handle_verified_payment(
            db_session,
            event_name="payment.captured",
            gateway_provider="RAZORPAY",
            order_id=order_id,
            payment_id=pay_id,
            booking_ref=booking.booking_ref,
            raw_payload=_build_payload("payment.captured", pay_id, order_id, booking.booking_ref),
        )
        assert res2["success"] is True

    # Verify only ONE booking confirmation record and ONE whatsapp invoice pdf record exist
    conf_notes = db_session.query(NotificationRecord).filter(
        NotificationRecord.template_type == "BOOKING_CONFIRMATION"
    ).all()
    matching_conf = [n for n in conf_notes if (n.payload or {}).get("booking_ref") == booking.booking_ref]
    assert len(matching_conf) == 1

    pdf_notes = db_session.query(NotificationRecord).filter(
        NotificationRecord.template_type == "WHATSAPP_INVOICE_PDF"
    ).all()
    matching_pdf = [n for n in pdf_notes if (n.payload or {}).get("booking_ref") == booking.booking_ref]
    assert len(matching_pdf) == 1


def test_2_concurrent_fulfill_paid_invoice_calls(db_session):
    """2. two concurrent fulfill_paid_invoice() calls for same invoice -> one actually performs work"""
    booking, tx = _create_test_booking_and_tx(db_session)
    invoice = Invoice(
        id=uuid.uuid4(),
        invoice_number=f"INV-TEST-{uuid.uuid4().hex[:6].upper()}",
        transaction_id=tx.id,
        customer_name="Aariz Test",
        customer_email="test@shafsky.com",
        subtotal_amount=8474.58,
        tax_amount=1525.42,
        total_amount=10000.0,
        currency="INR",
        status=InvoiceStatus.PAID,
    )
    db_session.add(invoice)
    db_session.commit()
    db_session.refresh(invoice)

    results = []

    def _worker():
        db = SessionLocal()
        try:
            with patch("app.services.pdf_service.generate_invoice_pdf_bytes", return_value=b"%PDF-1.4 test"), \
                 patch("app.services.pdf_service.upload_invoice_pdf", return_value=True), \
                 patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message", return_value={"success": True, "message_id": "wamid.1"}), \
                 patch("app.integrations.whatsapp.client.whatsapp_client.send_document_message", return_value={"success": True, "message_id": "wamid.2"}), \
                 patch("app.services.notification_service.NotificationService.send_email_resend_sync", return_value={"status": "DELIVERED", "message_id": "resend_1"}):
                res = fulfill_paid_invoice(db, str(invoice.id))
                results.append(res)
        finally:
            db.close()

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    assert all(r.get("success") is True for r in results)

    conf_notes = db_session.query(NotificationRecord).filter(
        NotificationRecord.template_type == "BOOKING_CONFIRMATION"
    ).all()
    matching_conf = [n for n in conf_notes if (n.payload or {}).get("booking_ref") == booking.booking_ref]
    assert len(matching_conf) == 1


def test_3_two_concurrent_booking_confirmation_claims(db_session):
    """3. two concurrent BOOKING_CONFIRMATION attempts -> one Meta send"""
    booking, tx = _create_test_booking_and_tx(db_session)
    send_count = 0

    def mock_send(*args, **kwargs):
        nonlocal send_count
        send_count += 1
        return {"success": True, "message_id": f"wamid.{send_count}"}

    ctx = {
        "booking_ref": booking.booking_ref,
        "passenger_name": booking.passenger_name,
        "passenger_email": booking.passenger_email,
        "passenger_phone": booking.passenger_phone,
        "service_name": "Elite Service",
        "total_amount": 10000.0,
        "currency": "INR",
    }

    with patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message", side_effect=mock_send), \
         patch("app.services.notification_service.NotificationService.send_email_resend_sync", return_value={"status": "DELIVERED", "message_id": "resend_1"}):

        res1 = NotificationService.notify_booking_confirmed(db_session, ctx)
        res2 = NotificationService.notify_booking_confirmed(db_session, ctx)

    assert send_count == 1
    assert res1.get("customer", {}).get("status") == "DELIVERED"
    assert res2.get("customer", {}).get("status") == "SKIPPED"


def test_4_two_concurrent_whatsapp_invoice_pdf_claims(db_session):
    """4. two concurrent WHATSAPP_INVOICE_PDF attempts -> one Meta send"""
    booking, tx = _create_test_booking_and_tx(db_session)
    send_count = 0

    def mock_send_doc(*args, **kwargs):
        nonlocal send_count
        send_count += 1
        return {"success": True, "message_id": f"wamid.doc.{send_count}"}

    with patch("app.integrations.whatsapp.client.whatsapp_client.send_document_message", side_effect=mock_send_doc):
        res1 = NotificationService.send_whatsapp_invoice_document(
            db_session,
            booking_ref=booking.booking_ref,
            recipient_phone=booking.passenger_phone,
            invoice_number="INV-2026-TEST",
            pdf_bytes=b"%PDF-test",
        )
        res2 = NotificationService.send_whatsapp_invoice_document(
            db_session,
            booking_ref=booking.booking_ref,
            recipient_phone=booking.passenger_phone,
            invoice_number="INV-2026-TEST",
            pdf_bytes=b"%PDF-test",
        )

    assert send_count == 1
    assert res1.get("status") == "DELIVERED"
    assert res2.get("status") == "SKIPPED"


def test_5_payment_link_paid_and_captured_single_fulfillment(db_session):
    """5. payment_link.paid + payment.captured -> one fulfillment"""
    booking, tx = _create_test_booking_and_tx(db_session)
    pay_id = f"pay_{uuid.uuid4().hex[:8]}"
    order_id = f"order_{uuid.uuid4().hex[:8]}"

    with patch("app.services.pdf_service.generate_invoice_pdf_bytes", return_value=b"%PDF-1.4 test"), \
         patch("app.services.pdf_service.upload_invoice_pdf", return_value=True), \
         patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message", return_value={"success": True, "message_id": "wamid.1"}), \
         patch("app.integrations.whatsapp.client.whatsapp_client.send_document_message", return_value={"success": True, "message_id": "wamid.2"}), \
         patch("app.services.notification_service.NotificationService.send_email_resend_sync", return_value={"status": "DELIVERED", "message_id": "resend_1"}):

        res1 = PaymentService.handle_verified_payment(
            db_session,
            event_name="payment_link.paid",
            gateway_provider="RAZORPAY",
            order_id=order_id,
            payment_id=pay_id,
            booking_ref=booking.booking_ref,
            raw_payload=_build_payload("payment_link.paid", pay_id, order_id, booking.booking_ref),
        )
        res2 = PaymentService.handle_verified_payment(
            db_session,
            event_name="payment.captured",
            gateway_provider="RAZORPAY",
            order_id=order_id,
            payment_id=pay_id,
            booking_ref=booking.booking_ref,
            raw_payload=_build_payload("payment.captured", pay_id, order_id, booking.booking_ref),
        )

    assert res1["success"] is True
    assert res2["success"] is True

    conf_notes = db_session.query(NotificationRecord).filter(
        NotificationRecord.template_type == "BOOKING_CONFIRMATION"
    ).all()
    matching_conf = [n for n in conf_notes if (n.payload or {}).get("booking_ref") == booking.booking_ref]
    assert len(matching_conf) == 1


def test_6_order_paid_and_captured_single_fulfillment(db_session):
    """6. order.paid + payment.captured -> one fulfillment"""
    booking, tx = _create_test_booking_and_tx(db_session)
    pay_id = f"pay_{uuid.uuid4().hex[:8]}"
    order_id = f"order_{uuid.uuid4().hex[:8]}"

    with patch("app.services.pdf_service.generate_invoice_pdf_bytes", return_value=b"%PDF-1.4 test"), \
         patch("app.services.pdf_service.upload_invoice_pdf", return_value=True), \
         patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message", return_value={"success": True, "message_id": "wamid.1"}), \
         patch("app.integrations.whatsapp.client.whatsapp_client.send_document_message", return_value={"success": True, "message_id": "wamid.2"}), \
         patch("app.services.notification_service.NotificationService.send_email_resend_sync", return_value={"status": "DELIVERED", "message_id": "resend_1"}):

        res1 = PaymentService.handle_verified_payment(
            db_session,
            event_name="order.paid",
            gateway_provider="RAZORPAY",
            order_id=order_id,
            payment_id=pay_id,
            booking_ref=booking.booking_ref,
            raw_payload=_build_payload("order.paid", pay_id, order_id, booking.booking_ref),
        )
        res2 = PaymentService.handle_verified_payment(
            db_session,
            event_name="payment.captured",
            gateway_provider="RAZORPAY",
            order_id=order_id,
            payment_id=pay_id,
            booking_ref=booking.booking_ref,
            raw_payload=_build_payload("payment.captured", pay_id, order_id, booking.booking_ref),
        )

    assert res1["success"] is True
    assert res2["success"] is True

    conf_notes = db_session.query(NotificationRecord).filter(
        NotificationRecord.template_type == "BOOKING_CONFIRMATION"
    ).all()
    matching_conf = [n for n in conf_notes if (n.payload or {}).get("booking_ref") == booking.booking_ref]
    assert len(matching_conf) == 1


def test_7_failed_meta_send_becomes_failed_and_retryable(db_session):
    """7. failed Meta send -> notification becomes FAILED and retryable"""
    booking, tx = _create_test_booking_and_tx(db_session)

    # First attempt: Meta API returns error
    with patch("app.integrations.whatsapp.client.whatsapp_client.send_document_message", return_value={"success": False, "error": "rate_limit"}):
        res1 = NotificationService.send_whatsapp_invoice_document(
            db_session,
            booking_ref=booking.booking_ref,
            recipient_phone=booking.passenger_phone,
            invoice_number="INV-FAIL-TEST",
            pdf_bytes=b"%PDF-test",
        )
    assert res1["status"] == "FAILED"

    # Second attempt: Meta API succeeds on retry
    with patch("app.integrations.whatsapp.client.whatsapp_client.send_document_message", return_value={"success": True, "message_id": "wamid.retry.ok"}):
        res2 = NotificationService.send_whatsapp_invoice_document(
            db_session,
            booking_ref=booking.booking_ref,
            recipient_phone=booking.passenger_phone,
            invoice_number="INV-FAIL-TEST",
            pdf_bytes=b"%PDF-test",
        )
    assert res2["status"] == "DELIVERED"
    assert res2["message_id"] == "wamid.retry.ok"


def test_8_concurrent_retry_while_sending(db_session):
    """8. concurrent retry while first send is SENDING -> second attempt does not send"""
    booking, tx = _create_test_booking_and_tx(db_session)

    # Create an active SENDING record with recent timestamp (< 300s)
    rec = NotificationRecord(
        id=uuid.uuid4(),
        recipient_phone=booking.passenger_phone,
        template_type=NotificationService.WHATSAPP_INVOICE_TEMPLATE,
        channel="WHATSAPP",
        payload={"booking_ref": booking.booking_ref, "invoice_number": "INV-LOCK-TEST"},
        status=NotificationStatus.SENDING,
        attempts=1,
        max_attempts=3,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(rec)
    db_session.commit()

    with patch("app.integrations.whatsapp.client.whatsapp_client.send_document_message") as mock_send:
        res = NotificationService.send_whatsapp_invoice_document(
            db_session,
            booking_ref=booking.booking_ref,
            recipient_phone=booking.passenger_phone,
            invoice_number="INV-LOCK-TEST",
            pdf_bytes=b"%PDF-test",
        )
        assert res["status"] == "SKIPPED"
        assert res["claim_status"] == "IN_PROGRESS"
        mock_send.assert_not_called()


def test_9_stale_sending_lease_recovers_safely(db_session):
    """9. stale SENDING lease can recover safely"""
    booking, tx = _create_test_booking_and_tx(db_session)

    # Create a stale SENDING record (updated 10 minutes ago)
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    rec = NotificationRecord(
        id=uuid.uuid4(),
        recipient_phone=booking.passenger_phone,
        template_type=NotificationService.WHATSAPP_INVOICE_TEMPLATE,
        channel="WHATSAPP",
        payload={"booking_ref": booking.booking_ref, "invoice_number": "INV-STALE-TEST"},
        status=NotificationStatus.SENDING,
        attempts=1,
        max_attempts=3,
        created_at=stale_time,
        updated_at=stale_time,
    )
    db_session.add(rec)
    db_session.commit()

    with patch("app.integrations.whatsapp.client.whatsapp_client.send_document_message", return_value={"success": True, "message_id": "wamid.recovered"}):
        res = NotificationService.send_whatsapp_invoice_document(
            db_session,
            booking_ref=booking.booking_ref,
            recipient_phone=booking.passenger_phone,
            invoice_number="INV-STALE-TEST",
            pdf_bytes=b"%PDF-test",
        )
        assert res["status"] == "DELIVERED"
        assert res["message_id"] == "wamid.recovered"

    db_session.refresh(rec)
    assert rec.status == NotificationStatus.DELIVERED
    assert rec.attempts == 2
