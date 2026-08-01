"""
Abstract Base Interface for LLM Providers.
Ensures provider independence across OpenAI, Anthropic, Gemini, or Mock LLMs.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BaseLLMProvider(ABC):
    """Abstract interface enforcing uniform contract for LLM backends."""

    @abstractmethod
    def generate_reply(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        temperature: float = 0.7
    ) -> str:
        """Generates conversational text response from messages."""
        pass

    @abstractmethod
    def tool_call(
        self,
        messages: List[Dict[str, str]],
        tools_schema: List[Dict[str, Any]],
        system_prompt: str
    ) -> Dict[str, Any]:
        """Performs function tool call decision over messages."""
        pass

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """Performs provider connectivity and health diagnostic check."""
        pass
