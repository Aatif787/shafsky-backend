"""
Unit Test Suite for LLM Provider Integration Layer.
Verifies BaseLLMProvider contract and OpenAIProvider fallback resilience.
"""

import pytest
from app.ai.providers.openai_provider import OpenAIProvider
from app.ai.service import AiService


def test_openai_provider_health():
    provider = OpenAIProvider()
    status = provider.health_check()
    assert status["provider"] == "OpenAI"
    assert "configured" in status
    assert "timeout_seconds" in status


def test_openai_provider_fallback_reply():
    provider = OpenAIProvider()
    messages = [{"role": "user", "content": "What is the cost of Meet & Assist?"}]
    reply = provider.generate_reply(messages, system_prompt="You are an assistant")
    assert isinstance(reply, str)
    assert len(reply) > 10


def test_ai_service_provider_health_check():
    health = AiService.get_provider_health()
    assert health["provider"] == "OpenAI"
