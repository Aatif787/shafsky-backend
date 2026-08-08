"""
WhatsApp Integration Service Layer.
Receives incoming messages, delegates processing to AiService, and sends replies via WhatsAppClient.
"""

import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from app.ai.service import AiService
from app.ai.schemas import ChatRequest
from app.whatsapp.client import whatsapp_client
from app.whatsapp.schemas import WhatsAppWebhookPayload

logger = logging.getLogger(__name__)


from app.integrations.whatsapp.service import WhatsAppService, trigger_booking_whatsapp_notifications

__all__ = ["WhatsAppService", "trigger_booking_whatsapp_notifications"]
