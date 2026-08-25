"""
Phase 3 Test Suite: Enterprise-Grade Razorpay Webhooks, State Synchronization & Idempotency.
Verifies HMAC signature verification, persistent webhook deduplication, canonical payment processing,
cross-booking protection, failure handling, frontend verification, and WhatsApp compatibility.
"""

import pytest
import uuid
import hmac
import hashlib
import json
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.database import Base, engine, get_db
from app.models.schema import Booking, BookingStatus
from app.models.payment import PaymentTransaction, PaymentStatus, Invoice, InvoiceStatus, PaymentWebhookEvent
from app.models.whatsapp_models import WhatsAppConversation
from app.providers.razorpay_provider import razorpay_provider
from app.services.auth_service import AuthService

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def setup_webhook_secret(monkeypatch):
    """Inject a dedicated test webhook secret so HMAC signature verification works in tests."""
    test_secret = "shafsky_test_wh_secret"
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", test_secret)
    razorpay_provider.webhook_secret = test_secret
    yield


def generate_webhook_signature(body_bytes: bytes, secret: str = "shafsky_test_wh_secret") -> str:
    return hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


def generate_payment_signature(order_id: str, payment_id: str, secret: str = "shafsky_test_key_secret") -> str:
    msg = f"{order_id}|{payment_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def create_test_booking_and_tx(db_session, booking_ref: str, order_id: str, amount: float = 12500.0):
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
        total_amount=amount,
        currency="INR",
        status=BookingStatus.PENDING,
        metadata_json={"service_airport": "DEL", "journey_type": "DEPARTURE"}
    )
    db_session.add(booking)

    tx = PaymentTransaction(
        id=uuid.uuid4(),
        transaction_ref=booking_ref,
        entity_type="AIRPORT_BOOKING",
        entity_id=booking_ref,
        customer_id=None,
        amount=amount,
        currency="INR",
        gateway_provider="RAZORPAY",
        gateway_payment_id=order_id,
        status=PaymentStatus.PENDING
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(booking)
    db_session.refresh(tx)
    return booking, tx


def test_1_valid_payment_captured_confirms_booking():
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret) if razorpay_provider.webhook_secret else "simulated_webhook_signature"

    headers = {"X-Razorpay-Signature": sig, "Content-Type": "application/json"}
    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers=headers)
    assert res.status_code == 200, res.text
    res_data = res.json()
    assert res_data["success"] is True
    assert res_data["data"]["status"] == "CONFIRMED"

    # Verify Database state
    db.refresh(booking)
    db.refresh(tx)
    assert booking.status == BookingStatus.CONFIRMED
    assert tx.status == PaymentStatus.SUCCESSFUL
    assert tx.gateway_payment_id == payment_id

    # Verify Invoice created
    inv = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert inv is not None
    assert inv.customer_email == "arthur@galaxy.com"


def test_2_duplicate_payment_captured_idempotency():
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    payload = {
        "event": "payment.captured",
        "event_id": event_id,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret) if razorpay_provider.webhook_secret else "simulated_webhook_signature"
    headers = {"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id, "Content-Type": "application/json"}

    # Delivery 1
    res1 = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["data"]["status"] == "CONFIRMED"

    # Delivery 2 (Duplicate)
    res2 = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["data"]["status"] == "DUPLICATE_IGNORED"

    # Verify only 1 invoice exists
    invoices = list(db.scalars(select(Invoice).where(Invoice.transaction_id == tx.id)).all())
    assert len(invoices) == 1


def test_3_order_paid_after_payment_captured_is_idempotent():
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    # 1. Send payment.captured
    payload1 = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    b1 = json.dumps(payload1).encode("utf-8")
    sig1 = generate_webhook_signature(b1, razorpay_provider.webhook_secret) if razorpay_provider.webhook_secret else "simulated_webhook_signature"
    res1 = client.post("/api/payments/razorpay/webhook", content=b1, headers={"X-Razorpay-Signature": sig1})
    assert res1.status_code == 200

    # 2. Send order.paid
    payload2 = {
        "event": "order.paid",
        "payload": {
            "order": {
                "entity": {
                    "id": order_id,
                    "receipt": booking_ref,
                    "amount_paid": 1250000,
                    "currency": "INR",
                    "status": "paid",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    b2 = json.dumps(payload2).encode("utf-8")
    sig2 = generate_webhook_signature(b2, razorpay_provider.webhook_secret) if razorpay_provider.webhook_secret else "simulated_webhook_signature"
    res2 = client.post("/api/payments/razorpay/webhook", content=b2, headers={"X-Razorpay-Signature": sig2})
    assert res2.status_code == 200
    assert res2.json()["data"]["status"] == "CONFIRMED"

    # Status remains confirmed
    db.refresh(booking)
    assert booking.status == BookingStatus.CONFIRMED


def test_4_order_paid_without_prior_payment_captured():
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    payload = {
        "event": "order.paid",
        "payload": {
            "order": {
                "entity": {
                    "id": order_id,
                    "receipt": booking_ref,
                    "amount_paid": 1250000,
                    "currency": "INR",
                    "status": "paid",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret) if razorpay_provider.webhook_secret else "simulated_webhook_signature"

    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "CONFIRMED"

    db.refresh(booking)
    assert booking.status == BookingStatus.CONFIRMED


def test_5_invalid_webhook_signature_rejected():
    payload = {"event": "payment.captured", "payload": {}}
    body_bytes = json.dumps(payload).encode("utf-8")
    headers = {"X-Razorpay-Signature": "invalid_forged_signature_12345"}

    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers=headers)
    assert res.status_code == 400
    assert "Invalid Razorpay webhook signature" in res.json()["detail"]


def test_6_unknown_order_id_handled_gracefully():
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_unknown_999",
                    "order_id": "order_unknown_999",
                    "notes": {"booking_ref": "SHF-UNKNOWN-999"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret) if razorpay_provider.webhook_secret else "simulated_webhook_signature"

    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "NOT_FOUND"


def test_7_payment_failed_leaves_booking_pending():
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "error_description": "Card was declined by issuing bank",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret) if razorpay_provider.webhook_secret else "simulated_webhook_signature"

    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "FAILED"

    # Booking must NOT be confirmed; remains PENDING for retry
    db.refresh(booking)
    db.refresh(tx)
    assert booking.status == BookingStatus.PENDING
    assert tx.status == PaymentStatus.FAILED


def test_8_frontend_verify_endpoint():
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    sig = generate_payment_signature(order_id, payment_id, razorpay_provider.key_secret) if razorpay_provider.key_secret else "simulated_signature"

    verify_payload = {
        "razorpay_order_id": order_id,
        "razorpay_payment_id": payment_id,
        "razorpay_signature": sig,
        "booking_ref": booking_ref
    }

    res = client.post("/api/payments/verify", json=verify_payload)
    assert res.status_code == 200, res.text
    assert res.json()["success"] is True
    assert res.json()["data"]["status"] == "CONFIRMED"

    db.refresh(booking)
    db.refresh(tx)
    assert booking.status == BookingStatus.CONFIRMED
    assert tx.status == PaymentStatus.SUCCESSFUL


def test_9_public_booking_status_endpoint():
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    # 1. Check status while PENDING
    res1 = client.get(f"/api/bookings/{booking_ref}/status")
    assert res1.status_code == 200
    assert res1.json()["data"]["status"] == "PENDING"
    assert res1.json()["data"]["paymentStatus"] == "PENDING"

    # 2. Promote to CONFIRMED
    booking.status = BookingStatus.CONFIRMED
    tx.status = PaymentStatus.SUCCESSFUL
    db.commit()

    # 3. Check status while CONFIRMED
    res2 = client.get(f"/api/bookings/{booking_ref}/status")
    assert res2.status_code == 200
    assert res2.json()["data"]["status"] == "CONFIRMED"
    assert res2.json()["data"]["paymentStatus"] == "PAID"


def test_10_whatsapp_channel_compatibility():
    db = next(get_db())
    booking_ref = f"SHF-WA-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    # Create active WhatsApp conversation
    unique_phone = f"91{uuid.uuid4().int % 10000000000:010d}"
    conv = WhatsAppConversation(
        id=uuid.uuid4(),
        phone_number=unique_phone,
        customer_name="WhatsApp Traveller",
        customer_phone=unique_phone,
        current_state="WAITING_PAYMENT",
        booking_ref=booking_ref,
        selected_service_name="Meet & Assist",
        payment_status="PENDING"
    )
    db.add(conv)
    db.commit()

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "notes": {"booking_ref": booking_ref, "channel": "whatsapp"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret) if razorpay_provider.webhook_secret else "simulated_webhook_signature"

    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "CONFIRMED"

    # Verify conversation updated
    db.refresh(conv)
    assert conv.current_state == "COMPLETED"
    assert conv.payment_status == "SUCCESSFUL"


# ──────────────────────────────────────────────────────────────────────────────
# Tests 11-16: payment.authorized Safety
# ──────────────────────────────────────────────────────────────────────────────

def test_11_payment_authorized_does_not_confirm_booking():
    """payment.authorized must NOT mark the booking as CONFIRMED."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    payload = {
        "event": "payment.authorized",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "status": "authorized",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret)
    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})

    assert res.status_code == 200
    assert res.json()["data"]["status"] == "AUTHORIZED"

    # Booking must remain PENDING
    db.refresh(booking)
    assert booking.status == BookingStatus.PENDING


def test_12_payment_authorized_sets_processing_status():
    """payment.authorized must set PaymentTransaction to PROCESSING, not SUCCESSFUL."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    payload = {
        "event": "payment.authorized",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "status": "authorized",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret)
    client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})

    db.refresh(tx)
    assert tx.status == PaymentStatus.PROCESSING


def test_13_payment_authorized_no_invoice():
    """payment.authorized must NOT generate an invoice."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    payload = {
        "event": "payment.authorized",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "status": "authorized",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret)
    client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})

    invoices = list(db.scalars(select(Invoice).where(Invoice.transaction_id == tx.id)).all())
    assert len(invoices) == 0


def test_14_authorized_then_captured_confirms_booking():
    """payment.authorized followed by payment.captured must confirm the booking exactly once."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    # Step 1: payment.authorized
    auth_payload = {
        "event": "payment.authorized",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "status": "authorized",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    b1 = json.dumps(auth_payload).encode("utf-8")
    sig1 = generate_webhook_signature(b1, razorpay_provider.webhook_secret)
    res1 = client.post("/api/payments/razorpay/webhook", content=b1, headers={"X-Razorpay-Signature": sig1})
    assert res1.status_code == 200
    assert res1.json()["data"]["status"] == "AUTHORIZED"

    db.refresh(booking)
    assert booking.status == BookingStatus.PENDING

    # Step 2: payment.captured
    cap_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    b2 = json.dumps(cap_payload).encode("utf-8")
    sig2 = generate_webhook_signature(b2, razorpay_provider.webhook_secret)
    res2 = client.post("/api/payments/razorpay/webhook", content=b2, headers={"X-Razorpay-Signature": sig2})
    assert res2.status_code == 200
    assert res2.json()["data"]["status"] == "CONFIRMED"

    db.refresh(booking)
    db.refresh(tx)
    assert booking.status == BookingStatus.CONFIRMED
    assert tx.status == PaymentStatus.SUCCESSFUL

    # Exactly one invoice
    invoices = list(db.scalars(select(Invoice).where(Invoice.transaction_id == tx.id)).all())
    assert len(invoices) == 1


def test_15_authorized_then_order_paid_confirms_booking():
    """payment.authorized followed by order.paid must confirm the booking."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    # Step 1: payment.authorized
    auth_payload = {
        "event": "payment.authorized",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "status": "authorized",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    b1 = json.dumps(auth_payload).encode("utf-8")
    sig1 = generate_webhook_signature(b1, razorpay_provider.webhook_secret)
    res1 = client.post("/api/payments/razorpay/webhook", content=b1, headers={"X-Razorpay-Signature": sig1})
    assert res1.status_code == 200

    # Step 2: order.paid
    order_payload = {
        "event": "order.paid",
        "payload": {
            "order": {
                "entity": {
                    "id": order_id,
                    "receipt": booking_ref,
                    "amount_paid": 1250000,
                    "currency": "INR",
                    "status": "paid",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    b2 = json.dumps(order_payload).encode("utf-8")
    sig2 = generate_webhook_signature(b2, razorpay_provider.webhook_secret)
    res2 = client.post("/api/payments/razorpay/webhook", content=b2, headers={"X-Razorpay-Signature": sig2})
    assert res2.status_code == 200
    assert res2.json()["data"]["status"] == "CONFIRMED"

    db.refresh(booking)
    assert booking.status == BookingStatus.CONFIRMED


def test_16_duplicate_payment_authorized_is_idempotent():
    """Sending payment.authorized twice for the same event must be idempotent."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id)

    payload = {
        "event": "payment.authorized",
        "event_id": event_id,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "status": "authorized",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret)
    headers = {"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id, "Content-Type": "application/json"}

    # Delivery 1
    res1 = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["data"]["status"] == "AUTHORIZED"

    # Delivery 2 (duplicate)
    res2 = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["data"]["status"] == "DUPLICATE_IGNORED"

    # Booking still PENDING
    db.refresh(booking)
    assert booking.status == BookingStatus.PENDING


# ──────────────────────────────────────────────────────────────────────────────
# Tests 17-22: Amount and Currency Reconciliation
# ──────────────────────────────────────────────────────────────────────────────

def test_17_amount_lower_than_booking_amount_rejected():
    """Webhook with amount lower than booking total_amount must be rejected with AMOUNT_MISMATCH."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id, amount=12500.0)

    # Underpayment: ₹1,000 instead of ₹12,500
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 100000,  # 1000.00 INR in paise
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret)
    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})

    assert res.status_code == 200
    assert res.json()["data"]["status"] == "AMOUNT_MISMATCH"

    # Booking and transaction remain unconfirmed
    db.refresh(booking)
    db.refresh(tx)
    assert booking.status == BookingStatus.PENDING
    assert tx.status == PaymentStatus.PENDING


def test_18_amount_higher_than_booking_amount_rejected():
    """Webhook with amount higher than booking total_amount must be rejected with AMOUNT_MISMATCH."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id, amount=12500.0)

    # Overpayment: ₹15,000 instead of ₹12,500
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1500000,  # 15000.00 INR in paise
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret)
    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})

    assert res.status_code == 200
    assert res.json()["data"]["status"] == "AMOUNT_MISMATCH"

    db.refresh(booking)
    db.refresh(tx)
    assert booking.status == BookingStatus.PENDING
    assert tx.status == PaymentStatus.PENDING


def test_19_currency_mismatch_rejected():
    """Webhook with mismatched currency (e.g. USD instead of INR) must be rejected with CURRENCY_MISMATCH."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id, amount=12500.0)

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "USD",  # Currency mismatch
                    "status": "captured",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret)
    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})

    assert res.status_code == 200
    assert res.json()["data"]["status"] == "CURRENCY_MISMATCH"

    db.refresh(booking)
    db.refresh(tx)
    assert booking.status == BookingStatus.PENDING
    assert tx.status == PaymentStatus.PENDING


def test_20_missing_amount_rejected():
    """Webhook with missing amount field must be rejected with PAYMENT_DATA_INCOMPLETE."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id, amount=12500.0)

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "currency": "INR",  # Missing amount
                    "status": "captured",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret)
    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})

    assert res.status_code == 200
    assert res.json()["data"]["status"] == "PAYMENT_DATA_INCOMPLETE"

    db.refresh(booking)
    assert booking.status == BookingStatus.PENDING


def test_21_missing_currency_rejected():
    """Webhook with missing currency field must be rejected with PAYMENT_DATA_INCOMPLETE."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id, amount=12500.0)

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,  # Missing currency
                    "status": "captured",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret)
    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})

    assert res.status_code == 200
    assert res.json()["data"]["status"] == "PAYMENT_DATA_INCOMPLETE"

    db.refresh(booking)
    assert booking.status == BookingStatus.PENDING


def test_22_order_paid_amount_and_currency_reconciliation():
    """order.paid webhook reconciles amount and currency and promotes booking to CONFIRMED."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id, amount=12500.0)

    payload = {
        "event": "order.paid",
        "payload": {
            "order": {
                "entity": {
                    "id": order_id,
                    "receipt": booking_ref,
                    "amount_paid": 1250000,
                    "currency": "INR",
                    "status": "paid",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_webhook_signature(body_bytes, razorpay_provider.webhook_secret)
    res = client.post("/api/payments/razorpay/webhook", content=body_bytes, headers={"X-Razorpay-Signature": sig})

    assert res.status_code == 200
    assert res.json()["data"]["status"] == "CONFIRMED"

    db.refresh(booking)
    db.refresh(tx)
    assert booking.status == BookingStatus.CONFIRMED
    assert tx.status == PaymentStatus.SUCCESSFUL


# ──────────────────────────────────────────────────────────────────────────────
# Tests 23-26: Refund Processing & Booking Cancellation
# ──────────────────────────────────────────────────────────────────────────────

def test_23_full_refund_cancels_booking_and_invoice():
    """Full refund webhook must mark transaction REFUNDED, invoice CANCELLED, and booking CANCELLED."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id, amount=12500.0)

    # 1. First confirm booking via payment.captured
    cap_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    b1 = json.dumps(cap_payload).encode("utf-8")
    sig1 = generate_webhook_signature(b1, razorpay_provider.webhook_secret)
    res1 = client.post("/api/payments/razorpay/webhook", content=b1, headers={"X-Razorpay-Signature": sig1})
    assert res1.status_code == 200
    assert res1.json()["data"]["status"] == "CONFIRMED"

    db.refresh(booking)
    db.refresh(tx)
    assert booking.status == BookingStatus.CONFIRMED
    assert tx.status == PaymentStatus.SUCCESSFUL

    # 2. Process full refund webhook
    refund_payload = {
        "event": "refund.processed",
        "payload": {
            "refund": {
                "entity": {
                    "id": f"rfnd_{uuid.uuid4().hex[:10]}",
                    "payment_id": payment_id,
                    "amount": 1250000,  # Full amount (₹12,500)
                    "currency": "INR",
                    "status": "processed",
                    "notes": {"booking_ref": booking_ref}
                }
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "amount_refunded": 1250000,
                    "refund_status": "full"
                }
            }
        }
    }
    b2 = json.dumps(refund_payload).encode("utf-8")
    sig2 = generate_webhook_signature(b2, razorpay_provider.webhook_secret)
    res2 = client.post("/api/payments/razorpay/webhook", content=b2, headers={"X-Razorpay-Signature": sig2})
    assert res2.status_code == 200
    assert res2.json()["data"]["status"] == "REFUNDED"

    # Verify final states
    db.refresh(booking)
    db.refresh(tx)
    assert booking.status == BookingStatus.CANCELLED
    assert tx.status == PaymentStatus.REFUNDED

    # Invoices cancelled
    invoices = list(db.scalars(select(Invoice).where(Invoice.transaction_id == tx.id)).all())
    assert len(invoices) >= 1
    for inv in invoices:
        assert inv.status == InvoiceStatus.CANCELLED


def test_24_partial_refund_does_not_cancel_booking():
    """Partial refund webhook must update transaction to PARTIALLY_REFUNDED but leave booking CONFIRMED."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id, amount=12500.0)

    # 1. Confirm booking
    cap_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"booking_ref": booking_ref, "channel": "web"}
                }
            }
        }
    }
    b1 = json.dumps(cap_payload).encode("utf-8")
    sig1 = generate_webhook_signature(b1, razorpay_provider.webhook_secret)
    client.post("/api/payments/razorpay/webhook", content=b1, headers={"X-Razorpay-Signature": sig1})

    db.refresh(booking)
    assert booking.status == BookingStatus.CONFIRMED

    # 2. Process partial refund webhook (₹2,500 of ₹12,500)
    partial_refund_payload = {
        "event": "refund.processed",
        "payload": {
            "refund": {
                "entity": {
                    "id": f"rfnd_{uuid.uuid4().hex[:10]}",
                    "payment_id": payment_id,
                    "amount": 250000,  # ₹2,500 partial refund
                    "currency": "INR",
                    "status": "processed",
                    "notes": {"booking_ref": booking_ref}
                }
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "amount_refunded": 250000,
                    "refund_status": "partial"
                }
            }
        }
    }
    b2 = json.dumps(partial_refund_payload).encode("utf-8")
    sig2 = generate_webhook_signature(b2, razorpay_provider.webhook_secret)
    res2 = client.post("/api/payments/razorpay/webhook", content=b2, headers={"X-Razorpay-Signature": sig2})
    assert res2.status_code == 200
    assert res2.json()["data"]["status"] == "PARTIALLY_REFUNDED"

    # Verify states: booking must NOT be cancelled
    db.refresh(booking)
    db.refresh(tx)
    assert booking.status == BookingStatus.CONFIRMED
    assert tx.status == PaymentStatus.PARTIALLY_REFUNDED

    # Invoice set to PARTIALLY_PAID
    invoices = list(db.scalars(select(Invoice).where(Invoice.transaction_id == tx.id)).all())
    for inv in invoices:
        assert inv.status == InvoiceStatus.PARTIALLY_PAID


def test_25_duplicate_refund_webhook_is_idempotent():
    """Duplicate refund webhook delivery must be idempotent."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_{uuid.uuid4().hex[:10]}"
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id, amount=12500.0)

    # Confirm
    cap_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "status": "captured",
                    "notes": {"booking_ref": booking_ref}
                }
            }
        }
    }
    b1 = json.dumps(cap_payload).encode("utf-8")
    sig1 = generate_webhook_signature(b1, razorpay_provider.webhook_secret)
    client.post("/api/payments/razorpay/webhook", content=b1, headers={"X-Razorpay-Signature": sig1})

    # Refund payload
    refund_payload = {
        "event": "refund.processed",
        "event_id": event_id,
        "payload": {
            "refund": {
                "entity": {
                    "id": f"rfnd_{uuid.uuid4().hex[:10]}",
                    "payment_id": payment_id,
                    "amount": 1250000,
                    "currency": "INR",
                    "status": "processed",
                    "notes": {"booking_ref": booking_ref}
                }
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 1250000,
                    "amount_refunded": 1250000,
                    "refund_status": "full"
                }
            }
        }
    }
    b2 = json.dumps(refund_payload).encode("utf-8")
    sig2 = generate_webhook_signature(b2, razorpay_provider.webhook_secret)
    headers = {"X-Razorpay-Signature": sig2, "X-Razorpay-Event-Id": event_id, "Content-Type": "application/json"}

    # Delivery 1
    res1 = client.post("/api/payments/razorpay/webhook", content=b2, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["data"]["status"] == "REFUNDED"

    # Delivery 2 (duplicate)
    res2 = client.post("/api/payments/razorpay/webhook", content=b2, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["data"]["status"] == "DUPLICATE_IGNORED"

    # Booking remains CANCELLED
    db.refresh(booking)
    assert booking.status == BookingStatus.CANCELLED


def test_26_full_refund_via_admin_api_cancels_booking():
    """Full refund via admin API endpoint must mark booking CANCELLED."""
    db = next(get_db())
    booking_ref = f"SHF-DEL-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_test_{uuid.uuid4().hex[:10]}"
    booking, tx = create_test_booking_and_tx(db, booking_ref, order_id, amount=8000.0)

    # Confirm booking & transaction
    booking.status = BookingStatus.CONFIRMED
    tx.status = PaymentStatus.SUCCESSFUL
    tx.gateway_payment_id = payment_id
    db.commit()

    # Admin refund API call
    admin_token = AuthService.create_access_token({"sub": "admin@shafsky.com", "user_id": str(uuid.uuid4()), "role": "SUPER_ADMIN"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    ref_res = client.post("/api/payments/refund", json={
        "transaction_id": str(tx.id),
        "amount": 8000.0,
        "reason": "Customer cancellation"
    }, headers=admin_headers)

    assert ref_res.status_code == 200
    assert ref_res.json()["data"]["status"] == "REFUNDED"

    db.refresh(booking)
    db.refresh(tx)
    assert booking.status == BookingStatus.CANCELLED
    assert tx.status == PaymentStatus.REFUNDED


