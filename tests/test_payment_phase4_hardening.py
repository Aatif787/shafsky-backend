"""
Phase 4 Automated Test Suite: Production Payment Hardening, Recovery, Refunds,
Reconciliation, State Machines, Double Payment Protection, and Security.
"""

import pytest
import hmac
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.database import Base, engine, get_db, SessionLocal
from app.models.payment import PaymentTransaction, Invoice, Refund, PaymentWebhookEvent, PaymentStatus, InvoiceStatus, PaymentMethod, PaymentStateMachine
from app.models.schema import Booking, BookingStatus
from app.services.auth_service import AuthService
from app.services.payment_service import PaymentService
from app.providers.razorpay_provider import razorpay_provider

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


TestingSessionLocal = SessionLocal


def generate_payment_signature(order_id: str, payment_id: str) -> str:
    if not getattr(razorpay_provider, "key_secret", None):
        return "simulated_signature"
    msg = f"{order_id}|{payment_id}".encode("utf-8")
    return hmac.new(razorpay_provider.key_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def get_test_db():
    db = TestingSessionLocal()
    try:
        return db
    except Exception:
        db.close()
        raise


def create_mock_admin_token() -> str:
    return AuthService.create_access_token(
        data={
            "sub": "admin@shafsky.com",
            "email": "admin@shafsky.com",
            "role": "SUPER_ADMIN",
            "user_id": str(uuid.uuid4())
        }
    )


def create_mock_customer_token(email: str = "customer@shafsky.com", user_id: str = None) -> str:
    return AuthService.create_access_token(
        data={
            "sub": email,
            "email": email,
            "role": "USER",
            "user_id": user_id or str(uuid.uuid4())
        }
    )


# ==============================================================================
# 1. State Machine Transition Hardening
# ==============================================================================

def test_payment_state_machine_transitions():
    """Verify all valid and invalid state transitions governed by PaymentStateMachine."""
    # Legal transitions
    assert PaymentStateMachine.can_transition(PaymentStatus.PENDING, PaymentStatus.PROCESSING)
    assert PaymentStateMachine.can_transition(PaymentStatus.PENDING, PaymentStatus.SUCCESSFUL)
    assert PaymentStateMachine.can_transition(PaymentStatus.PENDING, PaymentStatus.FAILED)
    assert PaymentStateMachine.can_transition(PaymentStatus.PENDING, PaymentStatus.CANCELLED)
    assert PaymentStateMachine.can_transition(PaymentStatus.PENDING, PaymentStatus.EXPIRED)
    assert PaymentStateMachine.can_transition(PaymentStatus.SUCCESSFUL, PaymentStatus.REFUND_PENDING)
    assert PaymentStateMachine.can_transition(PaymentStatus.SUCCESSFUL, PaymentStatus.PARTIALLY_REFUNDED)
    assert PaymentStateMachine.can_transition(PaymentStatus.SUCCESSFUL, PaymentStatus.REFUNDED)
    assert PaymentStateMachine.can_transition(PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED)

    # Illegal transitions
    assert not PaymentStateMachine.can_transition(PaymentStatus.SUCCESSFUL, PaymentStatus.PENDING)
    assert not PaymentStateMachine.can_transition(PaymentStatus.REFUNDED, PaymentStatus.SUCCESSFUL)
    assert not PaymentStateMachine.can_transition(PaymentStatus.FAILED, PaymentStatus.SUCCESSFUL)
    assert not PaymentStateMachine.can_transition(PaymentStatus.EXPIRED, PaymentStatus.SUCCESSFUL)

    with pytest.raises(ValueError, match="Illegal payment status transition"):
        PaymentStateMachine.validate_transition(PaymentStatus.SUCCESSFUL, PaymentStatus.PENDING)


# ==============================================================================
# 2. End-to-End Booking Creation, Payment Verification, and Invoicing
# ==============================================================================

def test_end_to_end_booking_payment_and_verification():
    """Verify complete lifecycle: Booking init -> Razorpay order -> Signature verify -> Confirmation & Invoice."""
    db = TestingSessionLocal()
    booking_ref = f"SHF-E2E-{uuid.uuid4().hex[:6].upper()}"
    order_id = f"order_e2e_{uuid.uuid4().hex[:8]}"
    payment_id = f"pay_test_{uuid.uuid4().hex[:8]}"
    sig = generate_payment_signature(order_id, payment_id)

    booking = Booking(
        id=uuid.uuid4(),
        booking_ref=booking_ref,
        passenger_name="Alice Walker",
        passenger_email="alice@example.com",
        passenger_phone="+919876543210",
        service_category="Airport Assistance",
        service_type="Meet & Assist",
        origin_code="DEL",
        dest_code="BOM",
        metadata_json={"package": "Silver Assist", "service_airport": "DEL"},
        total_amount=4500.0,
        currency="INR",
        status=BookingStatus.PENDING
    )
    db.add(booking)

    tx = PaymentTransaction(
        id=uuid.uuid4(),
        transaction_ref=booking_ref,
        entity_type="AIRPORT_BOOKING",
        entity_id=booking_ref,
        customer_id=None,
        amount=4500.0,
        currency="INR",
        gateway_provider="RAZORPAY",
        gateway_payment_id=order_id,
        status=PaymentStatus.PENDING
    )
    db.add(tx)
    db.commit()

    # Step 2: Verify payment via POST /api/payments/verify
    verify_payload = {
        "razorpay_order_id": order_id,
        "razorpay_payment_id": payment_id,
        "razorpay_signature": sig,
        "booking_ref": booking_ref
    }

    v_res = client.post("/api/payments/verify", json=verify_payload)
    assert v_res.status_code == 200
    assert v_res.json()["data"]["status"] == "CONFIRMED"

    # Step 3: Check database state
    db.close()
    db = TestingSessionLocal()
    booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
    assert booking.status == BookingStatus.CONFIRMED

    tx = db.scalar(select(PaymentTransaction).where(PaymentTransaction.entity_id == booking_ref))
    assert tx.status == PaymentStatus.SUCCESSFUL
    assert tx.gateway_payment_id == payment_id

    invoice = db.scalar(select(Invoice).where(Invoice.transaction_id == tx.id))
    assert invoice is not None
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.total_amount == 4500.0
    db.close()


# ==============================================================================
# 3. Double Payment & Overpayment Protection
# ==============================================================================

def test_double_payment_protection():
    """Verify distinct second payment for already confirmed booking is flagged and preserved without corrupting original."""
    db = TestingSessionLocal()
    booking_ref = f"SHF-DBL-{uuid.uuid4().hex[:6].upper()}"
    booking = Booking(
        id=uuid.uuid4(),
        booking_ref=booking_ref,
        passenger_name="Bob Evans",
        passenger_email="bob@example.com",
        passenger_phone="+919876543210",
        service_category="Airport Assistance",
        service_type="Meet & Assist",
        total_amount=5000.0,
        currency="INR",
        status=BookingStatus.PENDING
    )
    db.add(booking)

    tx1 = PaymentTransaction(
        transaction_ref=f"PAY-TX-{uuid.uuid4().hex[:6].upper()}",
        entity_type="AIRPORT_BOOKING",
        entity_id=booking_ref,
        amount=5000.0,
        currency="INR",
        payment_method=PaymentMethod.CREDIT_CARD,
        status=PaymentStatus.PENDING,
        gateway_provider="RAZORPAY",
        gateway_payment_id="order_initial_001"
    )
    db.add(tx1)
    db.commit()

    # 1st Payment Arrives
    res1 = PaymentService.handle_verified_payment(
        db,
        event_name="payment.captured",
        gateway_provider="RAZORPAY",
        order_id="order_initial_001",
        payment_id="pay_first_111",
        booking_ref=booking_ref
    )
    assert res1["status"] == "CONFIRMED"

    db.close()
    db = TestingSessionLocal()

    # 2nd Distinct Payment Arrives for the same booking
    res2 = PaymentService.handle_verified_payment(
        db,
        event_name="payment.captured",
        gateway_provider="RAZORPAY",
        order_id="order_second_002",
        payment_id="pay_second_222",
        booking_ref=booking_ref
    )
    print("DEBUG_RES2_OUTPUT:", res2)
    assert res2["status"] == "DUPLICATE_PAYMENT_FLAGGED", f"Unexpected res2: {res2}"
    assert "duplicate_tx_ref" in res2

    # Check transactions in DB
    db.expire_all()
    txs = list(db.scalars(select(PaymentTransaction).where(PaymentTransaction.entity_id == booking_ref)).all())
    assert len(txs) == 2

    dup_tx = [t for t in txs if t.is_duplicate][0]
    assert dup_tx.gateway_payment_id == "pay_second_222"
    assert "DUPLICATE_PAYMENT_DETECTED" in dup_tx.notes

    # Verify original booking remained unaffected
    confirmed_booking = db.scalar(select(Booking).where(Booking.booking_ref == booking_ref))
    assert confirmed_booking is not None
    assert confirmed_booking.status == BookingStatus.CONFIRMED
    assert confirmed_booking.total_amount == 5000.0
    db.close()



# ==============================================================================
# 4. Payment Retry Mechanism
# ==============================================================================

def test_payment_retry_on_pending_booking():
    """Verify customer can retry payment on pending booking without duplicating booking."""
    db = TestingSessionLocal()
    booking_ref = f"SHF-RTRY-{uuid.uuid4().hex[:6].upper()}"
    booking = Booking(
        id=uuid.uuid4(),
        booking_ref=booking_ref,
        passenger_name="Charlie Brown",
        passenger_email="charlie@example.com",
        passenger_phone="+919876543210",
        service_category="Airport Assistance",
        service_type="Meet & Assist",
        total_amount=3200.0,
        currency="INR",
        status=BookingStatus.PENDING
    )
    db.add(booking)
    db.commit()

    # Call Retry Endpoint
    res = client.post("/api/payments/retry", json={"booking_ref": booking_ref})
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["bookingRef"] == booking_ref
    assert data["totalAmount"] == 3200.0
    assert data["razorpay_order_id"].startswith("order_")

    # Complete payment on the retried order
    v_res = client.post("/api/payments/verify", json={
        "razorpay_order_id": data["razorpay_order_id"],
        "razorpay_payment_id": "pay_retry_success_999",
        "razorpay_signature": generate_payment_signature(data["razorpay_order_id"], "pay_retry_success_999"),
        "booking_ref": booking_ref
    })
    assert v_res.status_code == 200
    assert v_res.json()["data"]["status"] == "CONFIRMED"

    # Ensure booking is confirmed
    db.refresh(booking)
    assert booking.status == BookingStatus.CONFIRMED
    db.close()


def test_payment_retry_rejected_on_confirmed_booking():
    """Verify attempting to retry payment on already confirmed booking is rejected."""
    db = TestingSessionLocal()
    booking_ref = f"SHF-CONF-{uuid.uuid4().hex[:6].upper()}"
    booking = Booking(
        id=uuid.uuid4(),
        booking_ref=booking_ref,
        passenger_name="Diana Prince",
        passenger_email="diana@example.com",
        passenger_phone="+919876543210",
        service_category="Airport Assistance",
        service_type="Meet & Assist",
        total_amount=6000.0,
        currency="INR",
        status=BookingStatus.CONFIRMED
    )
    db.add(booking)
    db.commit()

    res = client.post("/api/payments/retry", json={"booking_ref": booking_ref})
    assert res.status_code == 400
    assert "already confirmed" in res.json()["detail"]
    db.close()


# ==============================================================================
# 5. Production Refund System
# ==============================================================================

def test_refund_success_and_invoice_cancellation():
    """Verify refund processes through provider, updates partial vs full status, and manages invoice."""
    db = TestingSessionLocal()
    tx = PaymentTransaction(
        id=uuid.uuid4(),
        transaction_ref=f"PAY-REF-{uuid.uuid4().hex[:6].upper()}",
        entity_type="AIRPORT_BOOKING",
        entity_id=f"SHF-REFUND-{uuid.uuid4().hex[:6].upper()}",
        amount=10000.0,
        currency="INR",
        payment_method=PaymentMethod.CREDIT_CARD,
        status=PaymentStatus.SUCCESSFUL,
        gateway_provider="RAZORPAY",
        gateway_payment_id="pay_ref_target_001"
    )
    db.add(tx)
    db.commit()

    inv = PaymentService.generate_invoice(db, tx, "Diana Prince", "diana@example.com")
    db.commit()
    assert inv.status == InvoiceStatus.PAID

    admin_token = create_mock_admin_token()

    # 1. Process Partial Refund of ₹4000
    p_refund_res = client.post(
        "/api/payments/refund",
        json={"transaction_id": str(tx.id), "amount": 4000.0, "reason": "Partial service adjustment"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert p_refund_res.status_code == 200
    db.refresh(tx)
    assert tx.status == PaymentStatus.PARTIALLY_REFUNDED
    db.refresh(inv)
    assert inv.status == InvoiceStatus.PARTIALLY_PAID

    # 2. Process Remaining ₹6000 (Full Refund)
    f_refund_res = client.post(
        "/api/payments/refund",
        json={"transaction_id": str(tx.id), "amount": 6000.0, "reason": "Full cancellation balance"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert f_refund_res.status_code == 200
    db.refresh(tx)
    assert tx.status == PaymentStatus.REFUNDED
    db.refresh(inv)
    assert inv.status == InvoiceStatus.CANCELLED
    db.close()


def test_refund_exceeding_paid_amount_rejected():
    """Verify refund amount exceeding paid balance returns 400 Bad Request."""
    db = TestingSessionLocal()
    tx = PaymentTransaction(
        id=uuid.uuid4(),
        transaction_ref=f"PAY-OVER-{uuid.uuid4().hex[:6].upper()}",
        entity_type="AIRPORT_BOOKING",
        entity_id=f"SHF-OVER-{uuid.uuid4().hex[:6].upper()}",
        amount=2000.0,
        currency="INR",
        payment_method=PaymentMethod.CREDIT_CARD,
        status=PaymentStatus.SUCCESSFUL,
        gateway_provider="RAZORPAY",
        gateway_payment_id="pay_over_target_001"
    )
    db.add(tx)
    db.commit()

    admin_token = create_mock_admin_token()
    res = client.post(
        "/api/payments/refund",
        json={"transaction_id": str(tx.id), "amount": 2500.0, "reason": "Exceeding refund"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res.status_code == 400
    assert "exceeds available refundable balance" in res.json()["detail"]
    db.close()


# ==============================================================================
# 6. Payment Reconciliation Engine & Anomaly Detection
# ==============================================================================

def test_reconciliation_anomaly_detection():
    """Verify reconciliation engine flags amount mismatch, unconfirmed payments, and duplicate payments."""
    db = TestingSessionLocal()
    
    ref_ok = f"SHF-REC-OK-{uuid.uuid4().hex[:6].upper()}"
    ref_amt = f"SHF-REC-AMT-{uuid.uuid4().hex[:6].upper()}"
    ref_pend = f"SHF-REC-PEND-{uuid.uuid4().hex[:6].upper()}"
    ref_unpaid = f"SHF-REC-UNP-{uuid.uuid4().hex[:6].upper()}"

    # 1. Normal Booking + Payment
    b1 = Booking(id=uuid.uuid4(), booking_ref=ref_ok, passenger_name="User OK", passenger_email="ok@shafsky.com", passenger_phone="+919876543210", service_category="Airport Assistance", service_type="Meet & Assist", total_amount=1000.0, currency="INR", status=BookingStatus.CONFIRMED)
    tx1 = PaymentTransaction(transaction_ref=f"TX-OK-{uuid.uuid4().hex[:6]}", entity_type="AIRPORT_BOOKING", entity_id=ref_ok, amount=1000.0, currency="INR", status=PaymentStatus.SUCCESSFUL)

    # 2. Amount Mismatch
    b2 = Booking(id=uuid.uuid4(), booking_ref=ref_amt, passenger_name="User Amt", passenger_email="amt@shafsky.com", passenger_phone="+919876543210", service_category="Airport Assistance", service_type="Meet & Assist", total_amount=2000.0, currency="INR", status=BookingStatus.CONFIRMED)
    tx2 = PaymentTransaction(transaction_ref=f"TX-AMT-{uuid.uuid4().hex[:6]}", entity_type="AIRPORT_BOOKING", entity_id=ref_amt, amount=1500.0, currency="INR", status=PaymentStatus.SUCCESSFUL)

    # 3. Paid but Booking is PENDING
    b3 = Booking(id=uuid.uuid4(), booking_ref=ref_pend, passenger_name="User Pend", passenger_email="pend@shafsky.com", passenger_phone="+919876543210", service_category="Airport Assistance", service_type="Meet & Assist", total_amount=3000.0, currency="INR", status=BookingStatus.PENDING)
    tx3 = PaymentTransaction(transaction_ref=f"TX-PEND-{uuid.uuid4().hex[:6]}", entity_type="AIRPORT_BOOKING", entity_id=ref_pend, amount=3000.0, currency="INR", status=PaymentStatus.SUCCESSFUL)

    # 4. Confirmed without any Successful Payment
    b4 = Booking(id=uuid.uuid4(), booking_ref=ref_unpaid, passenger_name="User Unpaid", passenger_email="unpaid@shafsky.com", passenger_phone="+919876543210", service_category="Airport Assistance", service_type="Meet & Assist", total_amount=4000.0, currency="INR", status=BookingStatus.CONFIRMED)

    db.add_all([b1, tx1, b2, tx2, b3, tx3, b4])
    db.commit()

    admin_token = create_mock_admin_token()
    res = client.get("/api/payments/admin/reconciliation", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    report = res.json()["data"]

    anomalies = report["anomalies"]
    types = {a["type"] for a in anomalies}
    assert "AMOUNT_MISMATCH" in types
    assert "PAID_WITHOUT_CONFIRMATION" in types
    assert "CONFIRMED_WITHOUT_PAYMENT" in types
    db.close()


# ==============================================================================
# 7. Stale Order Expiry & Gateway Sync
# ==============================================================================

def test_stale_order_expiry():
    """Verify transactions pending for > 24 hours are marked EXPIRED."""
    db = TestingSessionLocal()
    stale_time = datetime.now(timezone.utc) - timedelta(hours=30)
    ref_old = f"SHF-OLD-{uuid.uuid4().hex[:6].upper()}"
    ref_new = f"SHF-NEW-{uuid.uuid4().hex[:6].upper()}"

    tx_stale = PaymentTransaction(
        transaction_ref=f"TX-OLD-{uuid.uuid4().hex[:6]}",
        entity_type="AIRPORT_BOOKING",
        entity_id=ref_old,
        amount=1200.0,
        currency="INR",
        status=PaymentStatus.PENDING,
        created_at=stale_time
    )
    tx_recent = PaymentTransaction(
        transaction_ref=f"TX-NEW-{uuid.uuid4().hex[:6]}",
        entity_type="AIRPORT_BOOKING",
        entity_id=ref_new,
        amount=1200.0,
        currency="INR",
        status=PaymentStatus.PENDING,
        created_at=datetime.now(timezone.utc)
    )
    db.add_all([tx_stale, tx_recent])
    db.commit()

    admin_token = create_mock_admin_token()
    res = client.post("/api/payments/admin/expire-stale?max_age_hours=24", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    assert res.json()["data"]["expired_count"] >= 1

    db.refresh(tx_stale)
    db.refresh(tx_recent)
    assert tx_stale.status == PaymentStatus.EXPIRED
    assert tx_recent.status == PaymentStatus.PENDING
    db.close()


# ==============================================================================
# 8. Security & Legacy Route Neutralization
# ==============================================================================

def test_legacy_confirm_endpoint_blocked():
    """Verify legacy insecure /api/payments/confirm is neutralized and returns 403 Forbidden."""
    user_token = create_mock_customer_token()
    res = client.post(
        "/api/payments/confirm",
        json={"bookingId": str(uuid.uuid4()), "amount": 100.0, "transactionId": "fake_tx"},
        headers={"Authorization": f"Bearer {user_token}"}
    )
    assert res.status_code == 403
    assert "Direct client payment confirmation is disabled" in res.json()["detail"]


def test_customer_payment_history_isolation():
    """Verify Customer A cannot view Customer B's payment transactions."""
    db = TestingSessionLocal()
    user_a_id = str(uuid.uuid4())
    user_b_id = str(uuid.uuid4())
    ref_a = f"TX-USER-A-{uuid.uuid4().hex[:6]}"
    ref_b = f"TX-USER-B-{uuid.uuid4().hex[:6]}"

    tx_a = PaymentTransaction(
        transaction_ref=ref_a,
        entity_type="AIRPORT_BOOKING",
        entity_id=f"SHF-UA-{uuid.uuid4().hex[:6]}",
        customer_id=user_a_id,
        amount=5000.0,
        currency="INR",
        status=PaymentStatus.SUCCESSFUL
    )
    tx_b = PaymentTransaction(
        transaction_ref=ref_b,
        entity_type="AIRPORT_BOOKING",
        entity_id=f"SHF-UB-{uuid.uuid4().hex[:6]}",
        customer_id=user_b_id,
        amount=9000.0,
        currency="INR",
        status=PaymentStatus.SUCCESSFUL
    )
    db.add_all([tx_a, tx_b])
    db.commit()

    token_a = create_mock_customer_token(email="user_a@shafsky.com", user_id=user_a_id)
    res = client.post("/api/payments/customer-history", json={}, headers={"Authorization": f"Bearer {token_a}"})
    assert res.status_code == 200
    history = res.json()["data"]
    tx_refs = [h["transaction_ref"] for h in history]
    assert ref_a in tx_refs
    assert ref_b not in tx_refs
    db.close()


def test_admin_notification_retry_endpoint():
    """Verify admin can trigger notification re-dispatch for a confirmed booking."""
    db = TestingSessionLocal()
    booking_ref = f"SHF-NOTIF-{uuid.uuid4().hex[:6].upper()}"
    booking = Booking(
        id=uuid.uuid4(),
        booking_ref=booking_ref,
        passenger_name="Eve Adams",
        passenger_email="eve@example.com",
        passenger_phone="+919876543210",
        service_category="Airport Assistance",
        service_type="Meet & Assist",
        total_amount=4000.0,
        currency="INR",
        status=BookingStatus.CONFIRMED
    )
    db.add(booking)
    db.commit()

    admin_token = create_mock_admin_token()
    res = client.post(f"/api/payments/admin/notifications/retry/{booking_ref}", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    assert "Notifications queued" in res.json()["data"]["message"]
    db.close()
