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
from datetime import datetime, timezone, timedelta
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
        Enforces 30-minute session inactivity expiry.
        Returns: (conversation_object, is_session_expired_flag)
        """
        clean_phone = "".join(filter(str.isdigit, str(phone_number)))
        stmt = select(WhatsAppConversation).where(WhatsAppConversation.phone_number == clean_phone)
        conv = db.execute(stmt).scalar_one_or_none()

        is_expired = False
        now_utc = datetime.now(timezone.utc)

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
        else:
            # Check 30-minute inactivity expiry for non-terminal active sessions
            last_act = conv.updated_at or conv.created_at
            if last_act:
                # Normalize timezone if naive
                if last_act.tzinfo is None:
                    last_act = last_act.replace(tzinfo=timezone.utc)
                inactivity_seconds = (now_utc - last_act).total_seconds()
                if inactivity_seconds > 1800 and conv.current_state not in ["START", "CANCELLED", "BOOKING_CONFIRMED"]:
                    logger.info(f"[WhatsApp Session] Session for {clean_phone} expired after {inactivity_seconds:.0f}s inactivity.")
                    conv = cls._reset_conversation_fields(conv)
                    conv.current_state = "START"
                    conv.updated_at = now_utc
                    db.commit()
                    db.refresh(conv)
                    is_expired = True

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

        text_upper = (user_input or "").strip().upper()

        # Handle expired session notification
        if session_expired:
            return cls._send_category_menu(db, conv, prefix_notice="Your previous session has expired. Let's start again.\n\n")

        # ── INTERRUPT COMMANDS (CANCEL, RESTART, BACK, HELP) ──

        if text_upper in ["CANCEL", "STOP", "ABORT"] or input_id == "btn_cancel":
            conv.current_state = "CANCELLED"
            db.commit()
            msg = "Your booking process has been cancelled. Type *Hi* anytime to start a new booking with Shafsky Aviation."
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "cancelled", "state": conv.current_state}

        if text_upper in ["RESTART", "START OVER", "RESET"] or input_id == "btn_restart":
            conv = cls._reset_conversation_fields(conv)
            conv.current_state = "START"
            db.commit()
            return cls._send_category_menu(db, conv)

        if text_upper in ["HELP", "SUPPORT", "INFO"]:
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

        if text_upper == "BACK" or input_id == "btn_back":
            cls._handle_back_action(db, conv)
            return {"status": "back", "state": conv.current_state}

        # ── ROUTE BY STATE ──
        state = conv.current_state

        if state in ["START", "CANCELLED", "BOOKING_CONFIRMED"]:
            return cls._state_start(db, conv, user_input)

        elif state == "CATEGORY_SELECTION":
            return cls._state_category_selection(db, conv, user_input, input_id)

        elif state == "JOURNEY_TYPE_SELECTION":
            return cls._state_journey_type_selection(db, conv, user_input, input_id)

        elif state == "AIRPORT_SELECTION":
            return cls._state_airport_selection(db, conv, user_input)

        elif state == "AIRPORT_CONFIRMATION":
            return cls._state_airport_confirmation(db, conv, user_input, input_id)

        elif state == "SERVICE_SELECTION":
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
        elif curr == "JOURNEY_TYPE_SELECTION":
            cls._send_category_menu(db, conv)
        elif curr == "AIRPORT_SELECTION":
            conv.current_state = "JOURNEY_TYPE_SELECTION"
            db.commit()
            cls._prompt_journey_type(conv)
        elif curr == "AIRPORT_CONFIRMATION":
            conv.selected_airport_iata = None
            conv.selected_airport_name = None
            conv.current_state = "AIRPORT_SELECTION"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL):")
        elif curr == "SERVICE_SELECTION":
            if conv.requires_airport:
                conv.current_state = "AIRPORT_SELECTION"
                db.commit()
                whatsapp_client.send_text_message(conv.phone_number, "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL):")
            else:
                cls._send_category_menu(db, conv)
        elif curr in ["FLIGHT_INPUT", "FLIGHT_CONFIRMATION"]:
            conv.flight_num = None
            conv.flight_details_json = None
            conv.current_state = "SERVICE_SELECTION"
            db.commit()
            cls._send_service_menu(db, conv, conv.selected_category or "Airport Services")
        elif curr == "DATE_SELECTION":
            if conv.requires_flight:
                conv.current_state = "FLIGHT_INPUT"
                db.commit()
                whatsapp_client.send_text_message(conv.phone_number, "Please enter your Flight Number (e.g., *EK501*, *AI2424*):")
            else:
                conv.current_state = "SERVICE_SELECTION"
                db.commit()
                cls._send_service_menu(db, conv, conv.selected_category or "Airport Services")
        elif curr == "PASSENGER_COUNT":
            conv.current_state = "DATE_SELECTION"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your Date of Travel (DD/MM/YYYY or YYYY-MM-DD):")
        elif curr == "CUSTOMER_NAME":
            conv.current_state = "PASSENGER_COUNT"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, "How many passengers will be travelling? (Enter a number, e.g., 2):")
        elif curr == "CUSTOMER_EMAIL":
            conv.current_state = "CUSTOMER_NAME"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, "May I have your full name?")
        elif curr == "CUSTOMER_PHONE":
            conv.current_state = "CUSTOMER_EMAIL"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, "Please provide your email address for booking confirmation:")
        elif curr == "ADDITIONAL_REQUIREMENTS":
            conv.current_state = "CUSTOMER_PHONE"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, "Please provide your contact phone number (or type 'Same'):")
        elif curr == "BOOKING_REVIEW":
            conv.current_state = "ADDITIONAL_REQUIREMENTS"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, "Do you have any special requirements or notes? (Type *None* if no special requests):")
        else:
            cls._send_category_menu(db, conv)

    # ── STATE IMPLEMENTATIONS ──

    @classmethod
    def _state_start(cls, db: Session, conv: WhatsAppConversation, user_input: str) -> Dict[str, Any]:
        """Greeting and display of final 4-option main menu."""
        conv = cls._reset_conversation_fields(conv)
        conv.current_state = "CATEGORY_SELECTION"
        db.commit()
        return cls._send_category_menu(db, conv)

    @classmethod
    def _send_category_menu(cls, db: Session, conv: WhatsAppConversation, prefix_notice: str = "") -> Dict[str, Any]:
        """Sends the exact 4-option main menu."""
        conv.current_state = "CATEGORY_SELECTION"
        db.commit()

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
            conv.current_state = "JOURNEY_TYPE_SELECTION"
            db.commit()
            return cls._prompt_journey_type(conv)

        elif matched_category == "Travel Services":
            conv.requires_airport = False
            conv.requires_flight = False
            conv.current_state = "SERVICE_SELECTION"
            db.commit()
            return cls._send_service_menu(db, conv, "Travel Services")

        elif matched_category == "Private Charter":
            conv.requires_airport = False
            conv.requires_flight = False
            conv.current_state = "SERVICE_SELECTION"
            db.commit()
            return cls._send_service_menu(db, conv, "Private Charter")

        elif matched_category == "Hotel & Transportation":
            conv.current_state = "HOTEL_TRANSPORT_SUBMENU"
            db.commit()
            return cls._prompt_hotel_transport_submenu(conv)

        return {"status": "category_selected"}

    # ── 1. AIRPORT SERVICES FLOW ──

    @classmethod
    def _prompt_journey_type(cls, conv: WhatsAppConversation) -> Dict[str, Any]:
        """Prompts customer for Arrival / Departure / Transit."""
        body_text = (
            "✈️ *Airport Services*\n\n"
            "Please select your journey type:"
        )
        buttons = [
            {"id": "btn_jt_departure", "title": "Departure"},
            {"id": "btn_jt_arrival", "title": "Arrival"},
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
                "1. Departure\n"
                "2. Arrival\n"
                "3. Transit\n\n"
                "Reply *Departure*, *Arrival*, or *Transit*."
            )
            whatsapp_client.send_text_message(conv.phone_number, fallback)

        return {"status": "journey_type_prompt_sent"}

    @classmethod
    def _state_journey_type_selection(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Processes journey type selection."""
        norm = (input_id or user_text).strip().upper()
        jt = None
        if "DEPARTURE" in norm or norm == "1" or norm == "btn_jt_departure":
            jt = "DEPARTURE"
        elif "ARRIVAL" in norm or norm == "2" or norm == "btn_jt_arrival":
            jt = "ARRIVAL"
        elif "TRANSIT" in norm or norm == "3" or norm == "btn_jt_transit":
            jt = "TRANSIT"

        if not jt:
            whatsapp_client.send_text_message(conv.phone_number, "Please select *Departure*, *Arrival*, or *Transit*.")
            return {"status": "invalid_journey_type"}

        # Store journey type in flight_details_json metadata
        conv.flight_details_json = {"journey_type": jt}
        conv.current_state = "AIRPORT_SELECTION"
        db.commit()

        msg = (
            f"Journey Type: *{jt.title()}*\n\n"
            "Please enter your Airport Name, City, or IATA Code (e.g., *Delhi*, *DEL*, *Indira Gandhi*):"
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
            whatsapp_client.send_text_message(conv.phone_number, "Please enter an Airport Name, City, or IATA Code (e.g. Delhi, DEL).")
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
            msg = "This airport is currently unavailable for online booking."
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "unsupported_airport"}

        conv.selected_airport_name = airport.airport_name
        conv.selected_airport_iata = airport.iata_code
        conv.selected_airport_city = airport.city
        conv.selected_airport_country = airport.country
        conv.current_state = "AIRPORT_CONFIRMATION"
        db.commit()

        body_text = (
            f"📍 *Airport Resolved*\n\n"
            f"{airport.airport_name}\n"
            f"{airport.iata_code}\n"
            f"{airport.city}\n\n"
            "Please confirm if this is correct:"
        )

        buttons = [
            {"id": "btn_confirm_airport", "title": "Confirm"},
            {"id": "btn_change_airport", "title": "Change Airport"}
        ]

        res = whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Confirm Airport"
        )
        if not res.get("success"):
            fallback = (
                f"📍 *Airport Resolved*\n\n"
                f"{airport.airport_name}\n"
                f"{airport.iata_code}\n"
                f"{airport.city}\n\n"
                "Reply *Confirm* or *Change Airport*."
            )
            whatsapp_client.send_text_message(conv.phone_number, fallback)

        return {"status": "airport_confirmation_prompt"}

    @classmethod
    def _state_airport_confirmation(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Handles airport confirmation."""
        text_u = (input_id or user_text).strip().upper()

        if "CHANGE" in text_u or "NO" in text_u or text_u == "btn_change_airport":
            conv.selected_airport_iata = None
            conv.selected_airport_name = None
            conv.selected_airport_city = None
            conv.selected_airport_country = None
            conv.current_state = "AIRPORT_SELECTION"
            db.commit()
            msg = "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL):"
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "reprompt_airport"}

        if "CONFIRM" in text_u or "YES" in text_u or text_u == "1" or text_u == "btn_confirm_airport":
            conv.current_state = "SERVICE_SELECTION"
            db.commit()
            return cls._send_airport_services_menu(db, conv)

        whatsapp_client.send_text_message(conv.phone_number, "Please select *Confirm* or *Change Airport*.")
        return {"status": "invalid_airport_confirmation"}

    @classmethod
    def _send_airport_services_menu(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        """
        Queries and presents ONLY services/packages configured for the selected airport and journey type in the database.
        """
        iata = conv.selected_airport_iata
        jt = (conv.flight_details_json or {}).get("journey_type", "DEPARTURE").upper() if isinstance(conv.flight_details_json, dict) else "DEPARTURE"

        # Query database SupportedAirport
        airport = db.execute(select(SupportedAirport).where(SupportedAirport.iata_code == iata)).scalar_one_or_none()

        available_services = []
        if airport:
            # Query AirportService mappings for this airport and journey type
            stmt = (
                select(AirportService, Service)
                .join(Service, AirportService.service_id == Service.id)
                .where(
                    AirportService.airport_id == airport.id,
                    AirportService.journey_type == jt,
                    AirportService.is_available == True,
                    Service.is_active == True
                )
                .order_by(Service.display_order)
            )
            rows = db.execute(stmt).all()
            for aps, svc in rows:
                available_services.append({
                    "id": str(svc.id),
                    "title": svc.name,
                    "price": float(aps.price or 2500.0),
                    "description": svc.description or "VIP Airport Service"
                })

        # Fallback to standard active catalog if no specific mapping found
        if not available_services:
            try:
                admin_cats = ServiceConfigService.get_admin_catalog(db)
                for s in admin_cats:
                    if s.get("category") in ["Airport Assistance", "Airport Services"] and s.get("is_active"):
                        available_services.append({
                            "id": str(s.get("id")),
                            "title": str(s.get("title", s.get("name"))),
                            "price": float(s.get("base_price", s.get("price", 2500))),
                            "description": s.get("description", "")
                        })
            except Exception:
                for s in DEFAULT_SERVICE_CATALOG:
                    if s.get("category") == "Airport Assistance":
                        available_services.append({
                            "id": str(s.get("id")),
                            "title": s["title"],
                            "price": float(s.get("base_price", 2500)),
                            "description": s.get("description", "")
                        })

        list_rows = []
        for s in available_services[:10]:
            list_rows.append({
                "id": f"svc_id_{s['id']}",
                "title": s["title"][:24],
                "description": f"₹{int(s['price']):,} | {s['description'][:45]}"
            })

        body_text = (
            f"Airport: *{conv.selected_airport_name} ({conv.selected_airport_iata})*\n"
            f"Journey Type: *{jt.title()}*\n\n"
            "Please select your desired VIP service/package:"
        )
        sections = [{"title": "Available Services", "rows": list_rows}]

        res = whatsapp_client.send_interactive_list(
            to_phone=conv.phone_number,
            body_text=body_text,
            button_title="Select Service",
            sections=sections,
            header_text="Available Packages"
        )
        if not res.get("success"):
            lines = [f"Airport: *{conv.selected_airport_name} ({conv.selected_airport_iata})*\nJourney Type: *{jt.title()}*\n\nPlease reply with the service number:"]
            for idx, s in enumerate(available_services, 1):
                lines.append(f"{idx}. *{s['title']}* — ₹{int(s['price']):,}\n   _{s['description']}_")
            whatsapp_client.send_text_message(conv.phone_number, "\n".join(lines))

        return {"status": "airport_services_sent"}

    # ── 2. TRAVEL SERVICES & PRIVATE CHARTER & HOTEL FLOW ──

    @classmethod
    def _prompt_hotel_transport_submenu(cls, conv: WhatsAppConversation) -> Dict[str, Any]:
        """Prompts submenu for Hotel & Transportation."""
        body_text = (
            "🏨 *Hotel & Transportation*\n\n"
            "Please select an option:\n"
            "1️⃣ Hotel Booking\n"
            "2️⃣ Transportation"
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
            conv.current_state = "HOTEL_CITY"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, "🏨 *Hotel Booking*\n\nPlease enter your destination city or preferred hotel:")
            return {"status": "hotel_city_prompt_sent"}

        elif "TRANSPORT" in norm or norm == "2" or norm == "btn_sub_transport":
            conv.selected_service_name = "Premium Ground Transport"
            conv.requires_airport = False
            conv.requires_flight = False
            conv.current_state = "TRANSPORT_PICKUP"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, "🚗 *Transportation*\n\nPlease enter your pickup location:")
            return {"status": "transport_pickup_prompt_sent"}

        whatsapp_client.send_text_message(conv.phone_number, "Please select *1. Hotel Booking* or *2. Transportation*.")
        return {"status": "invalid_submenu_choice"}

    @classmethod
    def _state_hotel_city(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        city = user_text.strip()
        conv.selected_airport_city = city
        conv.current_state = "HOTEL_NIGHTS"
        db.commit()
        whatsapp_client.send_text_message(conv.phone_number, f"City: *{city}*\n\nHow many nights will you be staying? (e.g. 2):")
        return {"status": "hotel_nights_prompt"}

    @classmethod
    def _state_hotel_nights(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        digits = "".join(filter(str.isdigit, user_text)) or "1"
        conv.additional_requirements = f"Hotel in {conv.selected_airport_city}, {digits} nights"
        conv.current_state = "DATE_SELECTION"
        db.commit()
        whatsapp_client.send_text_message(conv.phone_number, "Please enter your Check-in Date (DD/MM/YYYY or YYYY-MM-DD):")
        return {"status": "hotel_date_prompt"}

    @classmethod
    def _state_transport_pickup(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        pickup = user_text.strip()
        conv.selected_airport_city = pickup
        conv.current_state = "TRANSPORT_DROPOFF"
        db.commit()
        whatsapp_client.send_text_message(conv.phone_number, f"Pickup: *{pickup}*\n\nPlease enter your drop-off destination:")
        return {"status": "transport_dropoff_prompt"}

    @classmethod
    def _state_transport_dropoff(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        dropoff = user_text.strip()
        conv.additional_requirements = f"Route: {conv.selected_airport_city} to {dropoff}"
        conv.current_state = "DATE_SELECTION"
        db.commit()
        whatsapp_client.send_text_message(conv.phone_number, "Please enter your Date of Travel (DD/MM/YYYY or YYYY-MM-DD):")
        return {"status": "transport_date_prompt"}

    @classmethod
    def _state_charter_origin(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        origin = user_text.strip()
        conv.selected_airport_city = origin
        conv.current_state = "CHARTER_DESTINATION"
        db.commit()
        whatsapp_client.send_text_message(conv.phone_number, f"Departure: *{origin}*\n\nPlease enter your destination city / airport:")
        return {"status": "charter_destination_prompt"}

    @classmethod
    def _state_charter_destination(cls, db: Session, conv: WhatsAppConversation, user_text: str) -> Dict[str, Any]:
        destination = user_text.strip()
        conv.additional_requirements = f"Private Charter: {conv.selected_airport_city} to {destination}"
        conv.current_state = "DATE_SELECTION"
        db.commit()
        whatsapp_client.send_text_message(conv.phone_number, "Please enter your Date of Travel (DD/MM/YYYY or YYYY-MM-DD):")
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

        rows = []
        for i, s in enumerate(cat_services[:10]):
            svc_id = s.get("id", f"svc_{i}")
            svc_name = s.get("title", s.get("name", "Service"))
            price = s.get("base_price", s.get("price", 0))
            price_display = f"₹{int(price):,}" if price > 0 else "Quote on Request"
            rows.append({
                "id": f"svc_id_{svc_id}",
                "title": svc_name[:24],
                "description": f"{price_display} | {s.get('description', '')[:45]}"
            })

        body_text = f"Category: *{category_name}*\n\nPlease select the service you wish to request:"
        sections = [{"title": f"{category_name} Catalogue", "rows": rows}]

        res = whatsapp_client.send_interactive_list(
            to_phone=conv.phone_number,
            body_text=body_text,
            button_title="Select Service",
            sections=sections,
            header_text="Service Catalogue"
        )
        if not res.get("success"):
            lines = [f"Category: *{category_name}*\n\nPlease reply with the service number:"]
            for idx, s in enumerate(cat_services, 1):
                p = s.get("base_price", s.get("price", 0))
                p_str = f"₹{int(p):,}" if p > 0 else "Quote on Request"
                lines.append(f"{idx}. *{s.get('title', s.get('name'))}* — {p_str}\n   _{s.get('description', '')}_")
            whatsapp_client.send_text_message(conv.phone_number, "\n".join(lines))

        return {"status": "service_menu_sent"}

    @classmethod
    def _state_service_selection(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Handles selection of a specific service/package."""
        category_name = conv.selected_category or "Airport Services"
        selected_svc = None

        # Check Airport Services from DB if category is Airport Services
        if category_name == "Airport Services" and conv.selected_airport_iata:
            airport = db.execute(select(SupportedAirport).where(SupportedAirport.iata_code == conv.selected_airport_iata)).scalar_one_or_none()
            if airport:
                jt = (conv.flight_details_json or {}).get("journey_type", "DEPARTURE").upper() if isinstance(conv.flight_details_json, dict) else "DEPARTURE"
                stmt = select(AirportService, Service).join(Service, AirportService.service_id == Service.id).where(
                    AirportService.airport_id == airport.id,
                    AirportService.journey_type == jt,
                    AirportService.is_available == True
                )
                rows = db.execute(stmt).all()
                services_list = [{"id": str(svc.id), "title": svc.name, "price": float(aps.price or 2500.0)} for aps, svc in rows]

                if input_id and input_id.startswith("svc_id_"):
                    raw_id = input_id.replace("svc_id_", "")
                    selected_svc = next((s for s in services_list if str(s["id"]) == raw_id), None)
                if not selected_svc:
                    clean = user_text.strip().lower()
                    if clean.isdigit():
                        idx = int(clean) - 1
                        if 0 <= idx < len(services_list):
                            selected_svc = services_list[idx]
                    else:
                        for s in services_list:
                            if s["title"].lower() in clean or clean in s["title"].lower():
                                selected_svc = s
                                break

        # Check General Catalogue
        if not selected_svc:
            category_obj = next((c for c in OFFICIAL_CATEGORIES if c["name"] == category_name), None)
            valid_db_cats = category_obj["db_categories"] if category_obj else [category_name]
            try:
                all_services = ServiceConfigService.get_admin_catalog(db)
            except Exception:
                all_services = DEFAULT_SERVICE_CATALOG
            cat_services = [s for s in all_services if s.get("category") in valid_db_cats or s.get("category") == category_name]
            if not cat_services:
                cat_services = [s for s in DEFAULT_SERVICE_CATALOG if s.get("category") in valid_db_cats or s.get("category") == category_name]

            if input_id and input_id.startswith("svc_id_"):
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
            conv.current_state = "FLIGHT_INPUT"
            db.commit()
            msg = f"Selected Service: *{svc_title}*\n\nPlease enter your Flight Number (e.g., *EK501*, *AI2424*, *6E224*):"
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "flight_prompt_sent"}

        elif category_name == "Private Charter":
            conv.current_state = "CHARTER_ORIGIN"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, f"Selected Charter: *{svc_title}*\n\nPlease enter your departure city / airport:")
            return {"status": "charter_origin_prompt"}

        else:
            conv.current_state = "DATE_SELECTION"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, f"Selected Service: *{svc_title}*\n\nPlease enter your Date of Travel (DD/MM/YYYY or YYYY-MM-DD):" )
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
        conv.flight_details_json = {
            "flight_number": norm_flight,
            "airline_code": val_res["airline_code"],
            "journey_type": jt,
            "verification_status": "not_verified",
            "status": "flight_number_received"
        }
        conv.current_state = "FLIGHT_CONFIRMATION"
        db.commit()

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
            conv.current_state = "FLIGHT_INPUT"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your flight number (e.g., *EK501*, *AI2424*, *6E224*):")
            return {"status": "reprompt_flight"}

        if "CONFIRM" in text_u or "YES" in text_u or text_u == "1" or text_u == "btn_confirm_flight":
            conv.current_state = "DATE_SELECTION"
            db.commit()
            msg = f"Flight Number Received: *{conv.flight_num}*\n\nPlease enter your Date of Travel (DD/MM/YYYY or YYYY-MM-DD):"
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "date_prompt_sent"}

        whatsapp_client.send_text_message(conv.phone_number, "Please select *Confirm Flight* or *Re-enter Flight*.")
        return {"status": "invalid_flight_confirmation"}

    # ── 4. DYNAMIC DETAILS COLLECTION (DATE, PASSENGERS, CUSTOMER DETAILS) ──

    @classmethod
    def _state_date_selection(cls, db: Session, conv: WhatsAppConversation, date_input: str) -> Dict[str, Any]:
        clean_date = date_input.strip()
        parsed_dt = None
        for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"]:
            try:
                parsed_dt = datetime.strptime(clean_date, fmt)
                break
            except Exception:
                pass

        if not parsed_dt:
            whatsapp_client.send_text_message(
                conv.phone_number,
                "Invalid date format. Please enter date in DD/MM/YYYY or YYYY-MM-DD format (e.g., 15/08/2026)."
            )
            return {"status": "invalid_date"}

        date_formatted = parsed_dt.strftime("%d %B %Y")
        conv.booking_date = date_formatted
        conv.current_state = "PASSENGER_COUNT"
        db.commit()

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
        conv.current_state = "CUSTOMER_NAME"
        db.commit()

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
        conv.current_state = "CUSTOMER_EMAIL"
        db.commit()

        msg = f"Name: *{clean_name}*\n\nPlease provide your email address for booking confirmation:"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "email_prompt_sent"}

    @classmethod
    def _state_customer_email(cls, db: Session, conv: WhatsAppConversation, email_input: str) -> Dict[str, Any]:
        clean_email = email_input.strip().lower()
        email_regex = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
        if not re.match(email_regex, clean_email):
            whatsapp_client.send_text_message(
                conv.phone_number,
                "Invalid email address format. Please provide a valid email address (e.g., name@example.com)."
            )
            return {"status": "invalid_email"}

        conv.customer_email = clean_email
        conv.current_state = "CUSTOMER_PHONE"
        db.commit()

        msg = f"Email Saved: *{clean_email}*\n\nPlease provide your contact phone number (or type 'Same' to use this WhatsApp number):"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "phone_prompt_sent"}

    @classmethod
    def _state_customer_phone(cls, db: Session, conv: WhatsAppConversation, phone_input: str) -> Dict[str, Any]:
        clean_p = phone_input.strip().lower()
        if clean_p in ["same", "yes", "this"]:
            conv.customer_phone = conv.phone_number
        else:
            digits = "".join(filter(str.isdigit, clean_p))
            if len(digits) < 10:
                whatsapp_client.send_text_message(conv.phone_number, "Invalid phone number. Please provide a valid contact number (min 10 digits).")
                return {"status": "invalid_phone"}
            conv.customer_phone = digits

        conv.current_state = "ADDITIONAL_REQUIREMENTS"
        db.commit()

        msg = "Do you have any special requirements or notes? (Type *None* if no special requests):"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "notes_prompt_sent"}

    @classmethod
    def _state_additional_requirements(cls, db: Session, conv: WhatsAppConversation, notes_input: str) -> Dict[str, Any]:
        clean_notes = notes_input.strip()
        conv.additional_requirements = "None" if clean_notes.lower() in ["none", "no", "n/a", "-"] else clean_notes
        conv.current_state = "BOOKING_REVIEW"
        db.commit()

        return cls._send_booking_summary(db, conv)

    # ── 5. BOOKING SUMMARY & CREATION (NO PAYMENT GATEWAY) ──

    @classmethod
    def _send_booking_summary(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        """Displays booking summary before creation."""
        jt = (conv.flight_details_json or {}).get("journey_type") if isinstance(conv.flight_details_json, dict) else None

        summary_lines = [
            "📋 *BOOKING SUMMARY — Shafsky Aviation*\n",
            f"• *Service*: {conv.selected_service_name or 'VIP Service'}",
        ]
        if conv.selected_airport_iata:
            summary_lines.append(f"• *Airport*: {conv.selected_airport_name} ({conv.selected_airport_iata})")
        if jt:
            summary_lines.append(f"• *Travel Type*: {jt.title()}")
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
            conv.current_state = "CUSTOMER_NAME"
            db.commit()
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
            conv.current_state = "BOOKING_CONFIRMED"
            conv.payment_status = "PENDING"
            conv.updated_at = datetime.now(timezone.utc)
            db.commit()

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
                            logger.info(f"[WhatsApp Idempotency] Event {msg_id} already processed. Skipping.")
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
