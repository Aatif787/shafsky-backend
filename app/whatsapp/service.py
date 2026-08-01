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


class WhatsAppService:
    """Service layer bridging Meta WhatsApp Cloud API with AI Conversation Service."""

    @classmethod
    def handle_incoming_webhook(cls, db: Session, payload: WhatsAppWebhookPayload) -> Dict[str, Any]:
        """Parses Meta incoming webhook payload and routes text messages to AI engine."""
        dispatched_count = 0
        results = []

        if payload.object != "whatsapp_business_account":
            return {"status": "ignored", "reason": "Not a whatsapp_business_account event"}

        for entry in payload.entry:
            for change in entry.changes:
                val = change.value
                messages = val.messages or []

                for msg in messages:
                    if msg.type != "text" or not msg.text:
                        logger.info(f"Skipping non-text message type '{msg.type}' from {msg.from_number}")
                        continue

                    from_phone = msg.from_number
                    user_text = msg.text.body.strip()

                    # Extract sender contact profile name if present
                    sender_name = "Valued Guest"
                    if val.contacts:
                        contact_profile = val.contacts[0].profile
                        if contact_profile and contact_profile.name:
                            sender_name = contact_profile.name

                    # Build AI Chat Request
                    chat_req = ChatRequest(
                        session_id=f"wa_{from_phone}",
                        message=user_text,
                        phone_number=from_phone,
                        channel="WHATSAPP",
                        metadata={
                            "from_number": from_phone,
                            "sender_name": sender_name,
                            "whatsapp_msg_id": msg.id
                        }
                    )

                    # Delegate to AI Service Engine
                    ai_res = AiService.process_chat(db, chat_req)

                    # Dispatch reply via Meta WhatsApp Client
                    send_res = whatsapp_client.send_text_message(
                        to_phone=from_phone,
                        message_body=ai_res.reply
                    )

                    dispatched_count += 1
                    results.append({
                        "from_phone": from_phone,
                        "reply": ai_res.reply[:50] + "...",
                        "send_status": send_res
                    })

        return {
            "status": "processed",
            "messages_handled": dispatched_count,
            "results": results
        }
