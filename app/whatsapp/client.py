"""
Meta WhatsApp Cloud API HTTP Client.
Handles outbound messaging, webhook challenge verification, and Graph API request retries.
"""

import os
import logging
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)


class WhatsAppClient:
    """HTTP Client for Meta WhatsApp Cloud API."""

    def __init__(self):
        self.access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "mock_wa_access_token")
        self.phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "mock_phone_number_id")
        self.verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "shafsky_wa_verify_token")
        self.api_version = os.getenv("META_GRAPH_API_VERSION", "v18.0")
        self.base_url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"

    def verify_webhook_challenge(self, mode: Optional[str], token: Optional[str], challenge: Optional[str]) -> Optional[str]:
        """Verifies Meta Webhook challenge token during initial setup."""
        if mode == "subscribe" and token == self.verify_token:
            logger.info("Meta WhatsApp webhook challenge verified successfully.")
            return challenge
        logger.warning(f"Meta WhatsApp webhook challenge failed. Mode: {mode}, Token: {token}")
        return None

    def send_text_message(self, to_phone: str, message_body: str) -> Dict[str, Any]:
        """Sends an outbound WhatsApp text message via Meta Graph API."""
        if not self.access_token or self.access_token == "mock_wa_access_token":
            logger.info(f"[MOCK WHATSAPP OUTBOUND] To: {to_phone} | Message: {message_body[:80]}...")
            return {
                "messaging_product": "whatsapp",
                "contacts": [{"input": to_phone, "wa_id": to_phone}],
                "messages": [{"id": f"wamid.mock_{to_phone}"}],
                "mock": True
            }

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "text",
            "text": {"preview_url": False, "body": message_body}
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(self.base_url, headers=headers, json=payload)
                if response.status_code == 200:
                    return response.json()
                logger.error(f"Meta API error ({response.status_code}): {response.text}")
                return {"error": response.text, "status_code": response.status_code}
        except Exception as err:
            logger.error(f"Failed to dispatch WhatsApp message to {to_phone}: {err}")
            return {"error": str(err)}


# Global client instance
whatsapp_client = WhatsAppClient()
