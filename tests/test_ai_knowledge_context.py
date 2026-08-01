"""
Unit Test Suite for AI Knowledge Base & Dynamic Context Builder.
"""

import pytest
import uuid
from app.database import SessionLocal
from app.ai.knowledge import AiKnowledgeService
from app.ai.context import AiContextBuilder
from app.ai.memory import ConversationMemory
from app.ai.schemas import ConversationSessionData


def test_ai_knowledge_service():
    summary = AiKnowledgeService.get_knowledge_summary()
    assert "Shafsky Aviation Services" in summary
    assert "BOM (Mumbai)" in summary
    assert "MEET_GREET" in summary

    faqs = AiKnowledgeService.get_faqs()
    assert len(faqs) >= 2


def test_ai_context_builder():
    db = SessionLocal()
    try:
        conv_id = f"test_ctx_{uuid.uuid4().hex[:6]}"
        session = ConversationSessionData(conversation_id=conv_id, booking_id=None)
        ConversationMemory.save_session(session)

        context_dict = AiContextBuilder.build_context(db, conv_id, email="customer@shafsky.com")
        assert context_dict["conversation_id"] == conv_id
        assert context_dict["current_state"] == "NEW"

        formatted = AiContextBuilder.format_context_string(context_dict)
        assert conv_id in formatted
    finally:
        db.close()
