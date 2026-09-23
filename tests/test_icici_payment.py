"""Unit tests for ICICI Bank TSP hashing and provider helpers (mocked network)."""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import MagicMock, patch

import pytest

from app.providers.icici_hash import (
    attach_secure_hash_v1,
    build_v1_hash_payload,
    generate_secure_hash_v1,
    generate_secure_hash_v2,
    verify_secure_hash_v1,
)
from app.providers.icici_provider import (
    IciciPaymentProvider,
    format_icici_amount,
    format_icici_txn_date,
    generate_merchant_txn_no,
)


SAMPLE_KEY = "unit-test-icici-secret-key-not-for-production"


def test_merchant_txn_no_constraints():
    txn = generate_merchant_txn_no("SF")
    assert txn.isalnum()
    assert len(txn) <= 20
    assert txn.startswith("SF")


def test_amount_two_decimals():
    assert format_icici_amount(2500) == "2500.00"
    assert format_icici_amount("99.9") == "99.90"
    assert format_icici_amount(10.005) == "10.01"
    with pytest.raises(ValueError):
        format_icici_amount(0)


def test_txn_date_format():
    from datetime import datetime, timezone

    stamp = format_icici_txn_date(datetime(2026, 9, 23, 12, 51, 30, tzinfo=timezone.utc))
    assert stamp == "20260923125130"
    assert len(format_icici_txn_date()) == 14


def test_v1_hash_sorts_alphabetically_and_excludes_secure_hash():
    params = {
        "merchantTxnNo": "ABC123",
        "amount": "100.00",
        "currencyCode": "356",
        "secureHash": "should-be-ignored",
        "aggregatorID": "AGG1",
        "merchantId": "MID1",
    }
    payload = build_v1_hash_payload(params)
    # Sorted keys: aggregatorID, amount, currencyCode, merchantId, merchantTxnNo
    assert payload == "AGG1100.00356MID1ABC123"
    assert "should-be-ignored" not in payload


def test_v1_hmac_sha256_lowercase_hex():
    params = {
        "amount": "2500.00",
        "currencyCode": "356",
        "merchantId": "MIDTEST",
        "merchantTxnNo": "SFTESTTXN001",
        "payType": "0",
        "transactionType": "SALE",
    }
    expected_message = build_v1_hash_payload(params)
    expected = hmac.new(
        SAMPLE_KEY.encode("utf-8"),
        expected_message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().lower()
    assert generate_secure_hash_v1(params, SAMPLE_KEY) == expected
    assert expected == expected.lower()
    assert all(c in "0123456789abcdef" for c in expected)


def test_attach_and_verify_secure_hash_roundtrip():
    params = {
        "merchantId": "MID",
        "aggregatorID": "AGG",
        "merchantTxnNo": "TXN1",
        "amount": "10.00",
        "currencyCode": "356",
        "payType": "0",
        "transactionType": "SALE",
        "returnURL": "https://example.com/cb",
        "txnDate": "20260923125130",
        "customerEmailID": "a@b.com",
    }
    attach_secure_hash_v1(params, SAMPLE_KEY)
    assert "secureHash" in params
    assert verify_secure_hash_v1(params, SAMPLE_KEY) is True
    tampered = dict(params)
    tampered["amount"] = "11.00"
    assert verify_secure_hash_v1(tampered, SAMPLE_KEY) is False


def test_empty_values_excluded_from_hash():
    params = {"a": "1", "b": "", "c": None, "d": "2"}
    assert build_v1_hash_payload(params) == "12"


def test_v2_hash_uses_minified_json():
    body = {"b": 2, "a": 1}
    expected = hmac.new(
        SAMPLE_KEY.encode("utf-8"),
        b'{"b":2,"a":1}',
        hashlib.sha256,
    ).hexdigest().lower()
    # json.dumps with separators preserves insertion order in Py3.7+
    assert generate_secure_hash_v2(body, SAMPLE_KEY) == expected


def test_build_initiate_sale_request_standard_method(monkeypatch):
    provider = IciciPaymentProvider()
    monkeypatch.setattr(provider, "reload", lambda: None)
    monkeypatch.setattr(provider, "mid", "MID123")
    monkeypatch.setattr(provider, "key", SAMPLE_KEY)
    monkeypatch.setattr(provider, "agg_id", "AGG123")
    monkeypatch.setattr(
        provider,
        "resolve_return_url",
        lambda: "https://shafsky-backend-1.onrender.com/api/payments/icici/callback",
    )

    body = provider.build_initiate_sale_request(
        merchant_txn_no="SFABCDEF1234567890",
        amount=2500,
        customer_email="guest@example.com",
        customer_mobile="+91 98765 43210",
        customer_name="Test Guest",
        addl_param1="BKREF1",
        txn_date="20260923125130",
    )
    assert body["payType"] == "0"
    assert body["transactionType"] == "SALE"
    assert body["currencyCode"] == "356"
    assert body["amount"] == "2500.00"
    assert body["customerMobileNo"] == "919876543210" or body["customerMobileNo"].endswith("9876543210")
    assert "secureHash" in body
    assert verify_secure_hash_v1(body, SAMPLE_KEY)


def test_localhost_return_url_rejected(monkeypatch):
    provider = IciciPaymentProvider()
    monkeypatch.setattr(provider, "mid", "MID")
    monkeypatch.setattr(provider, "key", SAMPLE_KEY)
    monkeypatch.setattr(provider, "agg_id", "AGG")
    with pytest.raises(ValueError):
        from app.providers.icici_provider import _public_https_url

        _public_https_url("http://127.0.0.1:8003/api/payments/icici/callback", label="ICICI_RETURN_URL")


def test_build_redirect_url():
    provider = IciciPaymentProvider()
    url = provider.build_redirect_url(
        "https://pgpayuat.icicibank.com/tsp/pg/api/v2/authRedirect",
        "CTX123",
    )
    assert url.endswith("?tranCtx=CTX123")


def test_initiate_sale_success_mocked(monkeypatch):
    provider = IciciPaymentProvider()
    monkeypatch.setattr(provider, "reload", lambda: None)
    monkeypatch.setattr(provider, "mid", "MID")
    monkeypatch.setattr(provider, "key", SAMPLE_KEY)
    monkeypatch.setattr(provider, "agg_id", "AGG")
    monkeypatch.setattr(provider, "sale_url", "https://example.test/initiateSale")
    monkeypatch.setattr(provider, "timeout", 5.0)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "responseCode": "R1000",
        "merchantId": "MID",
        "aggregatorID": "AGG",
        "merchantTxnNo": "SFTXN1",
        "redirectURI": "https://pgpayuat.icicibank.com/tsp/pg/api/v2/authRedirect",
        "tranCtx": "CTX99",
        "secureHash": "abc",
    }

    with patch("app.providers.icici_provider.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = mock_resp
        client_cls.return_value = client
        result = provider.initiate_sale({"merchantTxnNo": "SFTXN1", "secureHash": "x"})

    assert result["success"] is True
    assert result["payment_url"].endswith("tranCtx=CTX99")


def test_initiate_sale_failure_code(monkeypatch):
    provider = IciciPaymentProvider()
    monkeypatch.setattr(provider, "reload", lambda: None)
    monkeypatch.setattr(provider, "mid", "MID")
    monkeypatch.setattr(provider, "key", SAMPLE_KEY)
    monkeypatch.setattr(provider, "agg_id", "AGG")
    monkeypatch.setattr(provider, "timeout", 5.0)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"responseCode": "R9999", "merchantTxnNo": "SFTXN1"}

    with patch("app.providers.icici_provider.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = mock_resp
        client_cls.return_value = client
        result = provider.initiate_sale({"merchantTxnNo": "SFTXN1"})

    assert result["success"] is False


def test_status_success_mapping(monkeypatch):
    provider = IciciPaymentProvider()
    monkeypatch.setattr(provider, "reload", lambda: None)
    monkeypatch.setattr(provider, "mid", "MID")
    monkeypatch.setattr(provider, "key", SAMPLE_KEY)
    monkeypatch.setattr(provider, "agg_id", "AGG")
    monkeypatch.setattr(provider, "command_url", "https://example.test/command")
    monkeypatch.setattr(provider, "timeout", 5.0)

    status_body = {
        "responseCode": "000",
        "merchantId": "MID",
        "merchantTxnNo": "SFTXN1",
        "txnStatus": "SUC",
        "txnResponseCode": "0000",
        "txnID": "BANKTXN1",
        "amount": "100.00",
    }
    status_body["secureHash"] = generate_secure_hash_v1(status_body, SAMPLE_KEY)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = status_body
    mock_resp.text = ""

    with patch("app.providers.icici_provider.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = mock_resp
        client_cls.return_value = client
        result = provider.check_transaction_status(merchant_txn_no="SFTXN1")

    assert result["success"] is True
    assert result["paid"] is True


def test_callback_identity_amount_mismatch(monkeypatch):
    provider = IciciPaymentProvider()
    monkeypatch.setattr(provider, "reload", lambda: None)
    monkeypatch.setattr(provider, "mid", "MID")
    monkeypatch.setattr(provider, "key", SAMPLE_KEY)
    monkeypatch.setattr(provider, "agg_id", "AGG")

    params = {
        "merchantId": "MID",
        "aggregatorID": "AGG",
        "merchantTxnNo": "SFTXN1",
        "amount": "50.00",
    }
    params["secureHash"] = generate_secure_hash_v1(params, SAMPLE_KEY)
    result = provider.verify_callback_identity(
        params,
        expected_merchant_txn_no="SFTXN1",
        expected_amount=100.0,
    )
    assert result["ok"] is False
    assert "amount mismatch" in result["errors"]


def test_page65_payment_response_hash_uses_v1_ascending_names():
    """
    Gateway Interface Spec V0.4 page 65 sample Payment Response fields +
    page 68 ascending name order.
    """
    # Page 65 form fields (excluding secureHash)
    params = {
        "responseCode": "000",
        "respDescription": "SUCCESS",
        "merchantId": "M000001",
        "merchantTxnNo": "M0099999221",
        "txnID": "T1472640294491",
        "paymentDateTime": "20160831041454",
        "txnAuthID": "006503",
    }
    # Ascending names: merchantId, merchantTxnNo, paymentDateTime,
    # respDescription, responseCode, txnAuthID, txnID
    expected_plain = (
        "M000001"
        "M0099999221"
        "20160831041454"
        "SUCCESS"
        "000"
        "006503"
        "T1472640294491"
    )
    assert build_v1_hash_payload(params) == expected_plain
    attach_secure_hash_v1(params, SAMPLE_KEY)
    assert verify_secure_hash_v1(params, SAMPLE_KEY) is True


def test_callback_must_not_use_v2_when_body_securehash_present():
    from app.providers.icici_hash import verify_callback_or_status_response_hash

    params = {
        "merchantId": "MID",
        "merchantTxnNo": "TXN1",
        "responseCode": "000",
        "respDescription": "SUCCESS",
    }
    attach_secure_hash_v1(params, SAMPLE_KEY)
    # Even with a bogus V2 header, body secureHash (V1 / page 65+68) wins
    assert (
        verify_callback_or_status_response_hash(
            params,
            SAMPLE_KEY,
            header_securehash="deadbeef",
        )
        is True
    )


def test_page68_example_concat_order():
    # Spec example: name=aa, param1=abc, param2=xyz → aaabcxyz
    assert build_v1_hash_payload({"param1": "abc", "param2": "xyz", "name": "aa"}) == "aaabcxyz"


def test_callback_identity_txn_mismatch(monkeypatch):
    provider = IciciPaymentProvider()
    monkeypatch.setattr(provider, "reload", lambda: None)
    monkeypatch.setattr(provider, "mid", "MID")
    monkeypatch.setattr(provider, "key", SAMPLE_KEY)
    monkeypatch.setattr(provider, "agg_id", "AGG")

    params = {
        "merchantId": "MID",
        "aggregatorID": "AGG",
        "merchantTxnNo": "OTHER",
        "amount": "100.00",
    }
    params["secureHash"] = generate_secure_hash_v1(params, SAMPLE_KEY)
    result = provider.verify_callback_identity(
        params,
        expected_merchant_txn_no="SFTXN1",
        expected_amount=100.0,
    )
    assert result["ok"] is False
    assert "merchantTxnNo mismatch" in result["errors"]


def test_customer_return_url_cancelled_status(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ICICI_CUSTOMER_RETURN_URL", "https://shafskyaviation.com/book/payment-result")
    provider = IciciPaymentProvider()
    url = provider.resolve_customer_return_url("SF-BOOK-1", success=False, cancelled=True)
    assert "status=cancelled" in url
    assert "ref=SF-BOOK-1" in url
    assert "gateway=icici" in url


def test_browser_back_get_redirects_without_confirming(monkeypatch):
    """Regression: ICICI Back ← is GET to returnURL; must not 404 or confirm."""
    from app.services.payment_service import PaymentService

    monkeypatch.setattr(
        "app.providers.icici_provider.settings.ICICI_CUSTOMER_RETURN_URL",
        "https://shafskyaviation.com/book/payment-result",
    )
    db = MagicMock()
    db.scalar.return_value = None

    with patch.object(PaymentService, "handle_verified_payment") as confirm:
        outcome = PaymentService.handle_icici_browser_back(db, {})
        confirm.assert_not_called()

    assert outcome["cancelled"] is True
    assert outcome["success"] is False
    assert "status=cancelled" in outcome["redirect_url"]
    assert "gateway=icici" in outcome["redirect_url"]


def test_icici_callback_get_not_404(monkeypatch):
    """HTTP GET /api/payments/icici/callback must redirect (303), never ERR_404."""
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(
        "app.providers.icici_provider.settings.ICICI_CUSTOMER_RETURN_URL",
        "https://shafskyaviation.com/book/payment-result",
    )
    client = TestClient(app, raise_server_exceptions=False)
    res = client.get("/api/payments/icici/callback", follow_redirects=False)
    assert res.status_code == 303
    loc = res.headers.get("location") or ""
    assert "/book/payment-result" in loc
    assert "status=cancelled" in loc
    assert "ERR_404" not in (res.text or "")
