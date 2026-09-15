"""
Comprehensive Test Suite for Razorpay Standard Web Checkout Integration.

Validates:
1. Create Order endpoint (/api/create-order and /api/payments/create-order)
2. Amount validation (minimum 100 paise)
3. Return payload structure ({ order_id, amount, currency, key_id })
4. Signature verification (/api/verify-payment and /api/payments/verify)
5. Algorithm check: HMAC-SHA256(order_id + "|" + payment_id, KEY_SECRET)
6. Invalid signature rejection (HTTP 400)
7. Missing fields rejection (HTTP 400)
8. Secret key confidentiality (KEY_SECRET never leaked to client)
"""
import hmac
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from app.main import app
from app.providers.razorpay_provider import razorpay_provider

client = TestClient(app)


def _pending_catalog_booking():
    """Create a real pending booking so checkout can bind to an authoritative amount."""
    dep = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    arr = (datetime.now(timezone.utc) + timedelta(days=3, hours=2)).isoformat()
    res = client.post(
        "/api/bookings",
        json={
            "passenger_name": "Checkout Guest",
            "passenger_email": "checkout.guest@shafskyaviation.com",
            "passenger_phone": "+919876543210",
            "flight_num": "AI101",
            "origin_code": "BOM",
            "dest_code": "DEL",
            "departure_time": dep,
            "arrival_time": arr,
            "service_type": "silver",
            "total_amount": 3000.0,
            "currency": "INR",
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    paise = int(round(float(data["totalAmount"]) * 100))
    return data["bookingRef"], paise


def test_razorpay_credentials_loaded():
    """Verify Razorpay Key ID and Secret are loaded in the environment."""
    razorpay_provider._load_config()
    assert razorpay_provider.key_id != ""
    assert razorpay_provider.key_id.startswith("rzp_test_")
    assert razorpay_provider.key_secret != ""


def test_create_order_minimum_amount_validation():
    """Verify create-order rejects amounts below 100 paise."""
    res = client.post("/api/create-order", json={"amount": 50, "currency": "INR"})
    assert res.status_code == 400
    assert "100 paise" in res.json().get("detail", "")


def test_create_order_success():
    """Verify create-order successfully generates an order for a real booking."""
    booking_ref, paise = _pending_catalog_booking()
    payload = {
        "amount": paise,
        "currency": "INR",
        "receipt": booking_ref,
        "notes": {"service": "Meet and Greet"},
    }
    res = client.post("/api/create-order", json=payload)
    assert res.status_code == 201, res.text
    data = res.json()
    assert "order_id" in data
    assert data["order_id"].startswith("order_")
    assert data["amount"] == paise
    assert data["currency"] == "INR"
    assert data["key_id"] == razorpay_provider.key_id


def test_create_order_via_payments_prefix():
    """Verify create-order also works on /api/payments/create-order."""
    booking_ref, paise = _pending_catalog_booking()
    payload = {
        "amount": paise,
        "currency": "INR",
        "receipt": booking_ref,
    }
    res = client.post("/api/payments/create-order", json=payload)
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["amount"] == paise
    assert data["order_id"].startswith("order_")


def test_verify_signature_success():
    """Verify valid HMAC-SHA256 signature is accepted for a real pending order."""
    booking_ref, paise = _pending_catalog_booking()
    order_res = client.post(
        "/api/create-order",
        json={"amount": paise, "currency": "INR", "receipt": booking_ref},
    )
    assert order_res.status_code == 201, order_res.text
    order_id = order_res.json()["order_id"]
    payment_id = f"pay_test_{uuid.uuid4().hex[:12]}"
    key_secret = razorpay_provider.key_secret

    msg = f"{order_id}|{payment_id}".encode("utf-8")
    valid_sig = hmac.new(key_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    payload = {
        "razorpay_order_id": order_id,
        "razorpay_payment_id": payment_id,
        "razorpay_signature": valid_sig,
    }

    res = client.post("/api/verify-payment", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["success"] is True
    assert data["data"]["order_id"] == order_id
    assert data["data"]["payment_id"] == payment_id


def test_verify_signature_failure_tampered():
    """Verify tampered/invalid signature is rejected with HTTP 400."""
    payload = {
        "razorpay_order_id": "order_test_abc123",
        "razorpay_payment_id": f"pay_test_{uuid.uuid4().hex[:12]}",
        "razorpay_signature": "invalid_tampered_signature_123456789"
    }
    res = client.post("/api/verify-payment", json=payload)
    assert res.status_code == 400
    assert "Invalid Razorpay payment signature" in res.json().get("detail", "")


def test_verify_signature_missing_fields():
    """Verify missing fields are rejected with HTTP 400/422."""
    res = client.post("/api/verify-payment", json={"razorpay_order_id": "order_123"})
    assert res.status_code in (400, 422)


def test_secret_key_never_leaked():
    """Verify KEY_SECRET is never returned in any response."""
    res_order = client.post("/api/create-order", json={"amount": 10000, "currency": "INR"})
    assert razorpay_provider.key_secret not in res_order.text

    order_id = "order_leak_check"
    payment_id = "pay_leak_check"
    msg = f"{order_id}|{payment_id}".encode("utf-8")
    sig = hmac.new(razorpay_provider.key_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    res_verify = client.post("/api/verify-payment", json={
        "razorpay_order_id": order_id,
        "razorpay_payment_id": payment_id,
        "razorpay_signature": sig
    })
    assert razorpay_provider.key_secret not in res_verify.text
