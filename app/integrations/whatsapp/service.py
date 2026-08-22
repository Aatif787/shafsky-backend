"""
WhatsApp Integration Service & Persistent Booking State Machine Engine.
Handles Meta Webhooks, Idempotency, 4-Option Customer Menu, Database-Driven Airport Services,
Pure Local Flight Validation, Session Expiry (30 mins), and Payment-Free Booking Ingestion.
"""

import os
import re
import uuid
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, and_

from app.models.whatsapp_models import WhatsAppConversation, WhatsAppMessage, WhatsAppWebhookEvent
from app.models.schema import Booking, BookingStatus
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.integrations.whatsapp.client import whatsapp_client
from app.services.service_config_service import ServiceConfigService, DEFAULT_SERVICE_CATALOG
from app.services.journey_engine import JourneyDetectionEngine
from app.services.booking_service import BookingService

logger = logging.getLogger(__name__)

# Final 4 Customer-Facing Service Categories for WhatsApp (Cargo & Medical preserved in DB catalogue)
OFFICIAL_CATEGORIES = [
    {"id": "cat_airport", "name": "Airport Services", "db_categories": ["Airport Assistance", "Airport Services"]},
    {"id": "cat_travel", "name": "Travel Services", "db_categories": ["Travel Support", "Travel Services"]},
    {"id": "cat_charter", "name": "Private Charter", "db_categories": ["Private Charter"]},
    {"id": "cat_hotel_transport", "name": "Hotel & Transportation", "db_categories": ["Ground Transport", "Transportation Services", "Travel Support", "Hotel Services"]},
]


class WhatsAppBookingStateMachine:
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
        return conv

    @classmethod
    def get_or_create_conversation(cls, db: Session, phone_number: str) -> Tuple[WhatsAppConversation, bool]:
        """
        Retrieves active conversation session or creates a new one.
        Enforces configurable session inactivity expiry (default 30 minutes).
        Returns: (conversation_object, is_session_expired_flag)
        """
        clean_phone = "".join(filter(str.isdigit, str(phone_number)))
        stmt = select(WhatsAppConversation).where(WhatsAppConversation.phone_number == clean_phone)
        conv = db.execute(stmt).scalar_one_or_none()

        is_expired = False
        now_utc = datetime.now(timezone.utc)
        timeout_minutes = int(os.getenv("WHATSAPP_SESSION_TIMEOUT_MINUTES", "30"))
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
            # Check inactivity expiry for non-terminal active sessions
            last_act = conv.updated_at or conv.created_at
            if last_act:
                # Normalize timezone if naive
                if last_act.tzinfo is None:
                    last_act = last_act.replace(tzinfo=timezone.utc)
                inactivity_seconds = (now_utc - last_act).total_seconds()
                if inactivity_seconds > timeout_seconds and conv.current_state not in ["START", "CANCELLED", "BOOKING_CONFIRMED"]:
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
        input_type: str = "text",  # text, button_reply, list_reply
        input_id: Optional[str] = None,
        msg_id: Optional[str] = None,
        raw_payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Main entry point for processing customer inputs against current conversation state.
        Strictly enforces ONE MESSAGE = ONE STATE TRANSITION = ONE RESPONSE.
        Checked in strict order:
        1. Log inbound message
        2. Session Expiration Handling
        3. Global Restart Commands ("hi", "hello", "hey", "start", "menu", "restart", "main menu", "0")
        4. Global Interrupts (Cancel, Help, Back)
        5. State-specific Handlers
        """
        conv, session_expired = cls.get_or_create_conversation(db, from_phone)

        # Log inbound message
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
        text_upper = text_clean.upper()

        # Handle expired session notification
        if session_expired:
            logger.info(f"[WhatsApp Session] Expired session restarted for {conv.phone_number}")
            conv = cls._reset_conversation_fields(conv)
            cls._transition_state(db, conv, "CATEGORY_SELECTION")
            return cls._send_category_menu(db, conv, prefix_notice="Your previous session has expired. Let's start again.\n\n")

        # ── 1. GLOBAL RESTART COMMANDS (HI, HELLO, HEY, START, MENU, RESTART, MAIN MENU, 0) ──
        # Checked BEFORE any state-specific handlers. "Hi" MUST ALWAYS WIN.
        RESTART_COMMANDS = {
            "hi", "hello", "hey", "start", "menu", "restart", "main menu", "0",
            "reset", "start over"
        }
        RESTART_BUTTON_IDS = {"btn_restart", "btn_menu", "btn_main_menu"}

        if text_lower in RESTART_COMMANDS or input_id in RESTART_BUTTON_IDS:
            logger.info(f"[WhatsApp Session] Restart command detected for {conv.phone_number} (input: '{user_input}')")
            conv = cls._reset_conversation_fields(conv)
            cls._transition_state(db, conv, "CATEGORY_SELECTION")
            return cls._send_category_menu(db, conv)

        # ── 2. GLOBAL INTERRUPT COMMANDS (CANCEL, HELP, BACK) ──
        CANCEL_COMMANDS = {"cancel", "stop", "abort"}
        if text_lower in CANCEL_COMMANDS or input_id == "btn_cancel":
            cls._transition_state(db, conv, "CANCELLED")
            msg = "Your booking process has been cancelled. Type *Hi* anytime to start a new booking with Shafsky Aviation."
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "cancelled", "state": conv.current_state}

        HELP_COMMANDS = {"help", "support", "info"}
        if text_lower in HELP_COMMANDS:
            help_msg = (
                "ℹ️ *Shafsky Aviation Assistance*\n\n"
                "You are using our automated booking system.\n"
                "• Type *Hi* to restart your booking.\n"
                "• Type *BACK* to return to the previous step.\n"
                "• Type *CANCEL* to cancel your current booking.\n\n"
                "For direct representative support, call: +91-9599087959."
            )
            whatsapp_client.send_text_message(conv.phone_number, help_msg)
            return {"status": "help_sent", "state": conv.current_state}

        if text_lower == "back" or input_id == "btn_back":
            cls._handle_back_action(db, conv)
            return {"status": "back", "state": conv.current_state}

        # ── 3. ROUTE BY STATE ──
        # ── 3. ROUTE BY STATE ──
        state = conv.current_state

        if state in ["START", "CANCELLED", "BOOKING_CONFIRMED"]:
            return cls._state_start(db, conv, user_input)

        elif state == "CATEGORY_SELECTION":
            return cls._state_category_selection(db, conv, user_input, input_id)

        elif state in ["JOURNEY_TYPE_SELECTION", "AIRPORT_JOURNEY_TYPE"]:
            return cls._state_journey_type_selection(db, conv, user_input, input_id)

        elif state == "AIRPORT_TRAVEL_TYPE":
            return cls._state_travel_type_selection(db, conv, user_input, input_id)

        elif state == "AIRPORT_TRANSIT_TYPE":
            return cls._state_transit_type_selection(db, conv, user_input, input_id)

        elif state == "AIRPORT_SELECTION":
            return cls._state_airport_selection(db, conv, user_input)

        elif state == "AIRPORT_CONFIRMATION":
            return cls._state_airport_confirmation(db, conv, user_input, input_id)

        elif state in ["SERVICE_SELECTION", "AIRPORT_PACKAGE_SELECTION"]:
            return cls._state_service_selection(db, conv, user_input, input_id)

        elif state == "HOTEL_TRANSPORT_SUBMENU":
            return cls._state_hotel_transport_submenu(db, conv, user_input, input_id)

        elif state == "CHARTER_ORIGIN":
            return cls._state_charter_origin(db, conv, user_input)

        elif state == "CHARTER_DESTINATION":
            return cls._state_charter_destination(db, conv, user_input)

        elif state == "TRANSPORT_PICKUP":
            return cls._state_transport_pickup(db, conv, user_input)

        elif state == "TRANSPORT_DROPOFF":
            return cls._state_transport_dropoff(db, conv, user_input)

        elif state == "HOTEL_CITY":
            return cls._state_hotel_city(db, conv, user_input)

        elif state == "HOTEL_NIGHTS":
            return cls._state_hotel_nights(db, conv, user_input)

        elif state == "FLIGHT_INPUT":
            return cls._state_flight_input(db, conv, user_input)

        elif state == "FLIGHT_CONFIRMATION":
            return cls._state_flight_confirmation(db, conv, user_input, input_id)

        elif state == "DATE_SELECTION":
            return cls._state_date_selection(db, conv, user_input)

        elif state == "PASSENGER_COUNT":
            return cls._state_passenger_count(db, conv, user_input)

        elif state == "CUSTOMER_NAME":
            return cls._state_customer_name(db, conv, user_input)

        elif state == "CUSTOMER_EMAIL":
            return cls._state_customer_email(db, conv, user_input)

        elif state == "CUSTOMER_PHONE":
            return cls._state_customer_phone(db, conv, user_input)

        elif state == "ADDITIONAL_REQUIREMENTS":
            return cls._state_additional_requirements(db, conv, user_input)

        elif state == "BOOKING_REVIEW":
            return cls._state_booking_review(db, conv, user_input, input_id)

        # Fallback reset
        return cls._state_start(db, conv, user_input)

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
        elif curr in ["SERVICE_SELECTION", "AIRPORT_PACKAGE_SELECTION"]:
            if conv.requires_airport:
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
        else:
            cls._send_category_menu(db, conv)

    # ── STATE IMPLEMENTATIONS ──

    @classmethod
    def _state_start(cls, db: Session, conv: WhatsAppConversation, user_input: str) -> Dict[str, Any]:
        """Greeting and display of final 4-option main menu."""
        conv = cls._reset_conversation_fields(conv)
        cls._transition_state(db, conv, "CATEGORY_SELECTION")
        return cls._send_category_menu(db, conv)

    @classmethod
    def _send_category_menu(cls, db: Session, conv: WhatsAppConversation, prefix_notice: str = "") -> Dict[str, Any]:
        """Sends the exact 4-option main menu."""
        cls._transition_state(db, conv, "CATEGORY_SELECTION")

        body_text = (
            f"{prefix_notice}"
            "Welcome to Shafsky Aviation ✈️\n\n"
            "How can we assist you today?\n\n"
            "1️⃣ Airport Services\n"
            "2️⃣ Travel Services\n"
            "3️⃣ Private Charter\n"
            "4️⃣ Hotel & Transportation"
        )

        rows = [
            {"id": "cat_airport", "title": "Airport Services", "description": "Meet & Assist, Fast Track, Lounge"},
            {"id": "cat_travel", "title": "Travel Services", "description": "Visa, Insurance, Travel Support"},
            {"id": "cat_charter", "title": "Private Charter", "description": "Private Jet & Helicopter Charter"},
            {"id": "cat_hotel_transport", "title": "Hotel & Transportation", "description": "Luxury Hotels & Airport Transfers"},
        ]

        sections = [{"title": "Shafsky Service Menu", "rows": rows}]

        res = whatsapp_client.send_interactive_list(
            to_phone=conv.phone_number,
            body_text=body_text,
            button_title="View Options",
            sections=sections,
            header_text="Shafsky Aviation"
        )

        if not res.get("success"):
            fallback_text = (
                f"{prefix_notice}"
                "Welcome to Shafsky Aviation ✈️\n\n"
                "How can we assist you today?\n\n"
                "1️⃣ Airport Services\n"
                "2️⃣ Travel Services\n"
                "3️⃣ Private Charter\n"
                "4️⃣ Hotel & Transportation\n\n"
                "Please reply with *1*, *2*, *3*, or *4*."
            )
            whatsapp_client.send_text_message(conv.phone_number, fallback_text)

        return {"status": "category_menu_sent"}

    @classmethod
    def _state_category_selection(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Handles category selection from the 4 customer options."""
        matched_category = None

        if input_id:
            for cat in OFFICIAL_CATEGORIES:
                if cat["id"] == input_id:
                    matched_category = cat["name"]
                    break

        if not matched_category:
            norm = user_text.strip().lower()
            if "1" in norm or "airport" in norm or "meet" in norm:
                matched_category = "Airport Services"
            elif "2" in norm or "travel" in norm or "visa" in norm or "insurance" in norm:
                matched_category = "Travel Services"
            elif "3" in norm or "charter" in norm or "jet" in norm:
                matched_category = "Private Charter"
            elif "4" in norm or "hotel" in norm or "transport" in norm or "ground" in norm:
                matched_category = "Hotel & Transportation"

        if not matched_category:
            whatsapp_client.send_text_message(
                conv.phone_number,
                "Please select a valid option (1-4):\n1️⃣ Airport Services\n2️⃣ Travel Services\n3️⃣ Private Charter\n4️⃣ Hotel & Transportation"
            )
            return {"status": "invalid_category"}

        conv.selected_category = matched_category
        db.commit()

        # Branching based on selected category
        if matched_category == "Airport Services":
            conv.requires_airport = True
            conv.requires_flight = True
            cls._transition_state(db, conv, "JOURNEY_TYPE_SELECTION")
            return cls._prompt_journey_type(conv)

        elif matched_category == "Travel Services":
            conv.requires_airport = False
            conv.requires_flight = False
            cls._transition_state(db, conv, "SERVICE_SELECTION")
            return cls._send_service_menu(db, conv, "Travel Services")

        elif matched_category == "Private Charter":
            conv.requires_airport = False
            conv.requires_flight = False
            cls._transition_state(db, conv, "SERVICE_SELECTION")
            return cls._send_service_menu(db, conv, "Private Charter")

        elif matched_category == "Hotel & Transportation":
            cls._transition_state(db, conv, "HOTEL_TRANSPORT_SUBMENU")
            return cls._prompt_hotel_transport_submenu(conv)

        return {"status": "category_selected"}

    # ── 1. AIRPORT SERVICES FLOW (JOURNEY TYPE -> TRAVEL TYPE -> AIRPORT -> MATCHING SERVICES) ──

    @classmethod
    def _prompt_journey_type(cls, conv: WhatsAppConversation) -> Dict[str, Any]:
        """Prompts customer for Arrival / Departure / Transit."""
        body_text = (
            "✈️ *Airport Services*\n\n"
            "Please select your journey type:\n\n"
            "1. Arrival\n"
            "2. Departure\n"
            "3. Transit"
        )
        buttons = [
            {"id": "btn_jt_arrival", "title": "Arrival"},
            {"id": "btn_jt_departure", "title": "Departure"},
            {"id": "btn_jt_transit", "title": "Transit"}
        ]
        res = whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Journey Type"
        )
        if not res.get("success"):
            fallback = (
                "✈️ *Airport Services*\n\n"
                "Please select your journey type:\n"
                "1. Arrival\n"
                "2. Departure\n"
                "3. Transit\n\n"
                "Reply *1* (Arrival), *2* (Departure), or *3* (Transit)."
            )
            whatsapp_client.send_text_message(conv.phone_number, fallback)

        return {"status": "journey_type_prompt_sent"}

    @classmethod
    def _state_journey_type_selection(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Processes journey type selection and branches to Travel Type or Transit Type."""
        norm = (input_id or user_text).strip().upper()
        jt = None
        if norm in ["1", "ARRIVAL", "BTN_JT_ARRIVAL"] or "ARRIVAL" in norm:
            jt = "ARRIVAL"
        elif norm in ["2", "DEPARTURE", "BTN_JT_DEPARTURE"] or "DEPARTURE" in norm:
            jt = "DEPARTURE"
        elif norm in ["3", "TRANSIT", "BTN_JT_TRANSIT"] or "TRANSIT" in norm:
            jt = "TRANSIT"

        if not jt:
            whatsapp_client.send_text_message(
                conv.phone_number,
                "Please select a valid journey type:\n1. Arrival\n2. Departure\n3. Transit"
            )
            return {"status": "invalid_journey_type"}

        # Store journey type in flight_details_json metadata
        conv.flight_details_json = {"journey_type": jt}
        db.commit()

        if jt == "TRANSIT":
            cls._transition_state(db, conv, "AIRPORT_TRANSIT_TYPE")
            return cls._prompt_transit_type(conv)
        else:
            cls._transition_state(db, conv, "AIRPORT_TRAVEL_TYPE")
            return cls._prompt_travel_type(conv, jt.title())

    @classmethod
    def _prompt_travel_type(cls, conv: WhatsAppConversation, journey_name: str) -> Dict[str, Any]:
        """Prompts customer for Domestic or International travel type for Arrival/Departure."""
        body_text = (
            f"*{journey_name}* selected.\n\n"
            "Please select your travel type:\n\n"
            "1. Domestic\n"
            "2. International"
        )
        buttons = [
            {"id": "btn_travel_domestic", "title": "Domestic"},
            {"id": "btn_travel_international", "title": "International"}
        ]
        res = whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text=f"{journey_name} Travel Type"
        )
        if not res.get("success"):
            fallback = (
                f"*{journey_name}* selected.\n\n"
                "Please select your travel type:\n"
                "1. Domestic\n"
                "2. International\n\n"
                "Reply *1* for Domestic or *2* for International."
            )
            whatsapp_client.send_text_message(conv.phone_number, fallback)

        return {"status": "travel_type_prompt_sent"}

    @classmethod
    def _state_travel_type_selection(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Processes Domestic vs International travel type selection."""
        norm = (input_id or user_text).strip().upper()
        tt = None
        if norm in ["1", "DOMESTIC", "BTN_TRAVEL_DOMESTIC"] or "DOMESTIC" in norm:
            tt = "DOMESTIC"
        elif norm in ["2", "INTERNATIONAL", "INTL", "BTN_TRAVEL_INTERNATIONAL"] or "INTERNATIONAL" in norm or "INTL" in norm:
            tt = "INTERNATIONAL"

        if not tt:
            whatsapp_client.send_text_message(
                conv.phone_number,
                "Please select a valid travel type:\n1. Domestic\n2. International"
            )
            return {"status": "invalid_travel_type"}

        jt = (conv.flight_details_json or {}).get("journey_type", "DEPARTURE") if isinstance(conv.flight_details_json, dict) else "DEPARTURE"
        conv.flight_details_json = {
            "journey_type": jt,
            "travel_type": tt,
            "flight_type": tt
        }
        db.commit()

        cls._transition_state(db, conv, "AIRPORT_SELECTION")

        msg = (
            f"*{tt.title()}* selected.\n\n"
            "Please enter your Airport Name, City, or IATA Code (e.g., *Delhi*, *DEL*, *Lucknow*):"
        )
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "airport_prompt_sent"}

    @classmethod
    def _prompt_transit_type(cls, conv: WhatsAppConversation) -> Dict[str, Any]:
        """Prompts customer for Transit combinations."""
        body_text = (
            "🔄 *Airport Transit Services*\n\n"
            "Please select your transit type:\n\n"
            "1. Domestic → Domestic\n"
            "2. Domestic → International\n"
            "3. International → Domestic\n"
            "4. International → International"
        )
        rows = [
            {"id": "btn_transit_dom_dom", "title": "Domestic → Domestic", "description": "Domestic flight to domestic flight"},
            {"id": "btn_transit_dom_intl", "title": "Domestic → Intl", "description": "Domestic flight connecting to international"},
            {"id": "btn_transit_intl_dom", "title": "Intl → Domestic", "description": "International flight connecting to domestic"},
            {"id": "btn_transit_intl_intl", "title": "Intl → Intl", "description": "International to international transfer"}
        ]
        sections = [{"title": "Transit Options", "rows": rows}]

        res = whatsapp_client.send_interactive_list(
            to_phone=conv.phone_number,
            body_text=body_text,
            button_title="Select Transit",
            sections=sections,
            header_text="Transit Options"
        )
        if not res.get("success"):
            fallback = (
                "🔄 *Airport Transit Services*\n\n"
                "Please select your transit type:\n"
                "1. Domestic → Domestic\n"
                "2. Domestic → International\n"
                "3. International → Domestic\n"
                "4. International → International\n\n"
                "Reply with *1*, *2*, *3*, or *4*."
            )
            whatsapp_client.send_text_message(conv.phone_number, fallback)

        return {"status": "transit_type_prompt_sent"}

    @classmethod
    def _state_transit_type_selection(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Processes Transit Type selection."""
        norm = (input_id or user_text).strip().upper()
        tt = None
        if norm in ["1", "BTN_TRANSIT_DOM_DOM", "DOMESTIC_DOMESTIC", "DOMESTIC TO DOMESTIC", "DOMESTIC -> DOMESTIC", "DOMESTIC - DOMESTIC", "DOM_DOM"] or ("DOMESTIC" in norm and "INT" not in norm):
            tt = "DOMESTIC_DOMESTIC"
        elif norm in ["2", "BTN_TRANSIT_DOM_INTL", "DOMESTIC_INTERNATIONAL", "DOMESTIC TO INTERNATIONAL", "DOMESTIC -> INTL", "DOMESTIC -> INTERNATIONAL", "DOM_INTL"]:
            tt = "DOMESTIC_INTERNATIONAL"
        elif norm in ["3", "BTN_TRANSIT_INTL_DOM", "INTERNATIONAL_DOMESTIC", "INTERNATIONAL TO DOMESTIC", "INTL -> DOMESTIC", "INTL_DOM"]:
            tt = "INTERNATIONAL_DOMESTIC"
        elif norm in ["4", "BTN_TRANSIT_INTL_INTL", "INTERNATIONAL_INTERNATIONAL", "INTERNATIONAL TO INTERNATIONAL", "INTL -> INTL", "INTL_INTL"]:
            tt = "INTERNATIONAL_INTERNATIONAL"

        if not tt:
            whatsapp_client.send_text_message(
                conv.phone_number,
                "Please select a valid transit type (1-4):\n1. Domestic → Domestic\n2. Domestic → International\n3. International → Domestic\n4. International → International"
            )
            return {"status": "invalid_transit_type"}

        display_label = tt.replace("_", " → ").title()
        conv.flight_details_json = {
            "journey_type": "TRANSIT",
            "travel_type": tt,
            "transit_type": tt,
            "flight_type": tt
        }
        db.commit()

        cls._transition_state(db, conv, "AIRPORT_SELECTION")

        msg = (
            f"Transit Type: *{display_label}*\n\n"
            "Please enter your Airport Name, City, or IATA Code (e.g., *Delhi*, *DEL*, *Mumbai*):"
        )
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "airport_prompt_sent"}

    @classmethod
    def _state_airport_selection(cls, db: Session, conv: WhatsAppConversation, query: str) -> Dict[str, Any]:
        """
        Database-driven airport resolution:
        Searches ONLY Shafsky-supported airports configured in the database (`supported_airports`).
        Rejects non-configured or worldwide airports.
        """
        query_clean = query.strip()
        if not query_clean:
            whatsapp_client.send_text_message(conv.phone_number, "Please enter an Airport Name, City, or IATA Code (e.g. Delhi, DEL, Lucknow).")
            return {"status": "empty_airport_query"}

        # Search exclusively in database SupportedAirport table where is_supported=True and is_active=True
        try:
            search_stmt = select(SupportedAirport).where(
                SupportedAirport.is_active == True,
                SupportedAirport.is_supported == True,
                or_(
                    SupportedAirport.iata_code.ilike(query_clean),
                    SupportedAirport.city.ilike(f"%{query_clean}%"),
                    SupportedAirport.airport_name.ilike(f"%{query_clean}%")
                )
            )
            airport = db.execute(search_stmt).scalars().first()
        except Exception as err:
            logger.error(f"[WhatsApp Airport Resolution] DB query error: {err}")
            airport = None

        if not airport:
            msg = "Sorry, Airport Services are currently unavailable at this airport."
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "unsupported_airport"}

        conv.selected_airport_name = airport.airport_name
        conv.selected_airport_iata = airport.iata_code
        conv.selected_airport_city = airport.city
        conv.selected_airport_country = airport.country
        cls._transition_state(db, conv, "SERVICE_SELECTION")

        return cls._send_airport_services_menu(db, conv)

    @classmethod
    def _state_airport_confirmation(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Handles airport confirmation."""
        text_u = (input_id or user_text).strip().upper()

        if "CHANGE" in text_u or "NO" in text_u or text_u == "btn_change_airport":
            conv.selected_airport_iata = None
            conv.selected_airport_name = None
            conv.selected_airport_city = None
            conv.selected_airport_country = None
            cls._transition_state(db, conv, "AIRPORT_SELECTION")
            msg = "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL, Lucknow):"
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "reprompt_airport"}

        if "CONFIRM" in text_u or "YES" in text_u or text_u == "1" or text_u == "btn_confirm_airport":
            cls._transition_state(db, conv, "SERVICE_SELECTION")
            return cls._send_airport_services_menu(db, conv)

        whatsapp_client.send_text_message(conv.phone_number, "Please select *Confirm* or *Change Airport*.")
        return {"status": "invalid_airport_confirmation"}

    @classmethod
    def _send_airport_services_menu(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        """
        Queries and presents ONLY services/packages configured for the selected:
        airport + journey_type + travel_type / transit_type
        from the database source of truth.
        """
        iata = conv.selected_airport_iata
        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        jt = metadata.get("journey_type", "DEPARTURE").upper()
        tt = metadata.get("travel_type", "DOMESTIC").upper()

        # Query database SupportedAirport
        airport = db.execute(select(SupportedAirport).where(SupportedAirport.iata_code == iata)).scalar_one_or_none()
        if not airport:
            whatsapp_client.send_text_message(conv.phone_number, "Sorry, Airport Services are currently unavailable at this airport.")
            return {"status": "unsupported_airport"}

        # Determine matching flight_types in AirportService
        if jt == "TRANSIT":
            if tt in ["DOMESTIC_DOMESTIC", "DOMESTIC"]:
                flight_types = ["DOMESTIC_DOMESTIC", "DOMESTIC", "ALL"]
            elif tt == "DOMESTIC_INTERNATIONAL":
                flight_types = ["DOMESTIC_INTERNATIONAL", "ALL"]
            elif tt == "INTERNATIONAL_DOMESTIC":
                flight_types = ["INTERNATIONAL_DOMESTIC", "ALL"]
            elif tt in ["INTERNATIONAL_INTERNATIONAL", "INTERNATIONAL"]:
                flight_types = ["INTERNATIONAL_INTERNATIONAL", "INTERNATIONAL", "ALL"]
            else:
                flight_types = [tt, "ALL"]
        else:
            if tt == "DOMESTIC":
                flight_types = ["DOMESTIC", "ALL"]
            elif tt == "INTERNATIONAL":
                flight_types = ["INTERNATIONAL", "ALL"]
            else:
                flight_types = [tt, "ALL"]

        stmt = (
            select(AirportService, Service)
            .join(Service, AirportService.service_id == Service.id)
            .where(
                AirportService.airport_id == airport.id,
                AirportService.journey_type == jt,
                AirportService.flight_type.in_(flight_types),
                AirportService.is_available.is_(True),
                Service.is_active.is_(True)
            )
            .order_by(AirportService.display_priority, Service.display_order)
        )
        rows = db.execute(stmt).all()

        if not rows:
            tt_label = "Domestic" if tt == "DOMESTIC" else ("International" if tt == "INTERNATIONAL" else tt.replace("_", " → ").title())
            jt_label = jt.title()
            empty_msg = (
                f"Sorry, there are currently no *{tt_label} {jt_label}* services available at *{airport.airport_name}*.\n\n"
                "Please choose an option to continue:"
            )
            buttons = [
                {"id": "btn_change_travel_type", "title": "Change Travel Type"},
                {"id": "btn_change_airport", "title": "Change Airport"},
                {"id": "btn_main_menu", "title": "Main Menu"}
            ]
            res = whatsapp_client.send_interactive_buttons(
                to_phone=conv.phone_number,
                body_text=empty_msg,
                buttons=buttons,
                header_text="No Services Found"
            )
            if not res.get("success"):
                fallback = (
                    f"{empty_msg}\n\n"
                    "1. Change Travel Type\n"
                    "2. Change Airport\n"
                    "3. Main Menu\n\n"
                    "Reply *1*, *2*, or *3*."
                )
                whatsapp_client.send_text_message(conv.phone_number, fallback)
            return {"status": "no_services_found"}

        tt_display = "Domestic" if tt == "DOMESTIC" else ("International" if tt == "INTERNATIONAL" else tt.replace("_", " → ").title())

        body_text = (
            f"✨ *Available Services at {airport.airport_name}*\n\n"
            f"• *Journey Type*: {jt.title()}\n"
            f"• *Travel Type*: {tt_display}\n\n"
            "Please select a service package:"
        )

        available_services = []
        for aps, svc in rows:
            available_services.append({
                "id": str(svc.id),
                "title": svc.name,
                "price": float(aps.price),
                "description": aps.short_description or svc.description or "VIP Airport Service"
            })

        list_rows = []
        for svc in available_services[:10]:
            list_rows.append({
                "id": f"svc_id_{svc['id']}",
                "title": svc["title"][:24],
                "description": f"₹{int(svc['price']):,} - {svc['description'][:40]}"
            })

        sections = [{"title": "Select Package", "rows": list_rows}]

        res = whatsapp_client.send_interactive_list(
            to_phone=conv.phone_number,
            body_text=body_text,
            button_title="View Packages",
            sections=sections,
            header_text="Service Packages"
        )
        if not res.get("success"):
            fallback = body_text + "\n\n"
            for i, svc in enumerate(available_services, 1):
                fallback += f"{i}. *{svc['title']}* — ₹{int(svc['price']):,}\n"
            fallback += "\nPlease reply with the number of your choice (e.g. 1)."
            whatsapp_client.send_text_message(conv.phone_number, fallback)

        return {"status": "services_menu_sent"}

    # ── 2. HOTEL & TRANSPORTATION / CHARTER / TRAVEL FLOWS ──

    @classmethod
    def _prompt_hotel_transport_submenu(cls, conv: WhatsAppConversation) -> Dict[str, Any]:
        """Submenu for Hotel & Transportation."""
        body_text = (
            "🏨🚗 *Hotel & Transportation Services*\n\n"
            "Please choose a service:"
        )
        buttons = [
            {"id": "btn_sub_hotel", "title": "Hotel Booking"},
            {"id": "btn_sub_transport", "title": "Transportation"}
        ]
        res = whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Hotel & Transport"
        )
        if not res.get("success"):
            whatsapp_client.send_text_message(conv.phone_number, body_text + "\n\nReply *1* for Hotel Booking or *2* for Transportation.")
        return {"status": "hotel_transport_submenu_sent"}

    @classmethod
    def _state_hotel_transport_submenu(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Handles submenu choice between Hotel and Transportation."""
        norm = (input_id or user_text).strip().upper()
        if "HOTEL" in norm or norm == "1" or norm == "btn_sub_hotel":
            conv.selected_service_name = "Luxury Hotel Booking"
            conv.requires_airport = False
            conv.requires_flight = False
            cls._transition_state(db, conv, "HOTEL_CITY")
            whatsapp_client.send_text_message(conv.phone_number, "🏨 *Hotel Booking*\n\nPlease enter your destination city or preferred hotel:")
            return {"status": "hotel_city_prompt_sent"}

        elif "TRANSPORT" in norm or norm == "2" or norm == "btn_sub_transport":
            conv.selected_service_name = "Premium Ground Transport"
            conv.requires_airport = False
            conv.requires_flight = False
            cls._transition_state(db, conv, "TRANSPORT_PICKUP")
            whatsapp_client.send_text_message(conv.phone_number, "🚗 *Transportation*\n\nPlease enter your pickup location:")
            return {"status": "transport_pickup_prompt_sent"}

        whatsapp_client.send_text_message(conv.phone_number, "Please select *1. Hotel Booking* or *2. Transportation*.")
        return {"status": "invalid_submenu_choice"}

    @classmethod
    def _state_hotel_city(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        city = user_text.strip()
        conv.selected_airport_city = city
        cls._transition_state(db, conv, "HOTEL_NIGHTS")
        whatsapp_client.send_text_message(conv.phone_number, f"City: *{city}*\n\nHow many nights will you be staying? (e.g. 2):")
        return {"status": "hotel_nights_prompt"}

    @classmethod
    def _state_hotel_nights(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        digits = "".join(filter(str.isdigit, user_text)) or "1"
        conv.additional_requirements = f"Hotel in {conv.selected_airport_city}, {digits} nights"
        cls._transition_state(db, conv, "DATE_SELECTION")
        whatsapp_client.send_text_message(conv.phone_number, "Please enter your Check-in Date in DD/MM/YYYY format (e.g., 25/08/2026):")
        return {"status": "hotel_date_prompt"}

    @classmethod
    def _state_transport_pickup(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        pickup = user_text.strip()
        conv.selected_airport_city = pickup
        cls._transition_state(db, conv, "TRANSPORT_DROPOFF")
        whatsapp_client.send_text_message(conv.phone_number, f"Pickup: *{pickup}*\n\nPlease enter your drop-off destination:")
        return {"status": "transport_dropoff_prompt"}

    @classmethod
    def _state_transport_dropoff(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        dropoff = user_text.strip()
        conv.additional_requirements = f"Route: {conv.selected_airport_city} to {dropoff}"
        cls._transition_state(db, conv, "DATE_SELECTION")
        whatsapp_client.send_text_message(conv.phone_number, "Please enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):")
        return {"status": "transport_date_prompt"}

    @classmethod
    def _state_charter_origin(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        origin = user_text.strip()
        conv.selected_airport_city = origin
        cls._transition_state(db, conv, "CHARTER_DESTINATION")
        whatsapp_client.send_text_message(conv.phone_number, f"Departure: *{origin}*\n\nPlease enter your destination city / airport:")
        return {"status": "charter_destination_prompt"}

    @classmethod
    def _state_charter_destination(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        destination = user_text.strip()
        conv.additional_requirements = f"Private Charter: {conv.selected_airport_city} to {destination}"
        cls._transition_state(db, conv, "DATE_SELECTION")
        whatsapp_client.send_text_message(conv.phone_number, "Please enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):")
        return {"status": "charter_date_prompt"}

    @classmethod
    def _send_service_menu(cls, db: Session, conv: WhatsAppConversation, category_name: str) -> Dict[str, Any]:
        """Displays services for non-airport categories (Travel, Charter)."""
        category_obj = next((c for c in OFFICIAL_CATEGORIES if c["name"] == category_name), None)
        valid_db_cats = category_obj["db_categories"] if category_obj else [category_name]

        try:
            all_services = ServiceConfigService.get_admin_catalog(db)
        except Exception:
            all_services = DEFAULT_SERVICE_CATALOG
        cat_services = [s for s in all_services if s.get("category") in valid_db_cats or s.get("category") == category_name]
        if not cat_services:
            cat_services = [s for s in DEFAULT_SERVICE_CATALOG if s.get("category") in valid_db_cats or s.get("category") == category_name]

        if not cat_services:
            cat_services = [
                {"id": f"{category_name.lower()}_std", "title": f"Standard {category_name}", "price": 5000.0, "description": f"Full assistance with {category_name}"}
            ]

        body_text = f"✨ *{category_name}*\n\nPlease select an option:"
        rows = []
        for svc in cat_services[:10]:
            title = svc.get("title", svc.get("name", "Service"))
            price = float(svc.get("base_price", svc.get("price", 5000)))
            desc = svc.get("description", "")
            rows.append({
                "id": f"svc_id_{svc.get('id')}",
                "title": str(title)[:24],
                "description": f"₹{int(price):,} - {str(desc)[:40]}"
            })

        sections = [{"title": category_name, "rows": rows}]

        res = whatsapp_client.send_interactive_list(
            to_phone=conv.phone_number,
            body_text=body_text,
            button_title="Select Option",
            sections=sections,
            header_text=category_name
        )
        if not res.get("success"):
            fallback = body_text + "\n\n"
            for i, svc in enumerate(cat_services, 1):
                t = svc.get("title", svc.get("name", "Service"))
                p = float(svc.get("base_price", svc.get("price", 5000)))
                fallback += f"{i}. *{t}* — ₹{int(p):,}\n"
            fallback += "\nPlease reply with the number of your choice (e.g. 1)."
            whatsapp_client.send_text_message(conv.phone_number, fallback)

        return {"status": "service_menu_sent"}

    @classmethod
    def _state_service_selection(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Handles service package selection from list reply or text reply."""
        category_name = conv.selected_category or "Airport Services"
        text_u = (input_id or user_text).strip().upper()

        # Section 13 & 14: Handle quick actions / navigation switches
        if text_u in ["BTN_CHANGE_TRAVEL_TYPE", "CHANGE TRAVEL TYPE", "TRAVEL TYPE"]:
            metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
            jt = metadata.get("journey_type", "DEPARTURE").upper()
            if jt == "TRANSIT":
                cls._transition_state(db, conv, "AIRPORT_TRANSIT_TYPE")
                return cls._prompt_transit_type(conv)
            else:
                cls._transition_state(db, conv, "AIRPORT_TRAVEL_TYPE")
                return cls._prompt_travel_type(conv, jt.title())

        if text_u in ["BTN_CHANGE_AIRPORT", "CHANGE AIRPORT"]:
            cls._transition_state(db, conv, "AIRPORT_SELECTION")
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL, Lucknow):")
            return {"status": "airport_prompt_sent"}

        selected_svc = None

        if category_name == "Airport Services":
            metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
            jt = metadata.get("journey_type", "DEPARTURE").upper()
            tt = metadata.get("travel_type", "DOMESTIC").upper()

            # Handle user switching travel type dynamically during service selection (Section 14)
            if text_u in ["DOMESTIC", "INTERNATIONAL", "INTL"]:
                new_tt = "DOMESTIC" if "DOMESTIC" in text_u else "INTERNATIONAL"
                new_meta = dict(metadata)
                new_meta["travel_type"] = new_tt
                new_meta["flight_type"] = new_tt
                conv.flight_details_json = new_meta
                db.commit()
                return cls._send_airport_services_menu(db, conv)

            if jt == "TRANSIT":
                if tt in ["DOMESTIC_DOMESTIC", "DOMESTIC"]:
                    flight_types = ["DOMESTIC_DOMESTIC", "DOMESTIC", "ALL"]
                elif tt == "DOMESTIC_INTERNATIONAL":
                    flight_types = ["DOMESTIC_INTERNATIONAL", "ALL"]
                elif tt == "INTERNATIONAL_DOMESTIC":
                    flight_types = ["INTERNATIONAL_DOMESTIC", "ALL"]
                elif tt in ["INTERNATIONAL_INTERNATIONAL", "INTERNATIONAL"]:
                    flight_types = ["INTERNATIONAL_INTERNATIONAL", "INTERNATIONAL", "ALL"]
                else:
                    flight_types = [tt, "ALL"]
            else:
                if tt == "DOMESTIC":
                    flight_types = ["DOMESTIC", "ALL"]
                elif tt == "INTERNATIONAL":
                    flight_types = ["INTERNATIONAL", "ALL"]
                else:
                    flight_types = [tt, "ALL"]

            iata = conv.selected_airport_iata
            airport = db.execute(select(SupportedAirport).where(SupportedAirport.iata_code == iata)).scalar_one_or_none()

            available_services = []
            if airport:
                stmt = (
                    select(AirportService, Service)
                    .join(Service, AirportService.service_id == Service.id)
                    .where(
                        AirportService.airport_id == airport.id,
                        AirportService.journey_type == jt,
                        AirportService.flight_type.in_(flight_types),
                        AirportService.is_available.is_(True),
                        Service.is_active.is_(True)
                    )
                    .order_by(AirportService.display_priority, Service.display_order)
                )
                for aps, svc in db.execute(stmt).all():
                    available_services.append({
                        "id": str(svc.id),
                        "title": svc.name,
                        "price": float(aps.price),
                        "description": aps.short_description or svc.description or "VIP Service"
                    })

            if not available_services:
                return cls._send_airport_services_menu(db, conv)

            if input_id:
                raw_id = input_id.replace("svc_id_", "")
                selected_svc = next((s for s in available_services if str(s["id"]) == raw_id), None)
            if not selected_svc:
                clean = user_text.strip().lower()
                if clean.isdigit():
                    idx = int(clean) - 1
                    if 0 <= idx < len(available_services):
                        selected_svc = available_services[idx]
                else:
                    for s in available_services:
                        if s["title"].lower() in clean or clean in s["title"].lower():
                            selected_svc = s
                            break

        else:
            category_obj = next((c for c in OFFICIAL_CATEGORIES if c["name"] == category_name), None)
            valid_db_cats = category_obj["db_categories"] if category_obj else [category_name]
            try:
                all_services = ServiceConfigService.get_admin_catalog(db)
            except Exception:
                all_services = DEFAULT_SERVICE_CATALOG
            cat_services = [s for s in all_services if s.get("category") in valid_db_cats or s.get("category") == category_name]

            if input_id:
                raw_id = input_id.replace("svc_id_", "")
                selected_svc = next((s for s in cat_services if str(s.get("id")) == raw_id), None)
            if not selected_svc:
                clean = user_text.strip().lower()
                if clean.isdigit():
                    idx = int(clean) - 1
                    if 0 <= idx < len(cat_services):
                        selected_svc = cat_services[idx]
                else:
                    for s in cat_services:
                        title = str(s.get("title", s.get("name", ""))).lower()
                        if title in clean or clean in title:
                            selected_svc = s
                            break

        if not selected_svc:
            whatsapp_client.send_text_message(conv.phone_number, "Service not found. Please select a valid service from the list.")
            return {"status": "invalid_service"}

        svc_id = str(selected_svc.get("id"))
        svc_title = str(selected_svc.get("title", selected_svc.get("name", "VIP Service")))
        price = float(selected_svc.get("base_price", selected_svc.get("price", 2500)))

        conv.selected_service_id = svc_id
        conv.selected_service_name = svc_title
        conv.total_amount = price
        db.commit()

        # Progression based on category
        if category_name == "Airport Services":
            cls._transition_state(db, conv, "FLIGHT_INPUT")
            msg = f"Selected Service: *{svc_title}*\n\nPlease enter your Flight Number (e.g., *EK501*, *AI2424*, *6E224*):"
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "flight_prompt_sent"}

        elif category_name == "Private Charter":
            cls._transition_state(db, conv, "CHARTER_ORIGIN")
            whatsapp_client.send_text_message(conv.phone_number, f"Selected Charter: *{svc_title}*\n\nPlease enter your departure city / airport:")
            return {"status": "charter_origin_prompt"}

        else:
            cls._transition_state(db, conv, "DATE_SELECTION")
            whatsapp_client.send_text_message(conv.phone_number, f"Selected Service: *{svc_title}*\n\nPlease enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):")
            return {"status": "date_prompt_sent"}

    # ── 3. FLIGHT LOCAL VALIDATION & PROGRESSION ──

    @classmethod
    def _validate_flight_number_local(cls, flight_num_input: str) -> Optional[Dict[str, str]]:
        """
        Fast, pure-local validation of flight numbers without calling external flight APIs.
        1. Normalizes whitespace and uppercase.
        2. Extracts airline IATA/ICAO code (2-3 alphanumeric chars with letters) and numeric flight number (1-4 digits).
        3. Validates the basic flight-number format and rejects invalid strings.
        """
        if not flight_num_input or not isinstance(flight_num_input, str):
            return None

        clean = re.sub(r"\s+", "", str(flight_num_input).strip().upper())
        if not clean or len(clean) < 3 or len(clean) > 8:
            return None

        match = re.match(r"^([A-Z0-9]{2,3})(\d{1,4}[A-Z]?)$", clean)
        if not match:
            return None

        airline_code = match.group(1)
        flight_digits = match.group(2)

        if not re.search(r"[A-Z]", airline_code) or not re.search(r"\d", flight_digits):
            return None

        normalized_flight = f"{airline_code}{flight_digits}"
        return {
            "flight_number": normalized_flight,
            "airline_code": airline_code,
            "flight_digits": flight_digits,
            "verification_status": "not_verified",
            "status": "flight_number_received",
        }

    @classmethod
    def _state_flight_input(cls, db: Session, conv: WhatsAppConversation, flight_num_input: str) -> Dict[str, Any]:
        """Processes flight number input locally without external APIs."""
        val_res = cls._validate_flight_number_local(flight_num_input)
        if not val_res:
            whatsapp_client.send_text_message(
                conv.phone_number,
                "Please enter a valid flight number (e.g., *EK501*, *AI2424*, *6E224*, *UK955*)."
            )
            return {"status": "invalid_flight_format"}

        norm_flight = val_res["flight_number"]
        conv.flight_num = norm_flight

        jt = (conv.flight_details_json or {}).get("journey_type", "DEPARTURE") if isinstance(conv.flight_details_json, dict) else "DEPARTURE"
        tt = (conv.flight_details_json or {}).get("travel_type", "DOMESTIC") if isinstance(conv.flight_details_json, dict) else "DOMESTIC"
        conv.flight_details_json = {
            "flight_number": norm_flight,
            "airline_code": val_res["airline_code"],
            "journey_type": jt,
            "travel_type": tt,
            "flight_type": tt,
            "verification_status": "not_verified",
            "status": "flight_number_received"
        }
        cls._transition_state(db, conv, "FLIGHT_CONFIRMATION")

        body_text = (
            f"✈️ *Flight Number Received: {norm_flight}*\n\n"
            f"• *Flight Number*: {norm_flight}\n"
        )
        if conv.selected_airport_iata:
            body_text += f"• *Airport*: {conv.selected_airport_name} ({conv.selected_airport_iata})\n"

        body_text += "\nPlease confirm your flight details:"

        buttons = [
            {"id": "btn_confirm_flight", "title": "Confirm Flight"},
            {"id": "btn_reenter_flight", "title": "Re-enter Flight"}
        ]

        res = whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Flight Details"
        )
        if not res.get("success"):
            fallback_text = (
                f"✈️ *Flight Number Received: {norm_flight}*\n\n"
                "Reply *Confirm* to proceed, or *Re-enter* to change flight number."
            )
            whatsapp_client.send_text_message(conv.phone_number, fallback_text)

        return {"status": "flight_number_received"}

    @classmethod
    def _state_flight_confirmation(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Handles flight confirmation."""
        text_u = (input_id or user_text).strip().upper()

        if "RE-ENTER" in text_u or "REENTER" in text_u or "CHANGE" in text_u or "NO" in text_u or text_u == "btn_reenter_flight":
            conv.flight_num = None
            cls._transition_state(db, conv, "FLIGHT_INPUT")
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your flight number (e.g., *EK501*, *AI2424*, *6E224*):")
            return {"status": "reprompt_flight"}

        if "CONFIRM" in text_u or "YES" in text_u or text_u == "1" or text_u == "btn_confirm_flight":
            cls._transition_state(db, conv, "DATE_SELECTION")
            msg = f"Flight Number Received: *{conv.flight_num}*\n\nPlease enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):"
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "date_prompt_sent"}

        whatsapp_client.send_text_message(conv.phone_number, "Please select *Confirm Flight* or *Re-enter Flight*.")
        return {"status": "invalid_flight_confirmation"}

    # ── 4. DYNAMIC DETAILS COLLECTION (DATE, PASSENGERS, CUSTOMER DETAILS) ──

    @classmethod
    def _get_service_timezone(cls, tz_name: Optional[str] = None) -> timezone:
        """
        Returns timezone for airport/service. Defaults to IST (Asia/Kolkata, UTC+5:30).
        Safely handles standard named timezones without crashing on Windows if tzdata is absent.
        """
        if not tz_name or tz_name in ("Asia/Kolkata", "Asia/Calcutta", "IST"):
            return timezone(timedelta(hours=5, minutes=30))
        if tz_name.upper() in ("UTC", "GMT"):
            return timezone.utc
        if tz_name.upper() in ("GST", "Asia/Dubai"):
            return timezone(timedelta(hours=4))

        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tz_name)
        except Exception:
            return timezone(timedelta(hours=5, minutes=30))

    @classmethod
    def _validate_whatsapp_date(
        cls,
        db: Session,
        conv: WhatsAppConversation,
        date_input: str
    ) -> Tuple[bool, Optional[date], Optional[str], str]:
        """
        Strict, pure-local validation of WhatsApp booking date.
        Enforces DD/MM/YYYY as primary format, parses to real date object,
        and evaluates strictly against backend timezone/current date.
        Returns: (is_valid, parsed_date, error_message, status_code)
        """
        clean_date = (date_input or "").strip()
        if not clean_date:
            error_msg = (
                "Please enter the date in DD/MM/YYYY format.\n"
                "Example: 25/08/2026"
            )
            return False, None, error_msg, "invalid_date_format"

        # 1. Parse date input into real date object
        parsed_dt = None
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
            try:
                dt = datetime.strptime(clean_date, fmt)
                if 2020 <= dt.year <= 2100:
                    parsed_dt = dt
                    break
            except (ValueError, TypeError):
                continue

        if not parsed_dt:
            error_msg = (
                "Please enter the date in DD/MM/YYYY format.\n"
                "Example: 25/08/2026"
            )
            return False, None, error_msg, "invalid_date_format"

        parsed_date = parsed_dt.date()

        # 2. Get current date dynamically in appropriate airport/service timezone
        airport_tz_name = None
        if conv.selected_airport_iata:
            airport = db.scalar(
                select(SupportedAirport).where(
                    SupportedAirport.iata_code == conv.selected_airport_iata.upper()
                )
            )
            if airport and airport.timezone:
                airport_tz_name = airport.timezone

        service_tz = cls._get_service_timezone(airport_tz_name)
        now_in_tz = datetime.now(service_tz)
        today_in_tz = now_in_tz.date()

        # 3. If selected date is before today -> Reject as already passed
        if parsed_date < today_in_tz:
            error_msg = (
                "❌ This date has already passed.\n"
                "Please enter a valid future date.\n\n"
                "Example: 25/08/2026"
            )
            return False, None, error_msg, "past_date_rejected"

        # 4. If selected date is today -> Validate against minimum booking window / cutoff rule
        if parsed_date == today_in_tz:
            metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
            service_time_str = metadata.get("flight_time") or metadata.get("service_time")
            if service_time_str:
                try:
                    time_parts = [int(p) for p in str(service_time_str).split(":")[:2]]
                    scheduled_dt = datetime(
                        parsed_date.year, parsed_date.month, parsed_date.day,
                        time_parts[0], time_parts[1],
                        tzinfo=service_tz
                    )
                    # Minimum 4 hours lead time required for same-day scheduled services
                    if scheduled_dt <= now_in_tz or (scheduled_dt - now_in_tz).total_seconds() < 4 * 3600:
                        error_msg = (
                            "❌ This booking is too close to the scheduled time.\n"
                            "Please choose a later available time or another date."
                        )
                        return False, None, error_msg, "cutoff_violation"
                except Exception:
                    pass

        # 5. Valid date (tomorrow or later, or today with valid notice)
        return True, parsed_date, None, "valid_date"

    @classmethod
    def _state_date_selection(cls, db: Session, conv: WhatsAppConversation, date_input: str) -> Dict[str, Any]:
        """Strict date validation handler for WhatsApp booking flow."""
        is_valid, parsed_date, err_msg, status_code = cls._validate_whatsapp_date(db, conv, date_input)
        if not is_valid:
            whatsapp_client.send_text_message(conv.phone_number, err_msg)
            return {"status": status_code}

        date_formatted = parsed_date.strftime("%d %B %Y")
        conv.booking_date = date_formatted
        cls._transition_state(db, conv, "PASSENGER_COUNT")

        msg = f"Date Saved: *{date_formatted}*\n\nHow many passengers will be travelling? (Enter a number, e.g., 2):"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "passenger_count_prompt_sent"}

    @classmethod
    def _state_passenger_count(cls, db: Session, conv: WhatsAppConversation, count_input: str) -> Dict[str, Any]:
        digits = "".join(filter(str.isdigit, count_input))
        if not digits or int(digits) < 1 or int(digits) > 50:
            whatsapp_client.send_text_message(conv.phone_number, "Please enter a valid passenger count (1-50).")
            return {"status": "invalid_passenger_count"}

        count = int(digits)
        conv.passenger_count = count
        cls._transition_state(db, conv, "CUSTOMER_NAME")

        msg = f"Passengers: *{count}*\n\nMay I have your full name?"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "name_prompt_sent"}

    @classmethod
    def _state_customer_name(cls, db: Session, conv: WhatsAppConversation, name_input: str) -> Dict[str, Any]:
        clean_name = name_input.strip()
        if len(clean_name) < 2:
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your valid full name.")
            return {"status": "invalid_name"}

        conv.customer_name = clean_name
        cls._transition_state(db, conv, "CUSTOMER_EMAIL")

        msg = f"Name: *{clean_name}*\n\nPlease provide your email address for booking confirmation:"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "email_prompt_sent"}

    @classmethod
    def _state_customer_email(cls, db: Session, conv: WhatsAppConversation, email_input: str) -> Dict[str, Any]:
        clean_email = email_input.strip()
        if "@" not in clean_email or "." not in clean_email or len(clean_email) < 5:
            whatsapp_client.send_text_message(conv.phone_number, "Please enter a valid email address (e.g. name@example.com).")
            return {"status": "invalid_email"}

        conv.customer_email = clean_email
        cls._transition_state(db, conv, "CUSTOMER_PHONE")

        msg = f"Email Saved: *{clean_email}*\n\nPlease provide your contact phone number (or type 'Same' to use this WhatsApp number):"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "phone_prompt_sent"}

    @classmethod
    def _state_customer_phone(cls, db: Session, conv: WhatsAppConversation, phone_input: str) -> Dict[str, Any]:
        clean_p = phone_input.strip().lower()
        if clean_p in ["same", "same number", "this", "my number", "yes", "ok"]:
            conv.customer_phone = conv.phone_number
        else:
            digits = "".join(filter(str.isdigit, phone_input))
            if len(digits) < 7:
                whatsapp_client.send_text_message(conv.phone_number, "Please enter a valid contact phone number with country code (or type 'Same').")
                return {"status": "invalid_phone"}
            conv.customer_phone = digits

        cls._transition_state(db, conv, "ADDITIONAL_REQUIREMENTS")

        msg = "Do you have any special requirements or notes? (Type *None* if no special requests):"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "notes_prompt_sent"}

    @classmethod
    def _state_additional_requirements(cls, db: Session, conv: WhatsAppConversation, notes_input: str) -> Dict[str, Any]:
        clean_notes = notes_input.strip()
        conv.additional_requirements = "None" if clean_notes.lower() in ["none", "no", "n/a", "-"] else clean_notes
        cls._transition_state(db, conv, "BOOKING_REVIEW")

        return cls._send_booking_summary(db, conv)

    # ── 5. BOOKING SUMMARY & CREATION (NO PAYMENT GATEWAY) ──

    @classmethod
    def _send_booking_summary(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        """Displays booking summary before creation."""
        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        jt = metadata.get("journey_type")
        tt = metadata.get("travel_type")

        summary_lines = [
            "📋 *BOOKING SUMMARY — Shafsky Aviation*\n",
            f"• *Service*: {conv.selected_service_name or 'VIP Service'}",
        ]
        if conv.selected_airport_iata:
            summary_lines.append(f"• *Airport*: {conv.selected_airport_name} ({conv.selected_airport_iata})")
        if jt:
            summary_lines.append(f"• *Journey Type*: {jt.title()}")
        if tt:
            tt_display = "Domestic" if tt == "DOMESTIC" else ("International" if tt == "INTERNATIONAL" else tt.replace("_", " → ").title())
            summary_lines.append(f"• *Travel Type*: {tt_display}")
        if conv.flight_num:
            summary_lines.append(f"• *Flight*: {conv.flight_num}")

        summary_lines.extend([
            f"• *Date*: {conv.booking_date}",
            f"• *Passengers*: {conv.passenger_count}",
            f"• *Customer*: {conv.customer_name}",
            f"• *Email*: {conv.customer_email}",
            f"• *Phone*: {conv.customer_phone}",
            f"• *Special Requests*: {conv.additional_requirements}",
        ])

        if conv.total_amount and float(conv.total_amount) > 0:
            summary_lines.append(f"\n💰 *Estimated Amount*: ₹{int(conv.total_amount):,}\n")
        else:
            summary_lines.append("\n💰 *Pricing*: Custom Quote / Team Assistance\n")

        summary_lines.append("Please review your details to confirm your booking request:")

        body_text = "\n".join(summary_lines)
        buttons = [
            {"id": "btn_confirm_booking", "title": "Confirm Booking"},
            {"id": "btn_change_details", "title": "Change Details"},
            {"id": "btn_cancel", "title": "Cancel"}
        ]

        res = whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Booking Summary"
        )
        if not res.get("success"):
            fallback = body_text + "\n\nReply *Confirm* to submit your request, *Change* to edit, or *Cancel*."
            whatsapp_client.send_text_message(conv.phone_number, fallback)

        return {"status": "summary_sent"}

    @classmethod
    def _state_booking_review(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Handles booking confirmation or change details."""
        text_u = (input_id or user_text).strip().upper()

        if "CHANGE" in text_u or "EDIT" in text_u or text_u == "btn_change_details":
            cls._transition_state(db, conv, "CUSTOMER_NAME")
            whatsapp_client.send_text_message(conv.phone_number, "Let's update your details. May I have your full name?")
            return {"status": "edit_prompt"}

        if "CONFIRM" in text_u or "YES" in text_u or text_u == "1" or text_u == "btn_confirm_booking":
            return cls._create_booking_request(db, conv)

        whatsapp_client.send_text_message(conv.phone_number, "Please select *Confirm Booking*, *Change Details*, or *Cancel*.")
        return {"status": "invalid_summary_choice"}

    @classmethod
    def _create_booking_request(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        """
        Creates the database Booking record with PENDING status.
        NO PAYMENT GATEWAY is implemented or called.
        Informs customer that our team will contact them for payment & final confirmation.
        """
        amount = float(conv.total_amount or 0.0)
        booking_ref = BookingService.generate_booking_ref()

        try:
            # 1. Create DB Booking Record
            new_booking = Booking(
                id=uuid.uuid4(),
                booking_ref=booking_ref,
                passenger_name=conv.customer_name or "Guest",
                passenger_email=conv.customer_email or "guest@shafsky.com",
                passenger_phone=conv.customer_phone or conv.phone_number,
                service_category=conv.selected_category or "Airport Services",
                service_type=conv.selected_service_name or "VIP Service",
                origin_code=conv.selected_airport_iata or "DEL",
                dest_code="DEST",
                flight_num=conv.flight_num or "N/A",
                total_amount=amount,
                currency="INR",
                status=BookingStatus.PENDING,
                notes=conv.additional_requirements,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(new_booking)
            db.commit()
            db.refresh(new_booking)

            # 2. Update Conversation Record
            conv.booking_id = new_booking.id
            conv.booking_ref = booking_ref
            conv.payment_status = "PENDING"
            cls._transition_state(db, conv, "BOOKING_CONFIRMED")

        except Exception as db_err:
            db.rollback()
            logger.error(f"[WhatsApp Booking Creation] Database error: {db_err}")
            whatsapp_client.send_text_message(
                conv.phone_number,
                "An error occurred while creating your booking request. Please type *Hi* to try again."
            )
            return {"status": "booking_creation_failed", "error": str(db_err)}

        # 3. Customer WhatsApp Confirmation Message (No payment claim, team follow-up)
        cust_msg = (
            "🎉 *Booking Request Received!*\n\n"
            f"Your booking reference is *{booking_ref}*.\n\n"
            f"• *Service*: {conv.selected_service_name}\n"
        )
        if conv.selected_airport_name:
            cust_msg += f"• *Airport*: {conv.selected_airport_name} ({conv.selected_airport_iata})\n"
        if conv.flight_num:
            cust_msg += f"• *Flight*: {conv.flight_num}\n"

        cust_msg += (
            f"• *Date*: {conv.booking_date}\n"
            f"• *Passengers*: {conv.passenger_count}\n"
            f"• *Passenger Name*: {conv.customer_name}\n\n"
            "Our team will contact you regarding payment and final confirmation.\n\n"
            "Thank you for choosing Shafsky Aviation."
        )
        whatsapp_client.send_text_message(conv.phone_number, cust_msg)

        # 4. Team WhatsApp Notification
        officer_phone = os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "919599087959").strip()
        team_msg = (
            "🚨 *NEW BOOKING REQUEST RECEIVED*\n\n"
            f"• *Booking Ref*: {booking_ref}\n"
            f"• *Customer*: {conv.customer_name} ({conv.customer_phone})\n"
            f"• *Email*: {conv.customer_email}\n"
            f"• *Service*: {conv.selected_service_name}\n"
            f"• *Airport*: {conv.selected_airport_iata or 'N/A'}\n"
            f"• *Flight*: {conv.flight_num or 'N/A'}\n"
            f"• *Date*: {conv.booking_date}\n"
            f"• *Estimated Amount*: ₹{int(amount):,}\n"
            "• *Status*: PENDING (Payment & Confirmation Follow-up Required)"
        )
        whatsapp_client.send_text_message(officer_phone, team_msg)

        # 5. Customer & Team Email Notifications
        try:
            from app.services.notification_service import NotificationService
            NotificationService.notify_booking_created(db, {
                "booking_ref": booking_ref,
                "passenger_name": conv.customer_name,
                "passenger_email": conv.customer_email,
                "passenger_phone": conv.customer_phone,
                "flight_num": conv.flight_num,
                "origin_code": conv.selected_airport_iata,
                "dest_code": "DEST",
                "airport_code": conv.selected_airport_iata,
                "service_type": conv.selected_service_name,
                "total_amount": amount,
                "currency": "INR",
                "status": "PENDING",
            })
        except Exception as email_err:
            logger.warning(f"[WhatsApp Booking Notification] Email notification failed: {email_err}")

        return {
            "status": "booking_request_created",
            "booking_ref": booking_ref,
            "state": conv.current_state
        }


class WhatsAppService:
    """Unified WhatsApp Ingestion & Webhook Handler with Event Idempotency."""

    @classmethod
    def handle_incoming_webhook(cls, db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parses Meta webhook events safely with event-level idempotency.
        """
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

                # Process status events
                for st in val.get("statuses", []):
                    statuses_handled += 1
                    logger.info(f"[WhatsApp Webhook Status] Message {st.get('id')} status: {st.get('status')}")

                # Process message events
                for msg in val.get("messages", []):
                    msg_id = msg.get("id")
                    from_phone = msg.get("from")

                    # Idempotency check via WhatsAppWebhookEvent
                    if msg_id:
                        dup = db.execute(select(WhatsAppWebhookEvent).where(WhatsAppWebhookEvent.event_id == msg_id)).scalar_one_or_none()
                        if dup:
                            logger.info(f"[WhatsApp Webhook] Duplicate message ignored: {msg_id}")
                            continue

                        # Store event id
                        evt = WhatsAppWebhookEvent(id=uuid.uuid4(), event_id=msg_id, event_type="message", payload=msg)
                        db.add(evt)
                        db.commit()

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

                    # Delegate to Booking State Machine
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

        return {
            "status": "processed",
            "messages_handled": messages_handled,
            "statuses_handled": statuses_handled,
            "results": results
        }


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
