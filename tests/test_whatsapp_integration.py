"""
Comprehensive Unit Test Suite for Official Meta WhatsApp Cloud API Backend Integration.
Tests Webhook Verification GET, Event Ingestion POST, Status Parsing, Outbound Error Handling,
Health Checks, and Test Dispatch Endpoints.
"""

import uuid
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import httpx

from app.main import app
from app.integrations.whatsapp.client import WhatsAppClient, whatsapp_client, send_whatsapp_message
from app.integrations.whatsapp.service import WhatsAppService

client = TestClient(app)


# 1. Webhook GET Verification Success
def test_whatsapp_webhook_verification_success(monkeypatch):
    monkeypatch.setenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "shafsky_test_verify_token_123")
    res = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "shafsky_test_verify_token_123",
            "hub.challenge": "987654321"
        }
    )
    assert res.status_code == 200
    assert res.text == "987654321"


# 2. Webhook GET Verification Failure
def test_whatsapp_webhook_verification_failure(monkeypatch):
    monkeypatch.setenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "shafsky_test_verify_token_123")
    res = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "invalid_wrong_token",
            "hub.challenge": "987654321"
        }
    )
    assert res.status_code == 403
    assert "verification failed" in res.json()["detail"].lower()


# 3. Webhook POST Message Event Parsing
@patch("app.integrations.whatsapp.client.whatsapp_client.send_text_message")
def test_whatsapp_webhook_post_message_parsing(mock_send, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "")
    mock_send.return_value = {"success": True, "message_id": "wamid.test_reply_123"}

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_12345",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "15550248142", "phone_number_id": "105954558954427"},
                            "contacts": [{"profile": {"name": "Test Guest"}, "wa_id": "919876543210"}],
                            "messages": [
                                {
                                    "from": "919876543210",
                                    "id": f"wamid.inbound_{uuid.uuid4().hex[:8]}",
                                    "timestamp": "1678900000",
                                    "text": {"body": "I need help with my booking"},
                                    "type": "text"
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    res = client.post("/api/whatsapp/webhook", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["data"]["messages_handled"] == 1


# 4. Webhook POST Status Event Parsing (Sent, Delivered, Read, Failed)
def test_whatsapp_webhook_post_status_event_parsing(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "")
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_12345",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "15550248142", "phone_number_id": "105954558954427"},
                            "statuses": [
                                {
                                    "id": "wamid.msg_status_001",
                                    "status": "delivered",
                                    "timestamp": "1678900100",
                                    "recipient_id": "919876543210"
                                },
                                {
                                    "id": "wamid.msg_status_002",
                                    "status": "failed",
                                    "timestamp": "1678900200",
                                    "recipient_id": "919876543210",
                                    "errors": [{"code": 131026, "title": "Message undeliverable"}]
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    res = client.post("/api/whatsapp/webhook", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["data"]["statuses_handled"] == 2


# 5. Missing WhatsApp Configuration Handling
def test_whatsapp_missing_configuration_handling(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "")

    wa_client = WhatsAppClient()
    assert wa_client.is_configured() is False

    res = wa_client.send_message("919876543210", "Hello Test")
    assert res["success"] is False
    assert res["status"] == "unconfigured"
    assert "not configured" in res["error"]


# 6. Meta API Authentication Failure (HTTP 401)
@patch("httpx.Client.post")
def test_whatsapp_meta_api_auth_failure(mock_post, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "invalid_access_token_abc")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "105954558954427")

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.json.return_value = {
        "error": {
            "message": "Invalid OAuth access token.",
            "type": "OAuthException",
            "code": 190,
            "error_subcode": 463
        }
    }
    mock_response.text = "Invalid OAuth access token."
    mock_post.return_value = mock_response

    wa_client = WhatsAppClient()
    result = wa_client.send_message("919876543210", "Test Auth Error")

    assert result["success"] is False
    assert result["status_code"] == 401
    assert result["error_code"] == 190
    # Guarantee token is not leaked
    assert "invalid_access_token_abc" not in result["error"]


# 7. Successful Message Response (HTTP 200)
@patch("httpx.Client.post")
def test_whatsapp_successful_message_response(mock_post, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "valid_test_token_123")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "105954558954427")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "messaging_product": "whatsapp",
        "contacts": [{"input": "919876543210", "wa_id": "919876543210"}],
        "messages": [{"id": "wamid.HBgMOTE5ODc2NTQzMjEw"}]
    }
    mock_post.return_value = mock_response

    wa_client = WhatsAppClient()
    result = wa_client.send_message("919876543210", "Your booking is confirmed.")

    assert result["success"] is True
    assert result["status"] == "sent"
    assert result["message_id"] == "wamid.HBgMOTE5ODc2NTQzMjEw"


# 8. Meta API HTTP Error Handling (400, 403, 404, 429, 500)
@pytest.mark.parametrize("status_code,err_code,err_text", [
    (400, 100, "Invalid parameter"),
    (403, 200, "Permissions error"),
    (404, 80004, "Phone number ID not found"),
    (429, 130429, "Rate limit hit"),
    (500, 2, "Meta internal error"),
])
@patch("httpx.Client.post")
def test_whatsapp_meta_http_errors(mock_post, status_code, err_code, err_text, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "valid_test_token_123")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "105954558954427")

    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = {
        "error": {"message": err_text, "code": err_code}
    }
    mock_response.text = err_text
    mock_post.return_value = mock_response

    wa_client = WhatsAppClient()
    result = wa_client.send_message("919876543210", "Test HTTP error handling")

    assert result["success"] is False
    assert result["status_code"] == status_code
    assert result["error_code"] == err_code
    assert err_text in result["error"]


# 9. WhatsApp Status Endpoint
def test_whatsapp_integration_status_endpoint(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "valid_test_token_123")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "105954558954427")
    monkeypatch.setenv("WHATSAPP_API_VERSION", "v21.0")

    res = client.get("/api/whatsapp/status")
    assert res.status_code == 200
    data = res.json()
    assert data["configured"] is True
    assert data["api_version"] == "v21.0"
    # Never expose access token or secret IDs
    assert "access_token" not in data
    assert "phone_number_id" not in data


# 10. Webhook Signature Verification with WHATSAPP_APP_SECRET
def test_whatsapp_webhook_signature_verification_success(monkeypatch):
    test_secret = "test_meta_app_secret_12345"
    monkeypatch.setenv("WHATSAPP_APP_SECRET", test_secret)

    payload = {
        "object": "whatsapp_business_account",
        "entry": []
    }
    import json, hmac, hashlib
    payload_bytes = json.dumps(payload).encode("utf-8")
    sig = hmac.new(test_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

    res = client.post(
        "/api/whatsapp/webhook",
        content=payload_bytes,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={sig}"}
    )
    assert res.status_code == 200


def test_whatsapp_webhook_signature_verification_failure(monkeypatch):
    test_secret = "test_meta_app_secret_12345"
    monkeypatch.setenv("WHATSAPP_APP_SECRET", test_secret)

    payload = {"object": "whatsapp_business_account", "entry": []}
    import json
    payload_bytes = json.dumps(payload).encode("utf-8")

    # 1. Invalid signature
    res_invalid = client.post(
        "/api/whatsapp/webhook",
        content=payload_bytes,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=invalid_hex_signature"}
    )
    assert res_invalid.status_code == 401
    assert "Invalid WhatsApp webhook signature" in res_invalid.json()["detail"]

    # 2. Missing signature header
    res_missing = client.post(
        "/api/whatsapp/webhook",
        content=payload_bytes,
        headers={"Content-Type": "application/json"}
    )
    assert res_missing.status_code == 401


# 11. Webhook Signature with Raw Bytes and Header Variations
def test_whatsapp_webhook_signature_raw_bytes_and_formats(monkeypatch):
    test_secret = "meta_32char_secret_0123456789abc"
    monkeypatch.setenv("WHATSAPP_APP_SECRET", test_secret)

    # Test with precise raw bytes containing whitespace and nested UTF-8 JSON
    raw_payload_bytes = b'{\n  "object": "whatsapp_business_account",\n  "entry": []\n}'
    import hmac, hashlib
    sig_hex = hmac.new(test_secret.encode("utf-8"), raw_payload_bytes, hashlib.sha256).hexdigest()

    # 1. Standard sha256= prefix
    res1 = client.post(
        "/api/whatsapp/webhook",
        content=raw_payload_bytes,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={sig_hex}"}
    )
    assert res1.status_code == 200

    # 2. Uppercase SHA256= prefix
    res2 = client.post(
        "/api/whatsapp/webhook",
        content=raw_payload_bytes,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"SHA256={sig_hex}"}
    )
    assert res2.status_code == 200

    # 3. Lowercase header key with whitespace
    res3 = client.post(
        "/api/whatsapp/webhook",
        content=raw_payload_bytes,
        headers={"Content-Type": "application/json", "x-hub-signature-256": f"sha256={sig_hex} "}
    )
    assert res3.status_code == 200


# 12. Webhook Signature with Quoted Secret in Environment
def test_whatsapp_webhook_signature_quoted_env_secret(monkeypatch):
    # Simulate user putting quotes in .env file like WHATSAPP_APP_SECRET="abcdef12345"
    raw_secret = "quoted_secret_value_999"
    monkeypatch.setenv("WHATSAPP_APP_SECRET", f'"{raw_secret}"')

    raw_body = b'{"object":"whatsapp_business_account","entry":[]}'
    import hmac, hashlib
    sig_hex = hmac.new(raw_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    res = client.post(
        "/api/whatsapp/webhook",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={sig_hex}"}
    )
    assert res.status_code == 200

