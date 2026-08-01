"""
OpenAI LLM Provider Implementation.
Supports Chat Completions, function calling, timeout handling, retries, and fallback resilience.
"""

import os
import logging
from typing import List, Dict, Any, Optional
import httpx

from app.ai.providers.base_provider import BaseLLMProvider

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API integration provider."""

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.timeout = float(os.getenv("OPENAI_TIMEOUT", "15.0"))
        self.max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "2"))
        self.api_url = "https://api.openai.com/v1/chat/completions"

    def generate_reply(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        temperature: float = 0.7
    ) -> str:
        """Generates chat reply via OpenAI API or fallback engine."""
        if not self.api_key or self.api_key.startswith("mock"):
            logger.info("OpenAI API key unconfigured. Utilizing rule-based fallback response engine.")
            last_msg = messages[-1]["content"] if messages else ""
            return f"Shafsky Aviation Concierge: Received your query regarding '{last_msg[:40]}'. Our 24/7 aviation desk is standing by."

        formatted_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            formatted_messages.append({"role": m["role"], "content": m["content"]})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": temperature
        }

        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    res = client.post(self.api_url, headers=headers, json=payload)
                    if res.status_code == 200:
                        data = res.json()
                        return data["choices"][0]["message"]["content"]
                    logger.warning(f"OpenAI API returned status {res.status_code}: {res.text}")
            except Exception as err:
                logger.warning(f"OpenAI API request attempt {attempt + 1} failed: {err}")

        return "Shafsky Aviation Concierge: Our AI assistant is currently updating. A duty officer will be right with you."

    def tool_call(
        self,
        messages: List[Dict[str, str]],
        tools_schema: List[Dict[str, Any]],
        system_prompt: str
    ) -> Dict[str, Any]:
        """Performs function calling decision over messages."""
        if not self.api_key or self.api_key.startswith("mock"):
            last_msg = messages[-1]["content"] if messages else ""
            return {"tool_name": None, "arguments": {}, "fallback_reply": f"Processing request for '{last_msg[:30]}'"}

        formatted_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            formatted_messages.append({"role": m["role"], "content": m["content"]})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "tools": tools_schema,
            "tool_choice": "auto"
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(self.api_url, headers=headers, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    msg = data["choices"][0]["message"]
                    if msg.get("tool_calls"):
                        tc = msg["tool_calls"][0]
                        return {
                            "tool_name": tc["function"]["name"],
                            "arguments": tc["function"].get("arguments", {}),
                            "fallback_reply": None
                        }
                    return {"tool_name": None, "arguments": {}, "fallback_reply": msg.get("content")}
        except Exception as err:
            logger.error(f"OpenAI tool calling error: {err}")

        return {"tool_name": None, "arguments": {}, "fallback_reply": "Service query processed."}

    def health_check(self) -> Dict[str, Any]:
        """Checks OpenAI provider configuration status."""
        is_configured = bool(self.api_key and not self.api_key.startswith("mock"))
        return {
            "provider": "OpenAI",
            "model": self.model,
            "configured": is_configured,
            "timeout_seconds": self.timeout
        }
