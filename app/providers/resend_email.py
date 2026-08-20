"""Resend transactional email provider (existing project provider)."""

from typing import Dict, Any, Optional
import logging

from app.providers.base import EmailProvider
from app.services.notification_service import NotificationService

logger = logging.getLogger("shafsky.providers.resend")


class ResendEmailProvider(EmailProvider):
    def send_email(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        return NotificationService.send_email_resend_sync(
            recipient_email=to_email,
            subject=subject,
            html_content=body_html,
        )
