"""
WhatsApp Integration Service & Persistent Booking State Machine Engine.
Coordinator module composing domain flow mixins:
- AirportFlowMixin: Categories, Journey Types, Airports, Terminals, Service Packages
- FlightFlowMixin: AviationStack flight verification, mismatch handling, smart alignment
- DetailsFlowMixin: Travel dates, passengers, customer contact information
- CharterHotelFlowMixin: Private charter, luxury hotels, chauffeur ground transportation
- ReviewPaymentFlowMixin: Booking creation, Razorpay payment link checkout, idempotency
"""

import os
import uuid
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select

from app.models.whatsapp_models import WhatsAppConversation, WhatsAppMessage, WhatsAppWebhookEvent
from app.models.journey_models import SupportedAirport
from app.integrations.whatsapp.client import whatsapp_client
from app.integrations.whatsapp import copy as wa_copy
from app.integrations.whatsapp import delivery as wa_delivery
from app.integrations.whatsapp.handlers import (
    AirportFlowMixin,
    FlightFlowMixin,
    DetailsFlowMixin,
    CharterHotelFlowMixin,
    ReviewPaymentFlowMixin,
    OFFICIAL_CATEGORIES,
)
from app.utils.customer_email import (
    is_acceptable_customer_email as is_acceptable_whatsapp_customer_email,
    REAL_EMAIL_HELP as _REAL_EMAIL_HELP,
    EMAIL_FORMAT_HELP as _EMAIL_FORMAT_HELP,
)

logger = logging.getLogger(__name__)


class WhatsAppBookingStateMachine(
    AirportFlowMixin,
    FlightFlowMixin,
    DetailsFlowMixin,
    CharterHotelFlowMixin,
    ReviewPaymentFlowMixin,
):
    """
    Persistent state machine processor for Shafsky Aviation WhatsApp Booking Flow.
    Enforces: ONE USER MESSAGE = ONE VALIDATION = ONE STATE TRANSITION = ONE NEXT RESPONSE.
    """

    @classmethod
    def _transition_state(cls, db: Session, conv: WhatsAppConversation, new_state: str) -> None:
        """Helper to transition conversation state with standard audit logging."""
        old_state = conv.current_state
        if old_state != new_state:
            logger.info(f"[WhatsApp Session] State changed: {old_state} -> {new_state} for {conv.phone_number}")
            conv.current_state = new_state
            conv.updated_at = datetime.now(timezone.utc)
            db.commit()

    @classmethod
    def _reset_conversation_fields(cls, conv: WhatsAppConversation) -> WhatsAppConversation:
        """Resets all booking-specific fields for a new session."""
        conv.selected_category = None
        conv.selected_service_id = None
        conv.selected_service_name = None
        conv.requires_airport = True
        conv.requires_flight = True
        conv.requires_date = True
        conv.requires_passenger_count = True
        conv.selected_airport_iata = None
        conv.selected_airport_name = None
        conv.selected_airport_city = None
        conv.selected_airport_country = None
        conv.flight_num = None
        conv.flight_details_json = None
        conv.booking_date = None
        conv.passenger_count = 1
        conv.customer_name = None
        conv.customer_email = None
        conv.customer_phone = None
        conv.additional_requirements = None
        conv.total_amount = None
        conv.booking_id = None
        conv.booking_ref = None
        conv.payment_status = "PENDING"
        conv.razorpay_order_id = None
        conv.razorpay_payment_id = None
        conv.razorpay_payment_link_id = None
        conv.razorpay_payment_url = None
        return conv

    @classmethod
    def get_or_create_conversation(cls, db: Session, phone_number: str) -> Tuple[WhatsAppConversation, bool]:
        """
        Retrieves active conversation session or creates a new one.
        Enforces configurable session inactivity expiry (default 15 minutes).
        Returns: (conversation_object, is_session_expired_flag)
        """
        clean_phone = "".join(filter(str.isdigit, str(phone_number)))
        stmt = select(WhatsAppConversation).where(WhatsAppConversation.phone_number == clean_phone)
        conv = db.execute(stmt).scalar_one_or_none()

        is_expired = False
        now_utc = datetime.now(timezone.utc)
        timeout_minutes = int(os.getenv("WHATSAPP_SESSION_TIMEOUT_MINUTES", "15"))
        timeout_seconds = timeout_minutes * 60

        if not conv:
            conv = WhatsAppConversation(
                id=uuid.uuid4(),
                phone_number=clean_phone,
                current_state="START",
                customer_phone=clean_phone,
                created_at=now_utc,
                updated_at=now_utc
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)
            logger.info(f"[WhatsApp Session] New session for {clean_phone}")
        else:
            last_act = conv.updated_at or conv.created_at
            if last_act:
                if last_act.tzinfo is None:
                    diff_utc = (now_utc - last_act.replace(tzinfo=timezone.utc)).total_seconds()
                    diff_local = (datetime.now() - last_act).total_seconds()
                    inactivity_seconds = min(abs(diff_utc), abs(diff_local))
                else:
                    inactivity_seconds = (now_utc - last_act).total_seconds()

                protected_states = {
                    "START", "CANCELLED", "BOOKING_CONFIRMED",
                    "WAITING_PAYMENT", "PENDING_PAYMENT", "COMPLETED", "PAYMENT_PROCESSING",
                }
                if inactivity_seconds > timeout_seconds and conv.current_state not in protected_states:
                    logger.info(f"[WhatsApp Session] Session expired for {clean_phone} after {inactivity_seconds:.0f}s inactivity.")
                    conv = cls._reset_conversation_fields(conv)
                    conv.current_state = "START"
                    conv.updated_at = now_utc
                    db.commit()
                    db.refresh(conv)
                    is_expired = True
                else:
                    logger.info(f"[WhatsApp Session] Existing session for {clean_phone} (state: {conv.current_state})")
            else:
                logger.info(f"[WhatsApp Session] Existing session for {clean_phone} (state: {conv.current_state})")

        return conv, is_expired

    @classmethod
    def process_incoming_event(
        cls,
        db: Session,
        from_phone: str,
        user_input: str,
        input_type: str = "text",
        input_id: Optional[str] = None,
        msg_id: Optional[str] = None,
        raw_payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Main entry point for processing customer inputs against current conversation state.
        Strictly enforces ONE MESSAGE = ONE STATE TRANSITION = ONE NEXT RESPONSE.
        """
        try:
            conv, session_expired = cls.get_or_create_conversation(db, from_phone)

            log_msg = WhatsAppMessage(
                id=uuid.uuid4(),
                conversation_id=conv.id,
                message_id=msg_id,
                direction="INBOUND",
                message_type=input_type,
                content=user_input,
                raw_payload=raw_payload,
                created_at=datetime.now(timezone.utc)
            )
            db.add(log_msg)
            conv.updated_at = datetime.now(timezone.utc)
            db.commit()

            text_clean = (user_input or "").strip()
            text_lower = text_clean.lower()

            if session_expired:
                logger.info(f"[WhatsApp Session] Expired session restarted for {conv.phone_number}")
                conv = cls._reset_conversation_fields(conv)
                cls._transition_state(db, conv, "CATEGORY_SELECTION")
                result = cls._send_category_menu(db, conv, prefix_notice=wa_copy.EXPIRED_PREFIX)
                if not result.get("success") and result.get("status") != "category_menu_sent":
                    logger.warning(f"[WhatsApp Session] Failed to send expired session notification for {conv.phone_number}")
                    cls._send_fallback_message(conv.phone_number, "Your session has expired. Please type 'Hi' to start again.")
                return result

            # 1. Global Interrupts (Cancel, Help, Back)
            CANCEL_COMMANDS = {"cancel", "stop", "abort"}
            if text_lower in CANCEL_COMMANDS or input_id == "btn_cancel":
                cls._transition_state(db, conv, "CANCELLED")
                msg = (
                    "*Shafsky Aviation Services*\n\n"
                    "Your booking process has been cancelled.\n\n"
                    "Type *Hi* anytime to begin a new reservation."
                )
                wa_delivery.send_text(conv.phone_number, msg, client=whatsapp_client)
                return {"status": "cancelled", "state": conv.current_state}

            HELP_COMMANDS = {"help", "support", "info"}
            if text_lower in HELP_COMMANDS and conv.current_state not in ("WAITING_PAYMENT", "PENDING_PAYMENT"):
                help_msg = (
                    "*Shafsky Aviation Services*\n\n"
                    f"{wa_copy.SESSION_TIMEOUT_HINT}\n\n"
                    "• Type *Hi* to restart your booking.\n"
                    "• Type *BACK* to return to the previous step.\n"
                    "• Type *CANCEL* to cancel your current booking.\n"
                    "• Reply with a number to choose a listed service.\n\n"
                    "Airport services: 12 hours' notice for domestic, 24 hours for international.\n"
                    "For urgent assistance, call our executive on *+91-9599087959*."
                )
                wa_delivery.send_text(conv.phone_number, help_msg, client=whatsapp_client)
                return {"status": "help_sent", "state": conv.current_state}

            # Waiting for payment: do not wipe booking fields; Hi must not start a second booking.
            if conv.current_state in ("WAITING_PAYMENT", "PENDING_PAYMENT"):
                return cls._state_waiting_payment(db, conv, user_input, input_id)

            # 2. Global Restart Commands (Hi, Hello, Start, Menu, 0)
            RESTART_COMMANDS = {
                "hi", "hello", "hey", "start", "menu", "restart", "main menu", "0",
                "reset", "start over"
            }
            RESTART_BUTTON_IDS = {"btn_restart", "btn_menu", "btn_main_menu"}

            if text_lower in RESTART_COMMANDS or input_id in RESTART_BUTTON_IDS:
                logger.info(f"[WhatsApp Session] Restart command detected for {conv.phone_number} (input: '{user_input}')")
                conv = cls._reset_conversation_fields(conv)
                cls._transition_state(db, conv, "CATEGORY_SELECTION")
                result = cls._send_category_menu(db, conv)
                if not result.get("success") and result.get("status") != "category_menu_sent":
                    logger.warning(f"[WhatsApp Session] Failed to send category menu for {conv.phone_number}")
                    cls._send_fallback_message(conv.phone_number, "Welcome to Shafsky Aviation Services. Please select a service category.")
                return result

            if text_lower in HELP_COMMANDS:
                help_msg = (
                    "*Shafsky Aviation Services*\n\n"
                    f"{wa_copy.SESSION_TIMEOUT_HINT}\n\n"
                    "• Type *Hi* to restart your booking.\n"
                    "• Type *BACK* to return to the previous step.\n"
                    "• Type *CANCEL* to cancel your current booking.\n"
                    "• Reply with a number to choose a listed service.\n\n"
                    "Airport services: 12 hours' notice for domestic, 24 hours for international.\n"
                    "For urgent assistance, call our executive on *+91-9599087959*."
                )
                wa_delivery.send_text(conv.phone_number, help_msg, client=whatsapp_client)
                return {"status": "help_sent", "state": conv.current_state}

            if text_lower == "back" or input_id == "btn_back":
                cls._handle_back_action(db, conv)
                return {"status": "back", "state": conv.current_state}

            # 3. Route by State
            state = conv.current_state

            if state in ["START", "CANCELLED", "BOOKING_CONFIRMED"]:
                result = cls._state_start(db, conv, user_input)
            elif state == "CATEGORY_SELECTION":
                result = cls._state_category_selection(db, conv, user_input, input_id)
            elif state in ["JOURNEY_TYPE_SELECTION", "AIRPORT_JOURNEY_TYPE"]:
                result = cls._state_journey_type_selection(db, conv, user_input, input_id)
            elif state == "AIRPORT_TRAVEL_TYPE":
                result = cls._state_travel_type_selection(db, conv, user_input, input_id)
            elif state == "AIRPORT_TRANSIT_TYPE":
                result = cls._state_transit_type_selection(db, conv, user_input, input_id)
            elif state == "AIRPORT_SELECTION":
                result = cls._state_airport_selection(db, conv, user_input)
            elif state == "AIRPORT_CONFIRMATION":
                result = cls._state_airport_confirmation(db, conv, user_input, input_id)
            elif state == "TERMINAL_SELECTION":
                result = cls._state_terminal_selection(db, conv, user_input, input_id)
            elif state in ["SERVICE_SELECTION", "AIRPORT_PACKAGE_SELECTION"]:
                result = cls._state_service_selection(db, conv, user_input, input_id)
            elif state == "HOTEL_TRANSPORT_SUBMENU":
                result = cls._state_hotel_transport_submenu(db, conv, user_input, input_id)
            elif state == "CHARTER_ORIGIN":
                result = cls._state_charter_origin(db, conv, user_input)
            elif state == "CHARTER_DESTINATION":
                result = cls._state_charter_destination(db, conv, user_input)
            elif state == "TRANSPORT_PICKUP":
                result = cls._state_transport_pickup(db, conv, user_input)
            elif state == "TRANSPORT_DROPOFF":
                result = cls._state_transport_dropoff(db, conv, user_input)
            elif state == "HOTEL_CITY":
                result = cls._state_hotel_city(db, conv, user_input)
            elif state == "HOTEL_NIGHTS":
                result = cls._state_hotel_nights(db, conv, user_input)
            elif state == "FLIGHT_INPUT":
                result = cls._state_flight_input(db, conv, user_input, input_id)
            elif state == "FLIGHT_CONFIRMATION":
                result = cls._state_flight_confirmation(db, conv, user_input, input_id)
            elif state == "DATE_SELECTION":
                result = cls._state_date_selection(db, conv, user_input)
            elif state == "PASSENGER_COUNT":
                result = cls._state_passenger_count(db, conv, user_input)
            elif state == "CUSTOMER_NAME":
                result = cls._state_customer_name(db, conv, user_input)
            elif state == "CUSTOMER_EMAIL":
                result = cls._state_customer_email(db, conv, user_input)
            elif state == "CUSTOMER_PHONE":
                result = cls._state_customer_phone(db, conv, user_input)
            elif state == "ADDITIONAL_REQUIREMENTS":
                result = cls._state_additional_requirements(db, conv, user_input)
            elif state == "BOOKING_REVIEW":
                result = cls._state_booking_review(db, conv, user_input, input_id)
            elif state in ["WAITING_PAYMENT", "PENDING_PAYMENT"]:
                result = cls._state_waiting_payment(db, conv, user_input, input_id)
            else:
                result = cls._state_start(db, conv, user_input)

            if not result.get("success") and result.get("status") not in [
                "invalid_category",
                "invalid_journey_type",
                "invalid_travel_type",
                "invalid_transit_type",
                "invalid_terminal",
                "invalid_service",
                "invalid_flight_format",
                "invalid_flight_confirmation",
                "flight_airport_mismatch",
                "flight_not_found",
                "flight_verify_failed",
                "flight_verify_timeout",
                "flight_verify_rate_limited",
                "flight_verify_not_configured",
                "flight_reverify_required",
                "flight_incomplete",
                "flight_ambiguous",
                "unverified_flight_blocked",
                "booking_cutoff_blocked",
                "invalid_date_format",
                "past_date_rejected",
                "cutoff_violation",
                "invalid_passenger_count",
                "invalid_name",
                "invalid_email",
                "invalid_phone",
                "invalid_summary_choice",
                "empty_airport_query",
                "unsupported_airport",
            ]:
                logger.warning(f"[WhatsApp Session] State handler returned unsuccessful result for {conv.phone_number} in state {state}")
                cls._send_fallback_message(conv.phone_number, "Please select an option from the menu above, or type *BACK* to return to the previous step.")

            return result

        except Exception as e:
            logger.error(f"[WhatsApp Session] Exception in process_incoming_event for {from_phone}: {str(e)}", exc_info=True)
            cls._send_fallback_message(from_phone, "I encountered an issue processing your request. Please reply with your selection, or type *BACK* to return to the previous step.")
            return {"status": "error", "error": str(e)}

    @classmethod
    def _send_fallback_message(cls, phone_number: str, message: str) -> None:
        """Sends a simple text fallback message when interactive messages fail."""
        wa_delivery.send_text(phone_number, message, client=whatsapp_client)

    @classmethod
    def _merge_flight_details(cls, db: Session, conv: WhatsAppConversation, extra: Dict[str, Any]) -> None:
        meta = dict(conv.flight_details_json) if isinstance(conv.flight_details_json, dict) else {}
        meta.update(extra)
        conv.flight_details_json = meta
        flag_modified(conv, "flight_details_json")
        db.commit()

    @classmethod
    def _store_wa_menu(cls, db: Session, conv: WhatsAppConversation, items: list) -> None:
        cls._merge_flight_details(db, conv, {"_wa_menu": items})

    @classmethod
    def _handle_back_action(cls, db: Session, conv: WhatsAppConversation):
        """Reverts to the previous logical state and cleans dependent fields."""
        curr = conv.current_state
        if curr in ["CATEGORY_SELECTION", "START"]:
            cls._state_start(db, conv, "Hi")
        elif curr in ["JOURNEY_TYPE_SELECTION", "AIRPORT_JOURNEY_TYPE"]:
            cls._send_category_menu(db, conv)
        elif curr == "AIRPORT_TRAVEL_TYPE":
            cls._transition_state(db, conv, "JOURNEY_TYPE_SELECTION")
            cls._prompt_journey_type(conv)
        elif curr == "AIRPORT_TRANSIT_TYPE":
            cls._transition_state(db, conv, "JOURNEY_TYPE_SELECTION")
            cls._prompt_journey_type(conv)
        elif curr == "AIRPORT_SELECTION":
            jt = (conv.flight_details_json or {}).get("journey_type", "DEPARTURE") if isinstance(conv.flight_details_json, dict) else "DEPARTURE"
            if jt == "TRANSIT":
                cls._transition_state(db, conv, "AIRPORT_TRANSIT_TYPE")
                cls._prompt_transit_type(conv)
            else:
                cls._transition_state(db, conv, "AIRPORT_TRAVEL_TYPE")
                cls._prompt_travel_type(conv, jt.title())
        elif curr == "AIRPORT_CONFIRMATION":
            conv.selected_airport_iata = None
            conv.selected_airport_name = None
            cls._transition_state(db, conv, "AIRPORT_SELECTION")
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL):")
        elif curr == "TERMINAL_SELECTION":
            cls._transition_state(db, conv, "AIRPORT_SELECTION")
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL):")
        elif curr in ["SERVICE_SELECTION", "AIRPORT_PACKAGE_SELECTION"]:
            if conv.requires_airport:
                metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
                airport = db.execute(select(SupportedAirport).where(SupportedAirport.iata_code == conv.selected_airport_iata)).scalar_one_or_none()
                if airport:
                    jt_back = metadata.get("journey_type", "DEPARTURE")
                    tt_back = metadata.get("travel_type", "DOMESTIC")
                    tt_back, route_err = cls._authoritative_catalog_travel_type(db, conv, jt_back, tt_back)
                    if route_err:
                        return cls._send_route_classification_error(conv, route_err)
                    applicable_terminals = cls._get_applicable_terminals(
                        db, airport, jt_back, tt_back
                    )
                    if len(applicable_terminals) > 1:
                        cls._transition_state(db, conv, "TERMINAL_SELECTION")
                        cls._prompt_terminal_selection(conv, airport, applicable_terminals)
                        return
                cls._transition_state(db, conv, "AIRPORT_SELECTION")
                whatsapp_client.send_text_message(conv.phone_number, "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL):")
            else:
                cls._send_category_menu(db, conv)
        elif curr in ["FLIGHT_INPUT", "FLIGHT_CONFIRMATION"]:
            conv.flight_num = None
            conv.flight_details_json = None
            cls._transition_state(db, conv, "SERVICE_SELECTION")
            if conv.requires_airport:
                cls._send_airport_services_menu(db, conv)
            else:
                cls._send_service_menu(db, conv, conv.selected_category or "Airport Services")
        elif curr == "DATE_SELECTION":
            if conv.requires_flight:
                cls._transition_state(db, conv, "FLIGHT_INPUT")
                whatsapp_client.send_text_message(conv.phone_number, "Please enter your Flight Number (e.g., *EK501*, *AI2424*):")
            else:
                cls._transition_state(db, conv, "SERVICE_SELECTION")
                if conv.requires_airport:
                    cls._send_airport_services_menu(db, conv)
                else:
                    cls._send_service_menu(db, conv, conv.selected_category or "Airport Services")
        elif curr == "PASSENGER_COUNT":
            cls._transition_state(db, conv, "DATE_SELECTION")
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):")
        elif curr == "CUSTOMER_NAME":
            cls._transition_state(db, conv, "PASSENGER_COUNT")
            whatsapp_client.send_text_message(conv.phone_number, "How many passengers will be travelling? (Enter a number, e.g., 2):")
        elif curr == "CUSTOMER_EMAIL":
            cls._transition_state(db, conv, "CUSTOMER_NAME")
            whatsapp_client.send_text_message(conv.phone_number, "May I have your full name?")
        elif curr == "CUSTOMER_PHONE":
            cls._transition_state(db, conv, "CUSTOMER_EMAIL")
            whatsapp_client.send_text_message(conv.phone_number, "Please provide your email address for booking confirmation:")
        elif curr == "ADDITIONAL_REQUIREMENTS":
            cls._transition_state(db, conv, "CUSTOMER_PHONE")
            whatsapp_client.send_text_message(conv.phone_number, "Please provide your contact phone number (or type 'Same'):")
        elif curr == "BOOKING_REVIEW":
            cls._transition_state(db, conv, "ADDITIONAL_REQUIREMENTS")
            whatsapp_client.send_text_message(conv.phone_number, "Do you have any special requirements or notes? (Type *None* if no special requests):")
        elif curr in ["WAITING_PAYMENT", "PENDING_PAYMENT"]:
            cls._state_waiting_payment(db, conv, "resend")
            return
        else:
            cls._send_category_menu(db, conv)


class WhatsAppService:
    """Unified WhatsApp Ingestion & Webhook Handler with Event Idempotency."""

    @classmethod
    def _send_fallback_message(cls, phone_number: str, message: str) -> None:
        try:
            whatsapp_client.send_text_message(phone_number, message)
        except Exception as e:
            logger.error(f"[WhatsApp Service] Failed to send fallback message to {phone_number}: {str(e)}")

    @classmethod
    def handle_incoming_webhook(cls, db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Parses Meta webhook events safely with event-level idempotency."""
        try:
            if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
                return {"status": "ignored", "reason": "Not a whatsapp_business_account event"}

            messages_handled = 0
            statuses_handled = 0
            results = []

            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    val = change.get("value", {})
                    if not isinstance(val, dict):
                        continue

                    for st in val.get("statuses", []):
                        statuses_handled += 1
                        logger.info(f"[WhatsApp Webhook Status] Message {st.get('id')} status: {st.get('status')}")

                    for msg in val.get("messages", []):
                        msg_id = msg.get("id")
                        from_phone = msg.get("from")

                        if msg_id:
                            try:
                                dup = db.execute(select(WhatsAppWebhookEvent).where(WhatsAppWebhookEvent.event_id == msg_id)).scalar_one_or_none()
                                if dup:
                                    logger.info(f"[WhatsApp Webhook] Duplicate message ignored: {msg_id}")
                                    continue

                                evt = WhatsAppWebhookEvent(id=uuid.uuid4(), event_id=msg_id, event_type="message", payload=msg)
                                db.add(evt)
                                db.commit()
                            except Exception as db_err:
                                db.rollback()
                                logger.error(f"[WhatsApp Webhook] Database error during event storage for {from_phone}: {str(db_err)}")

                        msg_type = msg.get("type")
                        user_text = ""
                        input_id = None

                        if msg_type == "text":
                            user_text = msg.get("text", {}).get("body", "").strip()
                        elif msg_type == "interactive":
                            inter = msg.get("interactive", {})
                            i_type = inter.get("type")
                            if i_type == "button_reply":
                                btn = inter.get("button_reply", {})
                                input_id = btn.get("id")
                                user_text = btn.get("title", "")
                            elif i_type == "list_reply":
                                lst = inter.get("list_reply", {})
                                input_id = lst.get("id")
                                user_text = lst.get("title", "")

                        if not user_text and not input_id:
                            continue

                        try:
                            res = WhatsAppBookingStateMachine.process_incoming_event(
                                db=db,
                                from_phone=from_phone,
                                user_input=user_text,
                                input_type=msg_type,
                                input_id=input_id,
                                msg_id=msg_id,
                                raw_payload=msg
                            )
                            messages_handled += 1
                            results.append({"from": from_phone, "result": res})
                        except Exception as state_err:
                            logger.error(f"[WhatsApp Webhook] State machine error for {from_phone}: {str(state_err)}", exc_info=True)
                            try:
                                cls._send_fallback_message(from_phone, "I apologize, but I encountered an error. Please type 'Hi' to restart.")
                            except Exception as fallback_err:
                                logger.error(f"[WhatsApp Webhook] Failed to send fallback message: {str(fallback_err)}")
                            results.append({"from": from_phone, "result": {"status": "error", "error": str(state_err)}})

            return {
                "status": "processed",
                "messages_handled": messages_handled,
                "statuses_handled": statuses_handled,
                "results": results
            }

        except Exception as e:
            logger.error(f"[WhatsApp Webhook] Exception in handle_incoming_webhook: {str(e)}", exc_info=True)
            return {"status": "error", "error": str(e)}


def trigger_booking_whatsapp_notifications(booking: Any) -> None:
    """Non-blocking notification helper for direct web bookings."""
    try:
        if not whatsapp_client.is_configured():
            return
        officer_phone = os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "919599087959").strip()
        msg = (
            "🚨 *NEW DIRECT BOOKING CREATED*\n\n"
            f"• *Booking Ref*: {getattr(booking, 'booking_ref', 'N/A')}\n"
            f"• *Customer*: {getattr(booking, 'passenger_name', 'N/A')} ({getattr(booking, 'passenger_phone', 'N/A')})\n"
            f"• *Service*: {getattr(booking, 'service_type', 'N/A')}\n"
            f"• *Airport*: {getattr(booking, 'origin_code', 'N/A')}\n"
            f"• *Amount*: ₹{int(getattr(booking, 'total_amount', 0)):,}\n"
            f"• *Status*: {getattr(booking, 'status', 'PENDING')}"
        )
        whatsapp_client.send_text_message(officer_phone, msg)
    except Exception as err:
        logger.warning(f"[WhatsApp Notification Hook] Exception: {err}")
