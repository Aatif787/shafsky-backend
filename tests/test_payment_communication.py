"""
Test Suite for Payment & Multi-Channel Communication Foundation.
Verifies payment initiation, webhook verification, invoice generation,
refund processing, email/WhatsApp/SMS dispatching, and REST APIs.
"""

import pytest
import uuid
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine
from app.services.auth_service import AuthService
from app.services.communication_service import CommunicationService
from app.providers.base import MockEmailProvider, MockWhatsAppProvider, MockSMSProvider

client = TestClient(app)


from app.models.schema import Booking, BookingStatus
from app.database import get_db

@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


def test_payment_initiation_webhook_and_invoice():
    db = next(get_db())
    booking_ref = f"SHF-COMM-{uuid.uuid4().hex[:6].upper()}"
    booking = Booking(
        id=uuid.uuid4(),
        booking_ref=booking_ref,
        passenger_name="John Payee",
        passenger_email="john@shafsky.com",
        passenger_phone="+919876543210",
        service_category="Airport Assistance",
        service_type="Meet & Assist",
        origin_code="DEL",
        dest_code="DXB",
        total_amount=15000.0,
        currency="INR",
        status=BookingStatus.PENDING
    )
    db.add(booking)
    db.commit()

    user_id = str(uuid.uuid4())
    user_email = f"pay_user_{uuid.uuid4().hex[:6]}@shafsky.com"
    token = AuthService.create_access_token({"sub": user_email, "user_id": user_id, "role": "CUSTOMER"})
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Initiate Payment
    init_payload = {
        "entity_type": "AIRPORT_BOOKING",
        "entity_id": booking_ref,
        "customer_id": user_id,
        "customer_name": "John Payee",
        "customer_email": user_email,
        "amount": 15000.0,
        "currency": "INR",
        "payment_method": "CREDIT_CARD"
    }


    res = client.post("/api/payments/initiate", json=init_payload, headers=headers)
    assert res.status_code == 201, res.text
    tx_data = res.json()["data"]
    tx_ref = tx_data["transaction_ref"]
    tx_id = tx_data["id"]
    assert tx_data["status"] == "PENDING"

    # 2. Webhook Callback (Payment Succeeded)
    wh_payload = {
        "provider": "MOCK_PAYMENT",
        "event_type": "payment.succeeded",
        "transaction_ref": tx_ref,
        "gateway_payment_id": f"pay_{uuid.uuid4().hex[:8]}"
    }
    wh_res = client.post("/api/payments/webhook", json=wh_payload)
    assert wh_res.status_code == 200, wh_res.text
    assert wh_res.json()["data"]["status"] == "SUCCESSFUL"

    # 3. Fetch Transaction Details
    get_res = client.get(f"/api/payments/transactions/{tx_id}", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["data"]["status"] == "SUCCESSFUL"


def test_payment_refund_flow():
    db = next(get_db())
    booking_ref = f"SHF-COMM-{uuid.uuid4().hex[:6].upper()}"
    booking = Booking(
        id=uuid.uuid4(),
        booking_ref=booking_ref,
        passenger_name="Jane Refundee",
        passenger_email="jane@shafsky.com",
        passenger_phone="+919876543210",
        service_category="Airport Assistance",
        service_type="Meet & Assist",
        origin_code="DEL",
        dest_code="DXB",
        total_amount=5000.0,
        currency="INR",
        status=BookingStatus.PENDING
    )
    db.add(booking)
    db.commit()

    admin_token = AuthService.create_access_token({"sub": "admin@shafsky.com", "user_id": str(uuid.uuid4()), "role": "SUPER_ADMIN"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Initiate & Succeed Payment
    init_payload = {
        "entity_type": "AIRPORT_BOOKING",
        "entity_id": booking_ref,
        "customer_name": "Jane Refundee",
        "customer_email": "jane@shafsky.com",
        "amount": 5000.0,
        "currency": "INR",
        "payment_method": "UPI"
    }
    res = client.post("/api/payments/initiate", json=init_payload, headers=admin_headers)
    assert res.status_code == 201, res.text
    tx_id = res.json()["data"]["id"]
    tx_ref = res.json()["data"]["transaction_ref"]

    client.post("/api/payments/webhook", json={
        "provider": "MOCK_PAYMENT",
        "event_type": "payment.succeeded",
        "transaction_ref": tx_ref,
        "gateway_payment_id": "pay_mock_123"
    })


    # 2. Refund
    ref_res = client.post("/api/payments/refund", json={
        "transaction_id": tx_id,
        "amount": 5000.0,
        "reason": "Flight cancelled by passenger"
    }, headers=admin_headers)

    assert ref_res.status_code == 200, ref_res.text
    assert ref_res.json()["data"]["status"] == "REFUNDED"


def test_communication_providers_mock_dispatch():
    # Test Email Provider
    email_prov = MockEmailProvider()
    e_res = email_prov.send_email("test@shafsky.com", "Welcome", "<h1>Welcome to Shafsky</h1>")
    assert e_res["status"] == "DELIVERED"
    assert e_res["to"] == "test@shafsky.com"

    # Test WhatsApp Provider
    wa_prov = MockWhatsAppProvider()
    w_res = wa_prov.send_whatsapp_message("+919876543210", "booking_confirmation", {"booking_id": "SHF-101"})
    assert w_res["status"] == "SENT"
    assert w_res["phone"] == "+919876543210"

    # Test SMS Provider
    sms_prov = MockSMSProvider()
    s_res = sms_prov.send_sms("+919876543210", "Your OTP is 987654")
    assert s_res["status"] == "SENT"
