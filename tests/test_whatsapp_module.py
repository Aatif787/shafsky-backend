"""
Unit Test Suite for Meta WhatsApp Cloud API Integration Module.
"""

import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.whatsapp.client import whatsapp_client

client = TestClient(app)


def test_whatsapp_webhook_verification_success():
    res = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "shafsky_wa_verify_token",
            "hub.challenge": "115820120"
        }
    )
    assert res.status_code == 200
    assert res.text == "115820120"


def test_whatsapp_webhook_verification_failure():
    res = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "115820120"
        }
    )
    assert res.status_code == 403


def test_whatsapp_incoming_event_ingestion():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WHATSAPP_ACCOUNT_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "15550248142", "phone_number_id": "105954558954427"},
                            "contacts": [{"profile": {"name": "Lady Sarah Sterling"}, "wa_id": "919876543210"}],
                            "messages": [
                                {
                                    "from": "919876543210",
                                    "id": f"wamid.mod_{uuid.uuid4().hex[:8]}",
                                    "timestamp": "1678900000",
                                    "text": {"body": "Check my booking status"},
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
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["success"] is True
    assert data["data"]["messages_handled"] == 1
