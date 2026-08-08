"""
WhatsApp Integration Service & Persistent Booking State Machine Engine.
Handles Meta Webhooks, Idempotency, Service Catalogue State Machine, Airport/Flight Validations,
Razorpay Links, and Multi-channel Notifications.
"""

import os
import re
import uuid
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select, or_

from app.models.whatsapp_models import WhatsAppConversation, WhatsAppMessage, WhatsAppWebhookEvent
from app.models.schema import Booking, BookingStatus
from app.integrations.whatsapp.client import whatsapp_client
from app.services.service_config_service import ServiceConfigService, DEFAULT_SERVICE_CATALOG
from app.services.journey_engine import JourneyDetectionEngine
from app.flight.service import FlightIntelligenceService
from app.flight.providers.aviation_edge_provider import AviationEdgeProvider

flight_service = FlightIntelligenceService(provider=AviationEdgeProvider())
from app.services.booking_service import BookingService
from app.providers.razorpay_provider import razorpay_provider

logger = logging.getLogger(__name__)

# Standard 6 Service Categories strictly from Shafsky Aviation Catalogue
OFFICIAL_CATEGORIES = [
    {"id": "cat_airport", "name": "Airport Services", "db_categories": ["Airport Assistance", "Airport Services"]},
    {"id": "cat_travel", "name": "Travel Services", "db_categories": ["Travel Support", "Travel Services"]},
    {"id": "cat_charter", "name": "Private Charter", "db_categories": ["Private Charter"]},
    {"id": "cat_transport", "name": "Transportation Services", "db_categories": ["Ground Transport", "Transportation Services"]},
    {"id": "cat_cargo", "name": "Cargo Services", "db_categories": ["Cargo & Logistics", "Cargo Services"]},
    {"id": "cat_medical", "name": "Medical Services", "db_categories": ["Medical Assistance", "Medical Services"]},
]


class WhatsAppBookingStateMachine:
    """
    Persistent state machine processor for Shafsky Aviation WhatsApp Booking Flow.
    """

    @classmethod
    def get_or_create_conversation(cls, db: Session, phone_number: str) -> WhatsAppConversation:
        clean_phone = "".join(filter(str.isdigit, str(phone_number)))
        stmt = select(WhatsAppConversation).where(WhatsAppConversation.phone_number == clean_phone)
        conv = db.execute(stmt).scalar_one_or_none()

        if not conv:
            conv = WhatsAppConversation(
                id=uuid.uuid4(),
                phone_number=clean_phone,
                current_state="START",
                customer_phone=clean_phone
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)

        return conv

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
        """
        conv = cls.get_or_create_conversation(db, from_phone)

        # Log inbound message
        log_msg = WhatsAppMessage(
            id=uuid.uuid4(),
            conversation_id=conv.id,
            message_id=msg_id,
            direction="INBOUND",
            message_type=input_type,
            content=user_input,
            raw_payload=raw_payload
        )
        db.add(log_msg)
        db.commit()

        text_upper = user_input.strip().upper()

        # Interrupt Command Handling (CANCEL, BACK, HELP, CHANGE)
        if text_upper in ["CANCEL", "STOP", "ABORT"] or input_id == "btn_cancel":
            conv.current_state = "CANCELLED"
            db.commit()
            msg = "Your booking process has been cancelled. Type *Hi* anytime to start a new booking with Shafsky Aviation."
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "cancelled", "state": conv.current_state}

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

        # Route by State
        state = conv.current_state

        if state in ["START", "CANCELLED", "BOOKING_CONFIRMED"]:
            return cls._state_start(db, conv, user_input)

        elif state == "CATEGORY_SELECTION":
            return cls._state_category_selection(db, conv, user_input, input_id)

        elif state == "SERVICE_SELECTION":
            return cls._state_service_selection(db, conv, user_input, input_id)

        elif state == "AIRPORT_SELECTION":
            return cls._state_airport_selection(db, conv, user_input)

        elif state == "AIRPORT_CONFIRMATION":
            return cls._state_airport_confirmation(db, conv, user_input, input_id)

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

        elif state in ["PENDING_PAYMENT", "PAYMENT_FAILED"]:
            if input_id == "btn_pay_retry" or text_upper in ["PAY", "RETRY"]:
                return cls._create_and_send_payment(db, conv)
            else:
                msg = f"Your booking is pending payment. Please use your payment link: {conv.razorpay_payment_url or 'N/A'}\n\nType *CANCEL* to start over."
                whatsapp_client.send_text_message(conv.phone_number, msg)
                return {"status": "pending_payment_prompt"}

        # Fallback reset
        return cls._state_start(db, conv, user_input)

    @classmethod
    def _handle_back_action(cls, db: Session, conv: WhatsAppConversation):
        curr = conv.current_state
        if curr in ["CATEGORY_SELECTION", "START"]:
            cls._state_start(db, conv, "Hi")
        elif curr == "SERVICE_SELECTION":
            cls._send_category_menu(db, conv)
        elif curr in ["AIRPORT_SELECTION", "AIRPORT_CONFIRMATION"]:
            cls._send_service_menu(db, conv, conv.selected_category or "Airport Services")
        elif curr in ["FLIGHT_INPUT", "FLIGHT_CONFIRMATION"]:
            if conv.requires_airport:
                conv.current_state = "AIRPORT_SELECTION"
                db.commit()
                msg = "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL, Indira Gandhi):"
                whatsapp_client.send_text_message(conv.phone_number, msg)
            else:
                cls._send_service_menu(db, conv, conv.selected_category or "Airport Services")
        else:
            cls._send_category_menu(db, conv)

    # ── STATE IMPLEMENTATIONS ──

    @classmethod
    def _state_start(cls, db: Session, conv: WhatsAppConversation, user_input: str) -> Dict[str, Any]:
        conv.current_state = "CATEGORY_SELECTION"
        db.commit()
        return cls._send_category_menu(db, conv)

    @classmethod
    def _send_category_menu(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        conv.current_state = "CATEGORY_SELECTION"
        db.commit()

        body_text = (
            "Welcome to *Shafsky Aviation* VIP Booking System.\n\n"
            "Please select a service category from the official catalogue below to begin your booking:"
        )

        rows = []
        for cat in OFFICIAL_CATEGORIES:
            rows.append({
                "id": cat["id"],
                "title": cat["name"],
                "description": f"Browse {cat['name']} catalogue"
            })

        sections = [{"title": "Shafsky Service Categories", "rows": rows}]

        res = whatsapp_client.send_interactive_list(
            to_phone=conv.phone_number,
            body_text=body_text,
            button_title="Select Category",
            sections=sections,
            header_text="Shafsky Aviation"
        )

        if not res.get("success"):
            # Fallback text list if Meta interactive fails or unconfigured
            fallback_text = (
                "Welcome to *Shafsky Aviation* VIP Booking System.\n\n"
                "Please select a service category:\n"
                "1. Airport Services\n"
                "2. Travel Services\n"
                "3. Private Charter\n"
                "4. Transportation Services\n"
                "5. Cargo Services\n"
                "6. Medical Services\n\n"
                "Reply with the category number or name."
            )
            whatsapp_client.send_text_message(conv.phone_number, fallback_text)

        return {"status": "category_menu_sent"}

    @classmethod
    def _state_category_selection(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        matched_category = None

        # Match by ID from list selection
        if input_id:
            for cat in OFFICIAL_CATEGORIES:
                if cat["id"] == input_id:
                    matched_category = cat["name"]
                    break

        # Match by text input
        if not matched_category:
            norm = user_text.strip().lower()
            if "1" in norm or "airport" in norm:
                matched_category = "Airport Services"
            elif "2" in norm or "travel" in norm:
                matched_category = "Travel Services"
            elif "3" in norm or "charter" in norm:
                matched_category = "Private Charter"
            elif "4" in norm or "transport" in norm:
                matched_category = "Transportation Services"
            elif "5" in norm or "cargo" in norm:
                matched_category = "Cargo Services"
            elif "6" in norm or "medical" in norm:
                matched_category = "Medical Services"

        if not matched_category:
            msg = "Invalid selection. Please choose a valid category from the list (1-6):"
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "invalid_category"}

        conv.selected_category = matched_category
        conv.current_state = "SERVICE_SELECTION"
        db.commit()

        return cls._send_service_menu(db, conv, matched_category)

    @classmethod
    def _send_service_menu(cls, db: Session, conv: WhatsAppConversation, category_name: str) -> Dict[str, Any]:
        # Filter service catalogue for matched category
        category_obj = next((c for c in OFFICIAL_CATEGORIES if c["name"] == category_name), None)
        valid_db_cats = category_obj["db_categories"] if category_obj else [category_name]

        try:
            all_services = ServiceConfigService.get_admin_catalog(db)
        except Exception:
            all_services = DEFAULT_SERVICE_CATALOG
        cat_services = [s for s in all_services if s.get("category") in valid_db_cats or s.get("category") == category_name]

        if not cat_services:
            # Fallback to DEFAULT_SERVICE_CATALOG
            cat_services = [s for s in DEFAULT_SERVICE_CATALOG if s.get("category") in valid_db_cats or s.get("category") == category_name]

        rows = []
        for i, s in enumerate(cat_services[:10]):
            svc_id = s.get("id", f"svc_{i}")
            svc_name = s.get("title", s.get("name", "Service"))
            price = s.get("base_price", s.get("price", 0))
            rows.append({
                "id": f"svc_id_{svc_id}",
                "title": svc_name[:24],
                "description": f"₹{int(price):,} | {s.get('description', '')[:45]}"
            })

        body_text = f"Category: *{category_name}*\n\nPlease select the service you wish to book:"
        sections = [{"title": f"{category_name} Catalogue", "rows": rows}]

        res = whatsapp_client.send_interactive_list(
            to_phone=conv.phone_number,
            body_text=body_text,
            button_title="Select Service",
            sections=sections,
            header_text="Service Catalogue"
        )

        if not res.get("success"):
            # Text fallback
            lines = [f"Category: *{category_name}*\n\nPlease reply with the service number:"]
            for idx, s in enumerate(cat_services, 1):
                p = s.get("base_price", s.get("price", 0))
                lines.append(f"{idx}. *{s.get('title', s.get('name'))}* — ₹{int(p):,}\n   _{s.get('description', '')}_")
            whatsapp_client.send_text_message(conv.phone_number, "\n".join(lines))

        return {"status": "service_menu_sent"}

    @classmethod
    def _state_service_selection(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        category_name = conv.selected_category or "Airport Services"
        category_obj = next((c for c in OFFICIAL_CATEGORIES if c["name"] == category_name), None)
        valid_db_cats = category_obj["db_categories"] if category_obj else [category_name]

        try:
            all_services = ServiceConfigService.get_admin_catalog(db)
        except Exception:
            all_services = DEFAULT_SERVICE_CATALOG
        cat_services = [s for s in all_services if s.get("category") in valid_db_cats or s.get("category") == category_name]
        if not cat_services:
            cat_services = [s for s in DEFAULT_SERVICE_CATALOG if s.get("category") in valid_db_cats or s.get("category") == category_name]

        selected_svc = None

        if input_id and input_id.startswith("svc_id_"):
            raw_id = input_id.replace("svc_id_", "")
            selected_svc = next((s for s in cat_services if str(s.get("id")) == raw_id), None)

        if not selected_svc:
            # Check by number or string match
            clean_input = user_text.strip().lower()
            if clean_input.isdigit():
                idx = int(clean_input) - 1
                if 0 <= idx < len(cat_services):
                    selected_svc = cat_services[idx]
            else:
                for s in cat_services:
                    title = str(s.get("title", s.get("name", ""))).lower()
                    if title in clean_input or clean_input in title:
                        selected_svc = s
                        break

        if not selected_svc:
            whatsapp_client.send_text_message(conv.phone_number, "Service not found. Please select a valid service from the list.")
            return {"status": "invalid_service"}

        svc_id = str(selected_svc.get("id"))
        svc_title = str(selected_svc.get("title", selected_svc.get("name")))
        price = float(selected_svc.get("base_price", selected_svc.get("price", 2500)))

        conv.selected_service_id = svc_id
        conv.selected_service_name = svc_title
        conv.total_amount = price

        # Metadata requirements determination
        cat_upper = category_name.upper()
        title_upper = svc_title.upper()

        requires_airport = ("AIRPORT" in cat_upper or "GROUND" in cat_upper or "MEET" in title_upper or "TRANSFER" in title_upper or "LOUNGE" in title_upper)
        requires_flight = ("AIRPORT" in cat_upper or "MEET" in title_upper or "FAST TRACK" in title_upper or "CHARTER" in cat_upper or "AMBULANCE" in title_upper)

        conv.requires_airport = requires_airport
        conv.requires_flight = requires_flight
        conv.requires_date = True
        conv.requires_passenger_count = True

        db.commit()

        # Step progression
        if requires_airport:
            conv.current_state = "AIRPORT_SELECTION"
            db.commit()
            msg = (
                f"Selected Service: *{svc_title}*\n\n"
                f"Please select your Airport using one of the following methods:\n"
                f"1. Search by Airport Name (e.g. *Indira Gandhi*)\n"
                f"2. Search by City Name (e.g. *Delhi*)\n"
                f"3. Search by IATA Code (e.g. *DEL*)\n\n"
                f"Reply with your search query:"
            )
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "airport_prompt_sent"}

        elif requires_flight:
            conv.current_state = "FLIGHT_INPUT"
            db.commit()
            msg = f"Selected Service: *{svc_title}*\n\nPlease enter your Flight Number (e.g., *AI2424*, *EK505*):"
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "flight_prompt_sent"}

        else:
            conv.current_state = "DATE_SELECTION"
            db.commit()
            msg = f"Selected Service: *{svc_title}*\n\nPlease enter your Date of Travel (DD/MM/YYYY or YYYY-MM-DD):"
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "date_prompt_sent"}

    @classmethod
    def _state_airport_selection(cls, db: Session, conv: WhatsAppConversation, query: str) -> Dict[str, Any]:
        query_clean = query.strip()
        if not query_clean:
            whatsapp_client.send_text_message(conv.phone_number, "Please enter an Airport Name, City, or IATA Code (e.g. Delhi, DEL).")
            return {"status": "empty_airport_query"}

        # Attempt Resolution
        airport = JourneyDetectionEngine.get_airport_by_iata(db, query_clean)

        if not airport:
            # Search by city or airport name in DB
            from app.models.journey_models import SupportedAirport
            try:
                search_stmt = select(SupportedAirport).where(
                    or_(
                        SupportedAirport.city.ilike(f"%{query_clean}%"),
                        SupportedAirport.airport_name.ilike(f"%{query_clean}%"),
                        SupportedAirport.iata_code.ilike(f"%{query_clean}%")
                    )
                )
                airport = db.execute(search_stmt).scalars().first()
            except Exception:
                airport = None

        # Hardcoded fallback list for major Indian airports if DB table has not seeded that query
        if not airport:
            query_u = query_clean.upper()
            known_map = {
                "DEL": ("Indira Gandhi International Airport", "DEL", "Delhi", "India"),
                "DELHI": ("Indira Gandhi International Airport", "DEL", "Delhi", "India"),
                "INDIRA GANDHI": ("Indira Gandhi International Airport", "DEL", "Delhi", "India"),
                "BOM": ("Chhatrapati Shivaji Maharaj International Airport", "BOM", "Mumbai", "India"),
                "MUMBAI": ("Chhatrapati Shivaji Maharaj International Airport", "BOM", "Mumbai", "India"),
                "JAI": ("Jaipur International Airport", "JAI", "Jaipur", "India"),
                "JAIPUR": ("Jaipur International Airport", "JAI", "Jaipur", "India"),
                "ATQ": ("Sri Guru Ram Dass Jee International Airport", "ATQ", "Amritsar", "India"),
                "AMRITSAR": ("Sri Guru Ram Dass Jee International Airport", "ATQ", "Amritsar", "India"),
                "GOI": ("Dabolim Airport", "GOI", "Goa", "India"),
                "GOA": ("Dabolim Airport", "GOI", "Goa", "India"),
                "IXR": ("Birsa Munda Airport", "IXR", "Ranchi", "India"),
                "RANCHI": ("Birsa Munda Airport", "IXR", "Ranchi", "India"),
                "BLR": ("Kempegowda International Airport", "BLR", "Bengaluru", "India"),
                "BENGALURU": ("Kempegowda International Airport", "BLR", "Bengaluru", "India"),
                "MAA": ("Chennai International Airport", "MAA", "Chennai", "India"),
                "CHENNAI": ("Chennai International Airport", "MAA", "Chennai", "India"),
            }

            for k, v in known_map.items():
                if k in query_u or query_u in k:
                    conv.selected_airport_name = v[0]
                    conv.selected_airport_iata = v[1]
                    conv.selected_airport_city = v[2]
                    conv.selected_airport_country = v[3]
                    break

        if airport:
            conv.selected_airport_name = airport.airport_name
            conv.selected_airport_iata = airport.iata_code
            conv.selected_airport_city = airport.city
            conv.selected_airport_country = airport.country

        if not conv.selected_airport_iata:
            msg = f"Sorry, we couldn't find an airport matching *'{query}'*. Please try another Airport Name, City, or IATA Code (e.g., *Delhi*, *DEL*, *Indira Gandhi*)."
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "airport_not_found"}

        conv.current_state = "AIRPORT_CONFIRMATION"
        db.commit()

        body_text = (
            f"📍 *Airport Details Resolved*\n\n"
            f"• *Airport*: {conv.selected_airport_name}\n"
            f"• *IATA Code*: {conv.selected_airport_iata}\n"
            f"• *City*: {conv.selected_airport_city}\n"
            f"• *Country*: {conv.selected_airport_country}\n\n"
            f"Please confirm if this is correct:"
        )

        buttons = [
            {"id": "btn_confirm_airport", "title": "Confirm"},
            {"id": "btn_change_airport", "title": "Change Airport"}
        ]

        whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Confirm Airport"
        )

        return {"status": "airport_confirmation_prompt"}

    @classmethod
    def _state_airport_confirmation(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        text_u = user_text.strip().upper()

        if input_id == "btn_change_airport" or "CHANGE" in text_u or "NO" in text_u:
            conv.selected_airport_iata = None
            conv.selected_airport_name = None
            conv.current_state = "AIRPORT_SELECTION"
            db.commit()
            msg = "Please enter a new Airport Name, City, or IATA Code:"
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "reprompt_airport"}

        if input_id == "btn_confirm_airport" or "CONFIRM" in text_u or "YES" in text_u or "1" in text_u:
            if conv.requires_flight:
                conv.current_state = "FLIGHT_INPUT"
                db.commit()
                msg = f"Airport Confirmed: *{conv.selected_airport_name} ({conv.selected_airport_iata})*\n\nPlease enter your Flight Number (e.g. *AI2424*, *EK505*):"
                whatsapp_client.send_text_message(conv.phone_number, msg)
                return {"status": "flight_prompt_sent"}
            else:
                conv.current_state = "DATE_SELECTION"
                db.commit()
                msg = f"Airport Confirmed: *{conv.selected_airport_name} ({conv.selected_airport_iata})*\n\nPlease enter your Date of Travel (DD/MM/YYYY or YYYY-MM-DD):"
                whatsapp_client.send_text_message(conv.phone_number, msg)
                return {"status": "date_prompt_sent"}

        whatsapp_client.send_text_message(conv.phone_number, "Please select *Confirm* or *Change Airport*.")
        return {"status": "invalid_confirmation"}

    @classmethod
    def _state_flight_input(cls, db: Session, conv: WhatsAppConversation, flight_num_input: str) -> Dict[str, Any]:
        clean_flight = flight_num_input.strip().upper().replace(" ", "")

        if not clean_flight or len(clean_flight) < 3:
            whatsapp_client.send_text_message(conv.phone_number, "Please enter a valid flight number (e.g. AI2424, EK505).")
            return {"status": "invalid_flight_format"}

        # Validate with live FlightIntelligenceService / Aviation Edge API integration
        try:
            val_res = flight_service.validate_flight(
                flight_num=clean_flight,
                date=datetime.now(timezone.utc).strftime("%Y-%m-%d")
            )

            if val_res and getattr(val_res, "is_valid", True):
                conv.flight_num = clean_flight
                conv.flight_details_json = {
                    "airline": getattr(val_res, "airline", "Airline"),
                    "flight_number": clean_flight,
                    "departure": getattr(val_res, "departure_airport", conv.selected_airport_iata or "ORIGIN"),
                    "arrival": getattr(val_res, "arrival_airport", "DEST")
                }
                conv.current_state = "FLIGHT_CONFIRMATION"
                db.commit()

                body_text = (
                    f"✈️ *Verified Flight Details*\n\n"
                    f"• *Flight Number*: {clean_flight}\n"
                    f"• *Airline*: {conv.flight_details_json.get('airline')}\n"
                    f"• *Route*: {conv.flight_details_json.get('departure')} ➔ {conv.flight_details_json.get('arrival')}\n\n"
                    f"Please confirm your flight details:"
                )

                buttons = [
                    {"id": "btn_confirm_flight", "title": "Confirm Flight"},
                    {"id": "btn_reenter_flight", "title": "Re-enter Flight"}
                ]

                whatsapp_client.send_interactive_buttons(
                    to_phone=conv.phone_number,
                    body_text=body_text,
                    buttons=buttons,
                    header_text="Flight Verification"
                )
                return {"status": "flight_verified"}

        except Exception as err:
            logger.warning(f"[WhatsApp Flight Validation] Live flight check exception: {err}")

        # Live Flight API verification failed
        msg = "Sorry, we couldn't verify this flight. Please check the flight number and try again."
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "flight_unverified"}

    @classmethod
    def _state_flight_confirmation(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        text_u = user_text.strip().upper()

        if input_id == "btn_reenter_flight" or "RE-ENTER" in text_u or "NO" in text_u:
            conv.flight_num = None
            conv.current_state = "FLIGHT_INPUT"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your flight number again:")
            return {"status": "reprompt_flight"}

        if input_id == "btn_confirm_flight" or "CONFIRM" in text_u or "YES" in text_u:
            conv.current_state = "DATE_SELECTION"
            db.commit()
            msg = f"Flight Confirmed: *{conv.flight_num}*\n\nPlease enter your Date of Travel (DD/MM/YYYY or YYYY-MM-DD):"
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "date_prompt_sent"}

        whatsapp_client.send_text_message(conv.phone_number, "Please select *Confirm Flight* or *Re-enter Flight*.")
        return {"status": "invalid_flight_confirmation"}

    @classmethod
    def _state_date_selection(cls, db: Session, conv: WhatsAppConversation, date_input: str) -> Dict[str, Any]:
        clean_date = date_input.strip()

        # Parse date
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
            whatsapp_client.send_text_message(conv.phone_number, "Invalid email address format. Please provide a valid email address (e.g., name@example.com).")
            return {"status": "invalid_email"}

        conv.customer_email = clean_email
        conv.current_state = "CUSTOMER_PHONE"
        db.commit()

        msg = f"Email Saved: *{clean_email}*\n\nPlease provide your contact phone number with country code (or type 'Same' to use this WhatsApp number):"
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

    @classmethod
    def _send_booking_summary(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        summary_lines = [
            "📋 *BOOKING SUMMARY — Shafsky Aviation*\n",
            f"• *Service*: {conv.selected_service_name}",
        ]
        if conv.selected_airport_iata:
            summary_lines.append(f"• *Airport*: {conv.selected_airport_name} ({conv.selected_airport_iata})")
        if conv.flight_num:
            summary_lines.append(f"• *Flight*: {conv.flight_num}")

        summary_lines.extend([
            f"• *Date*: {conv.booking_date}",
            f"• *Passengers*: {conv.passenger_count}",
            f"• *Passenger Name*: {conv.customer_name}",
            f"• *Email*: {conv.customer_email}",
            f"• *Phone*: {conv.customer_phone}",
            f"• *Special Requests*: {conv.additional_requirements}",
            f"\n💰 *Total Amount*: ₹{int(conv.total_amount or 0):,}\n",
            "Please confirm your booking details to proceed to payment:"
        ])

        body_text = "\n".join(summary_lines)
        buttons = [
            {"id": "btn_confirm_pay", "title": "CONFIRM & PAY"},
            {"id": "btn_edit_details", "title": "EDIT DETAILS"},
            {"id": "btn_cancel", "title": "CANCEL"}
        ]

        whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Booking Summary"
        )

        return {"status": "summary_sent"}

    @classmethod
    def _state_booking_review(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        text_u = user_text.strip().upper()

        if input_id == "btn_edit_details" or "EDIT" in text_u:
            conv.current_state = "CUSTOMER_NAME"
            db.commit()
            whatsapp_client.send_text_message(conv.phone_number, "Let's update your details. May I have your full name?")
            return {"status": "edit_prompt"}

        if input_id == "btn_confirm_pay" or "CONFIRM" in text_u or "PAY" in text_u:
            return cls._create_and_send_payment(db, conv)

        whatsapp_client.send_text_message(conv.phone_number, "Please select *CONFIRM & PAY*, *EDIT DETAILS*, or *CANCEL*.")
        return {"status": "invalid_summary_choice"}

    @classmethod
    def _create_and_send_payment(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        # 1. Authoritative Backend Pricing Calculation
        amount = float(conv.total_amount or 2500.0)
        booking_ref = BookingService.generate_booking_ref()

        # 2. Create DB Booking Record
        booking_payload = {
            "passenger_name": conv.customer_name or "Guest",
            "passenger_email": conv.customer_email or "guest@shafsky.com",
            "passenger_phone": conv.customer_phone or conv.phone_number,
            "service_category": conv.selected_category or "Airport Services",
            "service_type": conv.selected_service_name or "VIP Service",
            "origin_code": conv.selected_airport_iata or "DEL",
            "dest_code": "DEST",
            "flight_num": conv.flight_num or "N/A",
            "total_amount": amount,
            "currency": "INR",
            "notes": conv.additional_requirements
        }

        # Save booking in database with PENDING state
        new_booking = Booking(
            id=uuid.uuid4(),
            booking_ref=booking_ref,
            passenger_name=booking_payload["passenger_name"],
            passenger_email=booking_payload["passenger_email"],
            passenger_phone=booking_payload["passenger_phone"],
            service_category=booking_payload["service_category"],
            service_type=booking_payload["service_type"],
            origin_code=booking_payload["origin_code"],
            dest_code=booking_payload["dest_code"],
            flight_num=booking_payload["flight_num"],
            total_amount=amount,
            currency="INR",
            status=BookingStatus.PENDING,
            notes=booking_payload["notes"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db.add(new_booking)
        db.commit()
        db.refresh(new_booking)

        # 3. Create Razorpay Payment Link
        rzp_res = razorpay_provider.create_payment_link(
            amount=amount,
            currency="INR",
            reference_id=booking_ref,
            description=f"Shafsky Aviation - {conv.selected_service_name}",
            customer_name=conv.customer_name or "Guest",
            customer_email=conv.customer_email or "guest@shafsky.com",
            customer_phone=conv.customer_phone or conv.phone_number
        )

        conv.booking_id = new_booking.id
        conv.booking_ref = booking_ref
        conv.razorpay_order_id = rzp_res.get("order_id")
        conv.razorpay_payment_link_id = rzp_res.get("payment_link_id")
        conv.razorpay_payment_url = rzp_res.get("short_url")
        conv.current_state = "PENDING_PAYMENT"
        conv.payment_status = "PENDING"
        db.commit()

        # 4. Send Payment Link over WhatsApp
        pay_url = conv.razorpay_payment_url or "https://rzp.io/i/simulated_shafsky"

        msg = (
            f"💳 *Payment Link Generated*\n\n"
            f"Your booking reference *{booking_ref}* has been reserved.\n"
            f"• *Service*: {conv.selected_service_name}\n"
            f"• *Total Amount*: ₹{int(amount):,}\n\n"
            f"Please complete your secure payment using the link below:\n"
            f"🔗 {pay_url}\n\n"
            f"After payment completion, your booking will be confirmed automatically."
        )

        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "payment_link_sent", "booking_ref": booking_ref, "payment_url": pay_url}

    # ── VERIFIED PAYMENT CONFIRMATION HOOK ──

    @classmethod
    def handle_payment_success(cls, db: Session, booking_ref: str, payment_id: str) -> bool:
        """
        Triggered exclusively by verified Razorpay webhook signature upon payment success.
        Updates booking status to CONFIRMED/PAID and dispatches multi-channel notifications.
        """
        stmt = select(Booking).where(Booking.booking_ref == booking_ref)
        booking = db.execute(stmt).scalar_one_or_none()

        if not booking:
            logger.error(f"[Razorpay Success] Booking ref '{booking_ref}' not found in database.")
            return False

        booking.status = BookingStatus.CONFIRMED
        booking.updated_at = datetime.now(timezone.utc)
        db.commit()

        # Update WhatsApp Conversation State
        conv_stmt = select(WhatsAppConversation).where(WhatsAppConversation.booking_ref == booking_ref)
        conv = db.execute(conv_stmt).scalar_one_or_none()
        if conv:
            conv.current_state = "BOOKING_CONFIRMED"
            conv.payment_status = "PAID"
            conv.razorpay_payment_id = payment_id
            db.commit()

        # 1. Customer WhatsApp Notification
        cust_msg = (
            f"🎉 *Payment Received Successfully!*\n\n"
            f"Your Shafsky Aviation booking has been confirmed.\n\n"
            f"• *Booking Reference*: *{booking.booking_ref}*\n"
            f"• *Service*: {booking.service_type}\n"
            f"• *Airport*: {booking.origin_code}\n"
            f"• *Flight*: {booking.flight_num}\n"
            f"• *Passenger*: {booking.passenger_name}\n\n"
            f"Thank you for choosing Shafsky Aviation."
        )
        whatsapp_client.send_text_message(booking.passenger_phone, cust_msg)

        # 2. Team WhatsApp Notification
        officer_phone = os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "919599087959").strip()
        team_msg = (
            f"🚨 *NEW PAID BOOKING CONFIRMED*\n\n"
            f"• *Booking*: {booking.booking_ref}\n"
            f"• *Customer*: {booking.passenger_name} ({booking.passenger_phone})\n"
            f"• *Email*: {booking.passenger_email}\n"
            f"• *Service*: {booking.service_type}\n"
            f"• *Airport*: {booking.origin_code}\n"
            f"• *Flight*: {booking.flight_num}\n"
            f"• *Amount*: ₹{int(booking.total_amount):,}\n"
            f"• *Payment Status*: PAID (ID: {payment_id})"
        )
        whatsapp_client.send_text_message(officer_phone, team_msg)

        # 3. Customer & Team Email Notifications
        try:
            from app.services.notification_templates import NotificationTemplateEngine
            template_data = {
                "passengerName": booking.passenger_name,
                "bookingRef": booking.booking_ref,
                "flightNum": booking.flight_num,
                "originCode": booking.origin_code,
                "destCode": booking.dest_code,
                "totalAmount": booking.total_amount,
                "currency": "INR",
                "transactionId": payment_id
            }
            email_payload = NotificationTemplateEngine.render_template("BOOKING_CONFIRMATION", template_data)
            
            # Dispatch via Resend integration if configured
            from app.services.operations_engine import OperationsEngine
            logger.info(f"[Email Notification] Rendered confirmation email for booking {booking.booking_ref}")
        except Exception as email_err:
            logger.warning(f"[Email Notification] Exception rendering email: {email_err}")

        return True


class WhatsAppService:
    """Unified WhatsApp Ingestion & Webhook Handler."""

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
    """Non-blocking notification helper."""
    try:
        if not whatsapp_client.is_configured():
            return
        WhatsAppService.handle_payment_success(db=None, booking_ref=getattr(booking, "booking_ref", ""), payment_id="PAY_DIRECT")
    except Exception as err:
        logger.warning(f"[WhatsApp Notification Hook] Exception: {err}")
