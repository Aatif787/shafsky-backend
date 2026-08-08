"""
WhatsApp Integration Service Layer.
Handles incoming webhook event processing (messages & status updates) and booking notification dispatchers.
"""

import os
import logging
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from app.integrations.whatsapp.client import whatsapp_client
from app.ai.service import AiService
from app.ai.schemas import ChatRequest

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Service layer bridging Meta WhatsApp Cloud API with Shafsky Core & AI Services."""

    @classmethod
    def handle_incoming_webhook(cls, db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parses Meta incoming webhook payload.
        Safely processes incoming messages and message status updates without assuming every event has a message.
        """
        if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
            return {"status": "ignored", "reason": "Not a whatsapp_business_account event"}

        messages_handled = 0
        statuses_handled = 0
        results = []

        entries = payload.get("entry", [])
        if not isinstance(entries, list):
            return {"status": "ignored", "reason": "Malformed entry structure"}

        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                val = change.get("value", {})
                if not isinstance(val, dict):
                    continue

                # 1. Process Message Status Updates (sent, delivered, read, failed)
                statuses = val.get("statuses", [])
                if isinstance(statuses, list):
                    for st in statuses:
                        status_name = st.get("status")
                        recipient_id = st.get("recipient_id")
                        msg_id = st.get("id")
                        statuses_handled += 1

                        if status_name == "failed":
                            errors = st.get("errors", [])
                            err_desc = errors[0].get("message") if errors else "Unknown failure"
                            logger.warning(
                                f"[WhatsApp Status] Delivery failed for message {msg_id} to {recipient_id}: {err_desc}"
                            )
                        else:
                            logger.info(f"[WhatsApp Status] Message {msg_id} status updated: {status_name}")

                # 2. Process Incoming Messages
                messages = val.get("messages", [])
                if isinstance(messages, list):
                    for msg in messages:
                        msg_type = msg.get("type")
                        from_phone = msg.get("from")
                        msg_id = msg.get("id")

                        if msg_type != "text" or not msg.get("text"):
                            logger.info(f"[WhatsApp Webhook] Skipping non-text message type '{msg_type}' from {from_phone}")
                            continue

                        user_text = msg["text"].get("body", "").strip()

                        # Extract profile name if present
                        sender_name = "Valued Guest"
                        contacts = val.get("contacts", [])
                        if contacts and isinstance(contacts, list) and len(contacts) > 0:
                            prof = contacts[0].get("profile", {})
                            if prof and prof.get("name"):
                                sender_name = prof.get("name")

                        # Build AI Chat Request
                        chat_req = ChatRequest(
                            session_id=f"wa_{from_phone}",
                            message=user_text,
                            phone_number=from_phone,
                            channel="WHATSAPP",
                            metadata={
                                "from_number": from_phone,
                                "sender_name": sender_name,
                                "whatsapp_msg_id": msg_id
                            }
                        )

                        # Process chat using AI engine
                        ai_res = AiService.process_chat(db, chat_req)

                        # Dispatch response back to WhatsApp recipient
                        send_res = whatsapp_client.send_text_message(
                            to_phone=from_phone,
                            message_body=ai_res.reply
                        )

                        messages_handled += 1
                        results.append({
                            "from_phone": from_phone,
                            "reply": ai_res.reply[:60] + "...",
                            "send_status": send_res
                        })

        return {
            "status": "processed",
            "messages_handled": messages_handled,
            "statuses_handled": statuses_handled,
            "results": results
        }

    @classmethod
    def send_customer_booking_confirmation(cls, booking: Any) -> Dict[str, Any]:
        """
        Dispatches customer booking confirmation message over WhatsApp Cloud API.
        """
        recipient_phone = getattr(booking, "passenger_phone", None)
        if not recipient_phone:
            logger.warning("[WhatsApp] Booking missing passenger phone number. Customer notification skipped.")
            return {"success": False, "error": "Missing passenger_phone"}

        booking_ref = getattr(booking, "booking_ref", "N/A")
        passenger_name = getattr(booking, "passenger_name", "Guest")
        flight_num = getattr(booking, "flight_num", "N/A")
        origin = getattr(booking, "origin_code", "ORIGIN")
        dest = getattr(booking, "dest_code", "DEST")
        service_cat = getattr(booking, "service_category", "Airport Service")

        msg_body = (
            f"✈️ *Shafsky Aviation VIP Service Confirmed*\n\n"
            f"Dear *{passenger_name}*,\n\n"
            f"Your booking reference *{booking_ref}* has been successfully confirmed.\n"
            f"• *Flight*: {flight_num} ({origin} ➔ {dest})\n"
            f"• *Service*: {service_cat}\n\n"
            f"Our dedicated airport concierge team will be waiting to greet you. "
            f"For immediate assistance or flight updates, reply to this message."
        )

        return whatsapp_client.send_text_message(recipient_phone, msg_body)

    @classmethod
    def send_officer_booking_notification(cls, booking: Any) -> Dict[str, Any]:
        """
        Dispatches VIP booking alert to duty officer phone over WhatsApp Cloud API.
        """
        officer_phone = os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "919599087959").strip()
        if not officer_phone:
            return {"success": False, "error": "Officer notification phone not configured"}

        booking_ref = getattr(booking, "booking_ref", "N/A")
        passenger_name = getattr(booking, "passenger_name", "Guest")
        passenger_phone = getattr(booking, "passenger_phone", "N/A")
        flight_num = getattr(booking, "flight_num", "N/A")
        origin = getattr(booking, "origin_code", "ORIGIN")
        dest = getattr(booking, "dest_code", "DEST")
        service_cat = getattr(booking, "service_category", "Airport Service")

        msg_body = (
            f"🛎️ *NEW VIP BOOKING ALERT — Shafsky Aviation*\n\n"
            f"• *Ref*: {booking_ref}\n"
            f"• *Passenger*: {passenger_name} ({passenger_phone})\n"
            f"• *Flight*: {flight_num} ({origin} ➔ {dest})\n"
            f"• *Service*: {service_cat}\n\n"
            f"Please assign duty concierge officer."
        )

        return whatsapp_client.send_text_message(officer_phone, msg_body)


def trigger_booking_whatsapp_notifications(booking: Any) -> None:
    """
    Non-blocking notification hook triggered upon successful booking creation.
    Fails gracefully with logged configuration warning if credentials are missing.
    """
    try:
        if not whatsapp_client.is_configured():
            logger.info(
                "[WhatsApp Notification Hook] WhatsApp integration is not configured. "
                "Booking creation completed normally, notification skipped."
            )
            return

        cust_res = WhatsAppService.send_customer_booking_confirmation(booking)
        logger.info(f"[WhatsApp Notification Hook] Customer dispatch result: {cust_res.get('status', 'complete')}")

        off_res = WhatsAppService.send_officer_booking_notification(booking)
        logger.info(f"[WhatsApp Notification Hook] Officer dispatch result: {off_res.get('status', 'complete')}")
    except Exception as err:
        logger.warning(f"[WhatsApp Notification Hook] Exception during notification dispatch: {err}")
