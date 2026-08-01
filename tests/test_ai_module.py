"""
Unit Test Suite for AI Conversation Module.
Verifies interactive chat, WhatsApp webhooks, tool wrappers,
and Redis/in-memory conversation history.
"""

import pytest
import uuid
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine
from app.ai.memory import ConversationMemory
from app.ai.tools import AiTools

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


def test_ai_memory_history():
    session_id = f"test_session_{uuid.uuid4().hex[:6]}"
    ConversationMemory.add_message(session_id, role="user", content="Hello AI")
    ConversationMemory.add_message(session_id, role="assistant", content="Hello, how can I assist you?")

    history = ConversationMemory.get_history(session_id)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"

    ConversationMemory.clear_session(session_id)
    assert len(ConversationMemory.get_history(session_id)) == 0


def test_ai_interactive_chat_api():
    session_id = f"chat_sess_{uuid.uuid4().hex[:6]}"
    payload = {
        "session_id": session_id,
        "message": "Welcome to Shafsky, can you help me?",
        "channel": "WEB"
    }

    res = client.post("/api/ai/chat", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["success"] is True
    assert "reply" in data["data"]
    assert data["data"]["session_id"] == session_id


def test_ai_whatsapp_webhook_api():
    payload = {
        "from_number": "+919876543210",
        "message_body": "What is the status of my booking?"
    }

    res = client.post("/api/ai/whatsapp", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["success"] is True
    assert "reply" in data["data"]


def test_ai_human_handoff_and_takeover():
    conv_id = f"conv_{uuid.uuid4().hex[:6]}"

    # 1. Trigger human handoff request via message
    res = client.post("/api/ai/chat", json={"session_id": conv_id, "message": "I want to speak with a human agent please"})
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["handoff_triggered"] is True
    assert data["current_state"] == "HANDOFF_TO_HUMAN"

    # 2. Inspect conversation state endpoint
    res = client.get(f"/api/ai/conversations/{conv_id}")
    assert res.status_code == 200
    sess_data = res.json()["data"]
    assert sess_data["current_state"] == "HANDOFF_TO_HUMAN"

    # 3. Staff officer takes over conversation
    res = client.post("/api/ai/takeover", json={"conversation_id": conv_id, "staff_user_id": "officer_alex"})
    assert res.status_code == 200
    assert res.json()["data"]["assigned_staff"] == "officer_alex"

    # 4. Resume AI conversation control
    res = client.post("/api/ai/resume", json={"conversation_id": conv_id, "reason": "Issue resolved by officer"})
    assert res.status_code == 200
    assert res.json()["data"]["ai_active"] is True
