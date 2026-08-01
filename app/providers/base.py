"""
Provider Abstractions & Interfaces for Payment & Communication Infrastructure.
Provides extensible abstract base classes and default mock implementations.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import uuid
import logging

logger = logging.getLogger(__name__)


# ─── PAYMENT PROVIDER INTERFACE ──────────────────────────────────────────────

class PaymentProvider(ABC):
    """Abstract interface for payment gateway integration (e.g. Stripe, Razorpay)."""

    @abstractmethod
    def create_payment_intent(self, amount: float, currency: str, reference_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abstractmethod
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        pass

    @abstractmethod
    def process_refund(self, transaction_id: str, amount: float, reason: str) -> Dict[str, Any]:
        pass


class MockPaymentProvider(PaymentProvider):
    """Default mock payment provider for development & testing."""

    def create_payment_intent(self, amount: float, currency: str, reference_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        payment_id = f"pay_{uuid.uuid4().hex[:12]}"
        logger.info(f"[MockPaymentProvider] Payment intent created: {payment_id} for {currency} {amount}")
        return {
            "payment_id": payment_id,
            "status": "REQUIRES_PAYMENT_METHOD",
            "client_secret": f"{payment_id}_secret_{uuid.uuid4().hex[:8]}",
            "amount": amount,
            "currency": currency,
            "reference_id": reference_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        logger.info("[MockPaymentProvider] Verifying webhook signature")
        return True

    def process_refund(self, transaction_id: str, amount: float, reason: str) -> Dict[str, Any]:
        refund_id = f"re_{uuid.uuid4().hex[:12]}"
        logger.info(f"[MockPaymentProvider] Refund processed: {refund_id} for transaction {transaction_id}")
        return {
            "refund_id": refund_id,
            "transaction_id": transaction_id,
            "amount": amount,
            "status": "COMPLETED",
            "processed_at": datetime.now(timezone.utc).isoformat()
        }


# ─── EMAIL PROVIDER INTERFACE ────────────────────────────────────────────────

class EmailProvider(ABC):
    """Abstract interface for email dispatch services (e.g. SendGrid, AWS SES)."""

    @abstractmethod
    def send_email(self, to_email: str, subject: str, body_html: str, body_text: Optional[str] = None) -> Dict[str, Any]:
        pass


class MockEmailProvider(EmailProvider):
    def send_email(self, to_email: str, subject: str, body_html: str, body_text: Optional[str] = None) -> Dict[str, Any]:
        msg_id = f"msg_email_{uuid.uuid4().hex[:10]}"
        logger.info(f"[MockEmailProvider] Email sent to {to_email} with subject '{subject}' (ID: {msg_id})")
        return {"message_id": msg_id, "status": "DELIVERED", "to": to_email}


# ─── WHATSAPP PROVIDER INTERFACE ─────────────────────────────────────────────

class WhatsAppProvider(ABC):
    """Abstract interface for WhatsApp Business API (e.g. Twilio, Meta Cloud API)."""

    @abstractmethod
    def send_whatsapp_message(self, phone_number: str, template_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        pass


class MockWhatsAppProvider(WhatsAppProvider):
    def send_whatsapp_message(self, phone_number: str, template_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        msg_id = f"msg_wa_{uuid.uuid4().hex[:10]}"
        logger.info(f"[MockWhatsAppProvider] WhatsApp message sent to {phone_number} using template '{template_name}'")
        return {"message_id": msg_id, "status": "SENT", "phone": phone_number}


# ─── SMS PROVIDER INTERFACE ──────────────────────────────────────────────────

class SMSProvider(ABC):
    """Abstract interface for SMS dispatch services (e.g. Twilio, AWS SNS)."""

    @abstractmethod
    def send_sms(self, phone_number: str, message: str) -> Dict[str, Any]:
        pass


class MockSMSProvider(SMSProvider):
    def send_sms(self, phone_number: str, message: str) -> Dict[str, Any]:
        msg_id = f"msg_sms_{uuid.uuid4().hex[:10]}"
        logger.info(f"[MockSMSProvider] SMS sent to {phone_number} (ID: {msg_id})")
        return {"message_id": msg_id, "status": "SENT", "phone": phone_number}
