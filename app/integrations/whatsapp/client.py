"""
Meta WhatsApp Cloud API HTTP Client.
Handles outbound messaging, webhook challenge verification, and Graph API request error handling.
"""

import os
import hmac
import logging
from typing import Dict, Any, Optional, List
import httpx

logger = logging.getLogger(__name__)


class WhatsAppClient:
    """Official Meta WhatsApp Cloud API HTTP Client."""

    def __init__(self):
        self._load_config()

    def _load_config(self):
        """Loads environment configuration dynamically."""
        self.access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
        self.phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        self.business_account_id = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip()
        self.verify_token = os.getenv(
            "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
            os.getenv("WHATSAPP_VERIFY_TOKEN", "")
        ).strip()
        self.api_version = os.getenv(
            "WHATSAPP_API_VERSION",
            os.getenv("META_GRAPH_API_VERSION", "v21.0")
        ).strip() or "v21.0"

    def is_configured(self) -> bool:
        """Checks if valid WhatsApp API credentials are set in the backend environment."""
        self._load_config()
        return bool(
            self.access_token
            and self.phone_number_id
            and not self.access_token.startswith("mock_")
            and not self.phone_number_id.startswith("mock_")
        )

    @property
    def base_url(self) -> str:
        self._load_config()
        return f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"

    def verify_webhook_challenge(
        self,
        mode: Optional[str],
        token: Optional[str],
        challenge: Optional[str]
    ) -> Optional[str]:
        """
        Verifies Meta Webhook challenge token during webhook setup using constant-time comparison.
        """
        self._load_config()
        if mode == "subscribe" and token and self.verify_token:
            if hmac.compare_digest(token, self.verify_token):
                logger.info("[WhatsApp] Meta webhook challenge verified successfully.")
                return challenge

        logger.warning("[WhatsApp] Meta webhook challenge verification failed.")
        return None

    def send_message(
        self,
        to_phone: str,
        message_body: Optional[str] = None,
        template_name: Optional[str] = None,
        template_language: str = "en_US",
        template_components: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Sends an outbound WhatsApp text or template message via Meta Graph API.
        Does not leak access tokens in logs or returned error structures.
        """
        self._load_config()

        if not self.is_configured():
            logger.warning("[WhatsApp] Send skipped: WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID is not configured.")
            return {
                "success": False,
                "error": "WhatsApp Cloud API integration is not configured in backend environment.",
                "status": "unconfigured"
            }

        # Normalize phone number to digits only
        clean_phone = "".join(filter(str.isdigit, str(to_phone)))
        if not clean_phone or len(clean_phone) < 10:
            logger.error(f"[WhatsApp] Invalid recipient phone number format: '{to_phone}'")
            return {
                "success": False,
                "error": f"Invalid recipient phone number format: '{to_phone}'",
                "status": "failed"
            }

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        if template_name:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": clean_phone,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": template_language},
                    "components": template_components or []
                }
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": clean_phone,
                "type": "text",
                "text": {"preview_url": False, "body": message_body or ""}
            }

        try:
            with httpx.Client(timeout=12.0) as client:
                response = client.post(self.base_url, headers=headers, json=payload)
                masked_phone = f"{clean_phone[:3]}****{clean_phone[-3:]}" if len(clean_phone) > 6 else clean_phone

                if response.status_code == 200:
                    data = response.json()
                    message_id = None
                    if "messages" in data and len(data["messages"]) > 0:
                        message_id = data["messages"][0].get("id")

                    logger.info(f"[WhatsApp] Message dispatched successfully to {masked_phone}. Message ID: {message_id}")
                    return {
                        "success": True,
                        "message_id": message_id,
                        "status": "sent",
                        "response": data
                    }

                # Meta API Returned Non-200 Error
                err_data = {}
                try:
                    err_data = response.json().get("error", {})
                except Exception:
                    pass

                error_msg = err_data.get("message", response.text)
                error_code = err_data.get("code", response.status_code)
                error_subcode = err_data.get("error_subcode")

                # Sanitize error message to ensure token is never exposed
                if self.access_token and self.access_token in error_msg:
                    error_msg = error_msg.replace(self.access_token, "[REDACTED]")

                logger.error(
                    f"[WhatsApp] Meta API Error (HTTP {response.status_code}, Code {error_code}): {error_msg} for recipient {masked_phone}"
                )
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "error_code": error_code,
                    "error_subcode": error_subcode,
                    "error": f"Meta API Error ({response.status_code}): {error_msg}",
                    "status": "failed"
                }

        except httpx.TimeoutException:
            logger.error(f"[WhatsApp] Network timeout connecting to Meta Graph API at {self.base_url}")
            return {
                "success": False,
                "error": "Timeout connecting to Meta WhatsApp API.",
                "status": "failed"
            }
        except Exception as err:
            logger.error(f"[WhatsApp] Exception during Graph API request: {err}")
            return {
                "success": False,
                "error": f"Network exception: {str(err)}",
                "status": "failed"
            }

    def send_text_message(self, to_phone: str, message_body: str) -> Dict[str, Any]:
        """Convenience method for text messages."""
        return self.send_message(to_phone=to_phone, message_body=message_body)


# Global Singleton Instance
whatsapp_client = WhatsAppClient()


def send_whatsapp_message(
    recipient_phone: str,
    message_text: Optional[str] = None,
    template_name: Optional[str] = None,
    template_components: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Reusable backend entry point for WhatsApp message dispatching.
    """
    return whatsapp_client.send_message(
        to_phone=recipient_phone,
        message_body=message_text,
        template_name=template_name,
        template_components=template_components
    )
