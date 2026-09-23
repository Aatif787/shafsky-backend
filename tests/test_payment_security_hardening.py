"""Security hardening tests for payment session tokens, fail-closed webhooks, gateway freeze."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from app.security.payment_token import (
    allow_payment_simulation,
    mint_payment_token,
    verify_payment_token,
)
from app.services.payment_service import PaymentService
from app.schemas.payment import WebhookPayload


def test_payment_token_roundtrip():
    token = mint_payment_token("SF-TEST-001")
    assert verify_payment_token(token, booking_ref="SF-TEST-001")
    assert not verify_payment_token(token, booking_ref="SF-OTHER")
    assert not verify_payment_token("tampered." + token.split(".", 1)[-1], booking_ref="SF-TEST-001")
    assert not verify_payment_token(None, booking_ref="SF-TEST-001")


def test_internal_webhook_never_fail_open():
    payload = WebhookPayload(
        provider="MOCK",
        event_type="payment.succeeded",
        transaction_ref="PAY-X",
        gateway_payment_id="pay_x",
    )
    with patch.dict(os.environ, {"PAYMENT_WEBHOOK_SECRET": "", "RAZORPAY_WEBHOOK_SECRET": ""}, clear=False):
        assert PaymentService.verify_internal_webhook_signature(payload, None) is False
        assert PaymentService.verify_internal_webhook_signature(payload, "anything") is False


def test_razorpay_simulated_signature_requires_explicit_flag(monkeypatch):
    from app.providers.razorpay_provider import RazorpayProvider

    monkeypatch.setenv("RAZORPAY_KEY_ID", "")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "")
    provider = RazorpayProvider()
    monkeypatch.setenv("ALLOW_PAYMENT_SIMULATION", "false")
    assert (
        provider.verify_payment_signature("order_x", "pay_x", "simulated_signature") is False
    )
    monkeypatch.setenv("ALLOW_PAYMENT_SIMULATION", "true")
    assert (
        provider.verify_payment_signature("order_x", "pay_x", "simulated_signature") is True
    )


def test_assert_gateway_exclusive_blocks_dual(monkeypatch):
    booking = MagicMock()
    booking.booking_ref = "SF-DUAL-1"
    booking.id = "00000000-0000-0000-0000-000000000001"

    frozen_tx = MagicMock()
    frozen_tx.gateway_provider = "ICICI"

    db = MagicMock()
    db.scalar.return_value = frozen_tx

    with patch.object(PaymentService, "active_payment_gateway", return_value="RAZORPAY"):
        with pytest.raises(ValueError, match="ICICI"):
            PaymentService.assert_gateway_exclusive(db, booking, "RAZORPAY")


def test_allow_payment_simulation_false_in_production(monkeypatch):
    monkeypatch.setenv("ALLOW_PAYMENT_SIMULATION", "true")
    with patch("app.security.payment_token.settings") as settings_mock:
        settings_mock.is_production = True
        assert allow_payment_simulation() is False
