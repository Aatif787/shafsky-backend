"""
Official Razorpay Integration Provider.
Handles Payment Order & Link Creation, Server-Side Amount Calculation, and HMAC SHA256 Webhook Verification.
"""

import os
import hmac
import hashlib
import logging
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)


class RazorpayProvider:
    """Razorpay Gateway Provider for Shafsky Aviation."""

    def __init__(self):
        self._load_config()

    def _load_config(self):
        self.key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
        self.key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
        self.webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()

    def is_configured(self) -> bool:
        self._load_config()
        return bool(self.key_id and self.key_secret and not self.key_id.startswith("rzp_test_placeholder"))

    def create_payment_link(
        self,
        amount: float,
        currency: str,
        reference_id: str,
        description: str,
        customer_name: str,
        customer_email: str,
        customer_phone: str
    ) -> Dict[str, Any]:
        """
        Creates an official Razorpay Payment Link server-side.
        Amount must be in smallest currency unit (e.g. paise for INR).
        """
        self._load_config()
        amount_paise = int(round(amount * 100))

        if not self.is_configured():
            logger.warning("[Razorpay] Provider not configured in environment. Using simulated payment link fallback.")
            fake_link_id = f"plink_sim_{reference_id.replace('-', '')}"
            fake_url = f"https://rzp.io/i/simulated_{fake_link_id}"
            return {
                "success": True,
                "payment_link_id": fake_link_id,
                "short_url": fake_url,
                "amount": amount,
                "currency": currency,
                "simulated": True
            }

        url = "https://api.razorpay.com/v1/payment_links"
        payload = {
            "amount": amount_paise,
            "currency": currency.upper(),
            "accept_partial": False,
            "reference_id": reference_id,
            "description": description,
            "customer": {
                "name": customer_name,
                "email": customer_email,
                "contact": customer_phone
            },
            "notify": {
                "sms": True,
                "email": True,
                "whatsapp": True
            },
            "reminder_enable": True,
            "notes": {
                "booking_ref": reference_id,
                "platform": "Shafsky Aviation WhatsApp Engine"
            }
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(
                    url,
                    auth=(self.key_id, self.key_secret),
                    json=payload
                )

                if res.status_code in (200, 201):
                    data = res.json()
                    logger.info(f"[Razorpay] Payment link created: {data.get('id')} for ref {reference_id}")
                    return {
                        "success": True,
                        "payment_link_id": data.get("id"),
                        "short_url": data.get("short_url"),
                        "order_id": data.get("order_id"),
                        "amount": amount,
                        "currency": currency,
                        "simulated": False,
                        "raw_response": data
                    }

                logger.error(f"[Razorpay] API Error ({res.status_code}): {res.text}")
                return {
                    "success": False,
                    "error": f"Razorpay API Error ({res.status_code}): {res.text}"
                }

        except Exception as err:
            logger.error(f"[Razorpay] Exception creating payment link: {err}")
            return {
                "success": False,
                "error": f"Network exception: {str(err)}"
            }

    def verify_webhook_signature(self, payload_bytes: bytes, signature_header: Optional[str]) -> bool:
        """
        Verifies HMAC SHA256 signature header sent by Razorpay webhook server.
        """
        self._load_config()
        if not signature_header or not self.webhook_secret:
            return False

        computed_signature = hmac.new(
            key=self.webhook_secret.encode("utf-8"),
            msg=payload_bytes,
            digestmod=hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(computed_signature, signature_header)


# Global Singleton Instance
razorpay_provider = RazorpayProvider()
