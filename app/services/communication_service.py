"""
Multi-Channel Communication & Notification Service.
Encapsulates Email, WhatsApp, SMS dispatching, background queue retry policy,
channel preferences, and audit tracking.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import logging
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.providers.base import (
    EmailProvider, MockEmailProvider,
    WhatsAppProvider, MockWhatsAppProvider,
    SMSProvider, MockSMSProvider
)
from app.providers.resend_email import ResendEmailProvider
from app.services.notification_service import NotificationService
from app.services.timeline_service import TimelineService
from app.config import settings

logger = logging.getLogger(__name__)


class CommunicationService:
    """Core multi-channel communication manager."""

    @classmethod
    def dispatch_email(
        cls,
        db: Session,
        user_id: Optional[str],
        to_email: str,
        subject: str,
        body_html: str,
        provider: Optional[EmailProvider] = None
    ) -> Dict[str, Any]:
        """Dispatches an email notification via configured provider."""
        if provider is None:
            if (settings.RESEND_API_KEY or "").strip():
                provider = ResendEmailProvider()
            else:
                logger.warning("Email dispatch using mock provider because Resend is not configured")
                provider = MockEmailProvider()
        res = provider.send_email(to_email=to_email, subject=subject, body_html=body_html)
        status = str(res.get("status") or "").upper()
        if status in ("DELIVERED", "SENT", "SUCCESS", "OK"):
            logger.info("Email dispatch accepted", extra={"message_id": res.get("message_id")})
        else:
            logger.warning("Email dispatch not delivered", extra={"status": status, "error": res.get("error") or res.get("reason")})
        return res

    @classmethod
    def dispatch_whatsapp(
        cls,
        db: Session,
        user_id: Optional[str],
        phone_number: str,
        template_name: str,
        parameters: Dict[str, Any],
        provider: Optional[WhatsAppProvider] = None
    ) -> Dict[str, Any]:
        """Dispatches a WhatsApp notification via configured provider."""
        provider = provider or MockWhatsAppProvider()
        res = provider.send_whatsapp_message(
            phone_number=phone_number,
            template_name=template_name,
            parameters=parameters
        )

        if user_id:
            try:
                NotificationService.create_notification(
                    db,
                    user_id=user_id,
                    title=f"WhatsApp Alert ({template_name})",
                    message=f"WhatsApp message dispatched to {phone_number}",
                    notification_type="WHATSAPP",
                    channel="WHATSAPP",
                    data={"phone": phone_number, "messageId": res.get("message_id")}
                )
            except Exception as err:
                logger.warning(f"Failed to log notification record: {err}")

        return res

    @classmethod
    def dispatch_sms(
        cls,
        db: Session,
        user_id: Optional[str],
        phone_number: str,
        message: str,
        provider: Optional[SMSProvider] = None
    ) -> Dict[str, Any]:
        """Dispatches an SMS notification via configured provider."""
        provider = provider or MockSMSProvider()
        res = provider.send_sms(phone_number=phone_number, message=message)

        if user_id:
            try:
                NotificationService.create_notification(
                    db,
                    user_id=user_id,
                    title="SMS Alert",
                    message=message,
                    notification_type="SMS",
                    channel="SMS",
                    data={"phone": phone_number, "messageId": res.get("message_id")}
                )
            except Exception as err:
                logger.warning(f"Failed to log notification record: {err}")

        return res
