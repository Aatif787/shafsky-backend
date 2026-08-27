"""
WhatsApp Razorpay Payment Link initiation, reuse/expiry, and webhook correlation.
"""

import hmac
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.database import Base, engine, get_db, SessionLocal
from app.models.schema import Booking, BookingStatus
from app.models.payment import PaymentTransaction, PaymentStatus, Invoice
from app.models.whatsapp_models import WhatsAppConversation
from app.providers.razorpay_provider import razorpay_provider
from app.services.payment_service import PaymentService
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def setup_webhook_secret(monkeypatch):
    test_secret = "shafsky_test_wh_secret"
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", test_secret)
    razorpay_provider.webhook_secret = test_secret
    monkeypatch.setattr(
        "app.services.pdf_service.schedule_invoice_fulfillment",
        lambda invoice_id: None,
    )
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify_booking_created",
        lambda *a, **k: {"status": "mocked"},
    )
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService.notify_booking_confirmed",
        lambda *a, **k: {"status": "mocked"},
    )
    yield


def generate_webhook_signature(body_bytes: bytes, secret: str = "shafsky_test_wh_secret") -> str:
    return hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


def _fake_link(ref: str, link_id: str = None, url: str = None, simulated: bool = False):
    plink = link_id or f"plink_{uuid.uuid4().hex[:12]}"
    short = url or f"https://rzp.io/i/{uuid.uuid4().hex[:8]}"
    return {
        "success": True,
        "payment_link_id": plink,
        "short_url": short,
        "order_id": None,
        "amount": 4500.0,
        "currency": "INR",
        "expire_by": int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp()),
        "status": "created",
        "simulated": simulated,
        "notes": {"booking_ref": ref, "channel": "whatsapp"},
    }


def _seed_review_conv(db, phone: str, amount: float = 4500.0) -> WhatsAppConversation:
    conv, _ = WhatsAppBookingStateMachine.get_or_create_conversation(db, phone)
    conv.selected_service_name = "Meet & Greet"
    conv.selected_airport_iata = "DEL"
    conv.selected_airport_name = "Indira Gandhi International Airport"
    conv.total_amount = amount
    conv.customer_name = "Test Guest"
    conv.customer_email = "guest@shafsky.com"
    conv.customer_phone = phone
    conv.booking_date = "20 August 2026"
    conv.passenger_count = 1
    conv.flight_num = "AI101"
    future = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    conv.flight_details_json = {
        "unit_price": amount,
        "base_price": amount,
        "journey_type": "DEPARTURE",
        "travel_type": "DOMESTIC",
        "verification_status": "verified",
        "verification_provider": "AVIATION_STACK",
        "origin_iata": "DEL",
        "destination_iata": "BOM",
        "departure_scheduled": future,
        "arrival_scheduled": future,
    }
    conv.current_state = "BOOKING_REVIEW"
    db.commit()
    return conv


def test_whatsapp_booking_creates_pending_booking_and_transaction(monkeypatch):
    captured = {}

    def fake_create(*args, **kwargs):
        captured["kwargs"] = kwargs
        return _fake_link(kwargs.get("booking_ref") or kwargs.get("reference_id"), "plink_wa_create1")

    monkeypatch.setattr(razorpay_provider, "create_payment_link", fake_create)
    monkeypatch.setattr(razorpay_provider, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv = _seed_review_conv(db, phone)
        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_text:
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
            db.refresh(conv)
            assert conv.current_state == "WAITING_PAYMENT"
            assert conv.booking_ref
            booking = db.scalar(select(Booking).where(Booking.booking_ref == conv.booking_ref))
            assert booking.status == BookingStatus.PENDING
            assert float(booking.total_amount) == 4500.0
            tx = db.scalar(
                select(PaymentTransaction).where(PaymentTransaction.entity_id == conv.booking_ref)
            )
            assert tx is not None
            assert tx.status == PaymentStatus.PENDING
            assert tx.gateway_payment_id == "plink_wa_create1"
            assert float(tx.amount) == 4500.0
            assert tx.currency == "INR"
            notes = (tx.gateway_response or {}).get("notes") or {}
            assert notes.get("booking_ref") == conv.booking_ref
            assert notes.get("channel") == "whatsapp"
            assert captured["kwargs"].get("channel") == "whatsapp"
            assert captured["kwargs"].get("booking_ref") == conv.booking_ref
            sent_all = " ".join(str(c[0][1]) for c in mock_text.call_args_list)
            assert "Please complete your payment" in sent_all
            assert "https://rzp.io/" in sent_all
            assert conv.razorpay_payment_url.startswith("https://")
    finally:
        db.close()


def test_simulated_plink_is_rejected_and_not_sent(monkeypatch):
    monkeypatch.setattr(
        razorpay_provider,
        "create_payment_link",
        lambda *a, **kw: _fake_link(kw.get("booking_ref"), "plink_sim_abc", "https://rzp.io/i/simulated_x", simulated=True),
    )
    monkeypatch.setattr(razorpay_provider, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv = _seed_review_conv(db, phone)
        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message") as mock_text:
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
            db.refresh(conv)
            booking = db.scalar(select(Booking).where(Booking.booking_ref == conv.booking_ref))
            assert booking.status == BookingStatus.PENDING
            tx = db.scalar(select(PaymentTransaction).where(PaymentTransaction.entity_id == conv.booking_ref))
            assert tx is None
            sent_blobs = " ".join(str(c[0][1]) for c in mock_text.call_args_list)
            assert "plink_sim_" not in sent_blobs
            assert "simulated" not in sent_blobs.lower()
            assert conv.current_state == "WAITING_PAYMENT"
    finally:
        db.close()


def test_active_payment_link_is_reused(monkeypatch):
    calls = {"n": 0}

    def fake_create(*args, **kwargs):
        calls["n"] += 1
        return _fake_link(kwargs.get("booking_ref"), "plink_reuse_1", "https://rzp.io/i/reuse1")

    monkeypatch.setattr(razorpay_provider, "create_payment_link", fake_create)
    monkeypatch.setattr(razorpay_provider, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv = _seed_review_conv(db, phone)
        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message"):
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
            db.refresh(conv)
            first_id = conv.razorpay_payment_link_id
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "resend")
            db.refresh(conv)
            assert conv.razorpay_payment_link_id == first_id
            assert calls["n"] == 1
            txs = list(db.scalars(select(PaymentTransaction).where(PaymentTransaction.entity_id == conv.booking_ref)))
            assert len(txs) == 1
    finally:
        db.close()


def test_expired_link_creates_replacement(monkeypatch):
    created = []

    def fake_create(*args, **kwargs):
        lid = f"plink_exp_{len(created)+1}"
        created.append(lid)
        return _fake_link(kwargs.get("booking_ref"), lid, f"https://rzp.io/i/{lid}")

    monkeypatch.setattr(razorpay_provider, "create_payment_link", fake_create)
    monkeypatch.setattr(razorpay_provider, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv = _seed_review_conv(db, phone)
        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message"):
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
            db.refresh(conv)
            tx = db.scalar(select(PaymentTransaction).where(PaymentTransaction.entity_id == conv.booking_ref))
            resp = dict(tx.gateway_response or {})
            resp["expire_by"] = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
            tx.gateway_response = resp
            db.commit()
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "resend")
            db.refresh(conv)
            assert conv.razorpay_payment_link_id != "plink_exp_1"
            assert conv.razorpay_payment_link_id == created[-1]
            db.refresh(tx)
            assert tx.status == PaymentStatus.EXPIRED
            txs = list(db.scalars(select(PaymentTransaction).where(PaymentTransaction.entity_id == conv.booking_ref)))
            assert len(txs) == 2
            booking = db.scalar(select(Booking).where(Booking.booking_ref == conv.booking_ref))
            assert booking.status == BookingStatus.PENDING
    finally:
        db.close()


def test_cancelled_link_creates_replacement(monkeypatch):
    created = []

    def fake_create(*args, **kwargs):
        lid = f"plink_can_{len(created)+1}"
        created.append(lid)
        return _fake_link(kwargs.get("booking_ref"), lid, f"https://rzp.io/i/{lid}")

    monkeypatch.setattr(razorpay_provider, "create_payment_link", fake_create)
    monkeypatch.setattr(razorpay_provider, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv = _seed_review_conv(db, phone)
        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message"):
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
            db.refresh(conv)
            tx = db.scalar(select(PaymentTransaction).where(PaymentTransaction.entity_id == conv.booking_ref))
            tx.status = PaymentStatus.CANCELLED
            resp = dict(tx.gateway_response or {})
            resp["link_status"] = "cancelled"
            tx.gateway_response = resp
            conv.payment_status = "CANCELLED"
            db.commit()
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "resend")
            db.refresh(conv)
            assert conv.razorpay_payment_link_id == created[-1]
            booking = db.scalar(select(Booking).where(Booking.booking_ref == conv.booking_ref))
            assert booking.status == BookingStatus.PENDING
    finally:
        db.close()


def _post_webhook(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body)
    return client.post("/api/payments/razorpay/webhook", content=body, headers={"X-Razorpay-Signature": sig})


def test_payment_link_paid_confirms_same_transaction(monkeypatch):
    monkeypatch.setattr(
        razorpay_provider,
        "create_payment_link",
        lambda *a, **kw: _fake_link(kw.get("booking_ref"), "plink_paid_1", "https://rzp.io/i/paid1"),
    )
    monkeypatch.setattr(razorpay_provider, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv = _seed_review_conv(db, phone, 12500.0)
        conv.flight_details_json = {
            "unit_price": 12500.0,
            "base_price": 12500.0,
            "journey_type": "DEPARTURE",
            "travel_type": "DOMESTIC",
            "verification_status": "verified",
            "verification_provider": "AVIATION_STACK",
            "origin_iata": "DEL",
            "destination_iata": "BOM",
            "departure_scheduled": (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat(),
            "arrival_scheduled": (datetime.now(timezone.utc) + timedelta(hours=50)).isoformat(),
        }
        db.commit()
        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message"):
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
        db.refresh(conv)
        booking_ref = conv.booking_ref
        pay_id = f"pay_{uuid.uuid4().hex[:10]}"
        order_id = f"order_{uuid.uuid4().hex[:10]}"
        payload = {
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": "plink_paid_1",
                        "amount": 1250000,
                        "amount_paid": 1250000,
                        "currency": "INR",
                        "status": "paid",
                        "order_id": order_id,
                        "reference_id": booking_ref,
                        "notes": {"booking_ref": booking_ref, "channel": "whatsapp"},
                    }
                },
                "payment": {
                    "entity": {
                        "id": pay_id,
                        "order_id": order_id,
                        "amount": 1250000,
                        "currency": "INR",
                        "notes": {"booking_ref": booking_ref, "channel": "whatsapp"},
                    }
                },
            },
        }
        res = _post_webhook(payload)
        assert res.status_code == 200
        assert res.json()["data"]["status"] == "CONFIRMED"
        booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
        db.refresh(booking)
        tx = db.scalar(select(PaymentTransaction).where(PaymentTransaction.entity_id == booking_ref))
        db.refresh(tx)
        assert booking.status == BookingStatus.CONFIRMED
        assert tx.status == PaymentStatus.SUCCESSFUL
        invoices = list(db.scalars(select(Invoice).where(Invoice.transaction_id == tx.id)))
        assert len(invoices) == 1
    finally:
        db.close()


def test_captured_and_order_paid_after_link_paid_are_idempotent(monkeypatch):
    monkeypatch.setattr(
        razorpay_provider,
        "create_payment_link",
        lambda *a, **kw: _fake_link(kw.get("booking_ref"), "plink_idemp_1", "https://rzp.io/i/idemp1"),
    )
    monkeypatch.setattr(razorpay_provider, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv = _seed_review_conv(db, phone, 12500.0)
        conv.flight_details_json = {
            "unit_price": 12500.0,
            "base_price": 12500.0,
            "journey_type": "DEPARTURE",
            "travel_type": "DOMESTIC",
            "verification_status": "verified",
            "verification_provider": "AVIATION_STACK",
            "origin_iata": "DEL",
            "destination_iata": "BOM",
            "departure_scheduled": (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat(),
            "arrival_scheduled": (datetime.now(timezone.utc) + timedelta(hours=50)).isoformat(),
        }
        db.commit()
        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message"):
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
        db.refresh(conv)
        booking_ref = conv.booking_ref
        pay_id = f"pay_{uuid.uuid4().hex[:10]}"
        order_id = f"order_{uuid.uuid4().hex[:10]}"

        def body(event, extra_payment=True):
            payload = {
                "event": event,
                "payload": {
                    "payment_link": {
                        "entity": {
                            "id": "plink_idemp_1",
                            "amount": 1250000,
                            "amount_paid": 1250000,
                            "currency": "INR",
                            "order_id": order_id,
                            "reference_id": booking_ref,
                            "notes": {"booking_ref": booking_ref, "channel": "whatsapp"},
                        }
                    },
                    "payment": {
                        "entity": {
                            "id": pay_id,
                            "order_id": order_id,
                            "amount": 1250000,
                            "currency": "INR",
                            "notes": {"booking_ref": booking_ref, "channel": "whatsapp"},
                        }
                    },
                    "order": {"entity": {"id": order_id, "amount": 1250000, "amount_paid": 1250000, "currency": "INR"}},
                },
            }
            return payload

        r1 = _post_webhook(body("payment_link.paid"))
        assert r1.json()["data"]["status"] == "CONFIRMED"
        r2 = _post_webhook(body("payment.captured"))
        assert r2.status_code == 200
        assert r2.json()["data"]["status"] == "CONFIRMED"
        r3 = _post_webhook(body("order.paid"))
        assert r3.status_code == 200
        assert r3.json()["data"]["status"] == "CONFIRMED"

        txs = list(
            db.scalars(
                select(PaymentTransaction).where(
                    PaymentTransaction.entity_id == booking_ref,
                    PaymentTransaction.is_duplicate.isnot(True),
                )
            )
        )
        assert len(txs) == 1
        invoices = list(db.scalars(select(Invoice).where(Invoice.transaction_id == txs[0].id)))
        assert len(invoices) == 1
        booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
        db.refresh(booking)
        assert booking.status == BookingStatus.CONFIRMED
    finally:
        db.close()


def test_distinct_payment_id_flagged_duplicate(monkeypatch):
    monkeypatch.setattr(
        razorpay_provider,
        "create_payment_link",
        lambda *a, **kw: _fake_link(kw.get("booking_ref"), "plink_dup_1", "https://rzp.io/i/dup1"),
    )
    monkeypatch.setattr(razorpay_provider, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv = _seed_review_conv(db, phone, 12500.0)
        conv.flight_details_json = {
            "unit_price": 12500.0,
            "base_price": 12500.0,
            "journey_type": "DEPARTURE",
            "travel_type": "DOMESTIC",
            "verification_status": "verified",
            "verification_provider": "AVIATION_STACK",
            "origin_iata": "DEL",
            "destination_iata": "BOM",
            "departure_scheduled": (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat(),
            "arrival_scheduled": (datetime.now(timezone.utc) + timedelta(hours=50)).isoformat(),
        }
        db.commit()
        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message"):
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
        db.refresh(conv)
        booking_ref = conv.booking_ref
        pay_a = f"pay_{uuid.uuid4().hex[:10]}"
        pay_b = f"pay_{uuid.uuid4().hex[:10]}"
        order_a = f"order_{uuid.uuid4().hex[:10]}"
        order_b = f"order_{uuid.uuid4().hex[:10]}"

        def captured(pay, order):
            return {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": pay,
                            "order_id": order,
                            "amount": 1250000,
                            "currency": "INR",
                            "notes": {"booking_ref": booking_ref, "channel": "whatsapp"},
                        }
                    }
                },
            }

        r1 = _post_webhook(captured(pay_a, order_a))
        assert r1.json()["data"]["status"] == "CONFIRMED"
        r2 = _post_webhook(captured(pay_b, order_b))
        assert r2.status_code == 200
        assert r2.json()["data"]["status"] == "DUPLICATE_PAYMENT_FLAGGED"
        dups = list(
            db.scalars(
                select(PaymentTransaction).where(
                    PaymentTransaction.entity_id == booking_ref,
                    PaymentTransaction.is_duplicate.is_(True),
                )
            )
        )
        assert len(dups) == 1
    finally:
        db.close()


def test_customer_i_paid_does_not_confirm_without_gateway(monkeypatch):
    monkeypatch.setattr(
        razorpay_provider,
        "create_payment_link",
        lambda *a, **kw: _fake_link(kw.get("booking_ref"), "plink_chk_1", "https://rzp.io/i/chk1"),
    )
    monkeypatch.setattr(razorpay_provider, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})
    monkeypatch.setattr(
        razorpay_provider,
        "fetch_payment_link",
        lambda payment_link_id: {"success": True, "status": "created", "payment_link_id": payment_link_id, "simulated": False},
    )

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv = _seed_review_conv(db, phone)
        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message"):
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
            db.refresh(conv)
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "I paid")
            db.refresh(conv)
            booking = db.scalar(select(Booking).where(Booking.booking_ref == conv.booking_ref))
            db.refresh(booking)
            assert booking.status == BookingStatus.PENDING
            assert conv.current_state == "WAITING_PAYMENT"
    finally:
        db.close()


def test_invalid_webhook_signature_rejected():
    payload = {"event": "payment_link.paid", "payload": {}}
    body = json.dumps(payload).encode("utf-8")
    res = client.post(
        "/api/payments/razorpay/webhook",
        content=body,
        headers={"X-Razorpay-Signature": "definitely_invalid"},
    )
    assert res.status_code == 400


def test_payment_link_expired_webhook_keeps_booking_pending(monkeypatch):
    monkeypatch.setattr(
        razorpay_provider,
        "create_payment_link",
        lambda *a, **kw: _fake_link(kw.get("booking_ref"), "plink_exwh_1", "https://rzp.io/i/exwh1"),
    )
    monkeypatch.setattr(razorpay_provider, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv = _seed_review_conv(db, phone)
        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message"):
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
        db.refresh(conv)
        payload = {
            "event": "payment_link.expired",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": "plink_exwh_1",
                        "status": "expired",
                        "reference_id": conv.booking_ref,
                        "notes": {"booking_ref": conv.booking_ref, "channel": "whatsapp"},
                    }
                }
            },
        }
        res = _post_webhook(payload)
        assert res.status_code == 200
        booking = db.scalar(select(Booking).where(Booking.booking_ref == conv.booking_ref))
        tx = db.scalar(select(PaymentTransaction).where(PaymentTransaction.entity_id == conv.booking_ref))
        db.refresh(booking)
        db.refresh(tx)
        assert booking.status == BookingStatus.PENDING
        assert tx.status == PaymentStatus.EXPIRED
    finally:
        db.close()


def test_waiting_payment_survives_session_timeout(monkeypatch):
    monkeypatch.setattr(
        razorpay_provider,
        "create_payment_link",
        lambda *a, **kw: _fake_link(kw.get("booking_ref"), "plink_to_1", "https://rzp.io/i/to1"),
    )
    monkeypatch.setattr(razorpay_provider, "list_payment_links_by_reference", lambda reference_id: {"success": True, "items": []})
    monkeypatch.setenv("WHATSAPP_SESSION_TIMEOUT_MINUTES", "15")

    db = SessionLocal()
    try:
        phone = f"91{uuid.uuid4().int % 10**10:010d}"
        conv = _seed_review_conv(db, phone)
        with patch("app.integrations.whatsapp.client.WhatsAppClient.send_text_message"):
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "CONFIRM", input_id="btn_confirm_booking")
            db.refresh(conv)
            conv.updated_at = datetime.now(timezone.utc) - timedelta(minutes=20)
            db.commit()
            WhatsAppBookingStateMachine.process_incoming_event(db, phone, "hi")
            db.refresh(conv)
            assert conv.current_state == "WAITING_PAYMENT"
            assert conv.booking_ref is not None
    finally:
        db.close()
