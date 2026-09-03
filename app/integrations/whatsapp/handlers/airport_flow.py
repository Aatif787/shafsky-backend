"""
Airport Flow Mixin for WhatsApp Booking State Machine.
Handles:
- Main category selection (Airport, Travel, Charter, Hotel/Transport)
- Journey Type selection (Arrival, Departure, Transit)
- Travel Type selection (Domestic, International)
- Transit combinations (Domestic-Domestic, Domestic-Intl, Intl-Domestic, Intl-Intl)
- Supported Airport resolution & search
- Terminal selection
- Package retrieval & dynamic inheritance presentation
- Service selection & progression to Flight/Charter/Date input
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select, or_

from app.models.whatsapp_models import WhatsAppConversation
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.integrations.whatsapp.client import whatsapp_client
from app.integrations.whatsapp import copy as wa_copy
from app.integrations.whatsapp import delivery as wa_delivery
from app.services.service_config_service import ServiceConfigService, DEFAULT_SERVICE_CATALOG
from app.integrations.whatsapp.handlers.base import BaseFlowMixin

logger = logging.getLogger(__name__)

OFFICIAL_CATEGORIES = [
    {"id": "cat_airport", "name": "Airport Services", "db_categories": ["Airport Assistance", "Airport Services"]},
    {"id": "cat_travel", "name": "Travel Services", "db_categories": ["Travel Support", "Travel Services"]},
    {"id": "cat_charter", "name": "Private Charter", "db_categories": ["Private Charter"]},
    {"id": "cat_hotel_transport", "name": "Hotel & Transportation", "db_categories": ["Ground Transport", "Transportation Services", "Travel Support", "Hotel Services"]},
]


class AirportFlowMixin(BaseFlowMixin):
    """Mixin for Airport Services, Categories, Terminals, and Service Package selection."""

    @classmethod
    def _state_start(cls, db: Session, conv: WhatsAppConversation, user_input: str) -> Dict[str, Any]:
        """
        Idle / post-cancel / post-booking entry.

        The welcome menu is only sent when the customer explicitly restarts
        (Hi / Hello / Menu / …). Any other text gets a short prompt instead of
        dumping the full service list unsolicited.
        """
        # Keep in sync with RESTART_COMMANDS in service.py (avoid circular import).
        restart_triggers = {
            "hi", "hello", "hey", "start", "menu", "restart", "main menu", "0",
            "reset", "start over",
        }
        text_lower = (user_input or "").strip().lower()
        if text_lower not in restart_triggers:
            wa_delivery.send_text(
                conv.phone_number,
                wa_copy.TYPE_HI_TO_START,
                client=whatsapp_client,
            )
            return {"status": "awaiting_hi", "success": True}

        conv = cls._reset_conversation_fields(conv, db)
        cls._transition_state(db, conv, "CATEGORY_SELECTION")
        return cls._send_category_menu(db, conv)

    @classmethod
    def _send_category_menu(cls, db: Session, conv: WhatsAppConversation, prefix_notice: str = "") -> Dict[str, Any]:
        """Sends the exact 4-option main menu."""
        cls._transition_state(db, conv, "CATEGORY_SELECTION")

        body_text = wa_copy.category_menu_body(prefix_notice)

        rows = [
            {"id": "cat_airport", "title": "Airport Services", "description": "Meet & Assist at supported airports"},
            {"id": "cat_travel", "title": "Travel Services", "description": "Visa, insurance, travel support"},
            {"id": "cat_charter", "title": "Private Charter", "description": "Private jet and helicopter"},
            {"id": "cat_hotel_transport", "title": "Hotel & Transportation", "description": "Hotels and airport transfers"},
        ]

        sections = [{"title": "Shafsky Service Menu", "rows": rows}]

        wa_delivery.send_list(
            phone=conv.phone_number,
            body_text=body_text,
            button_title="View Options",
            sections=sections,
            header_text=wa_copy.BRAND,
            fallback_text=wa_copy.category_menu_fallback(prefix_notice),
            client=whatsapp_client,
        )

        return {"status": "category_menu_sent", "success": True}

    @classmethod
    def _state_category_selection(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Handles category selection from the 4 customer options."""
        matched_category: Optional[str] = None

        if input_id:
            for cat in OFFICIAL_CATEGORIES:
                if cat["id"] == input_id:
                    matched_category = str(cat["name"])
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
                "Please select a valid option (1-4):\n\n1️⃣ Airport Services\n2️⃣ Travel Services\n3️⃣ Private Charter\n4️⃣ Hotel & Transportation"
            )
            return {"status": "invalid_category", "success": False}

        conv.selected_category = matched_category
        db.commit()

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

        return {"status": "category_selected", "success": True}

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
        wa_delivery.send_buttons(
            phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Journey Type",
            fallback_text=(
                "*Shafsky Aviation Services*\n\n"
                "Please select your journey type by replying with a number:\n\n"
                "1️⃣ Arrival\n"
                "2️⃣ Departure\n"
                "3️⃣ Transit"
            ),
            client=whatsapp_client,
        )

        return {"status": "journey_type_prompt_sent", "success": True}

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
                "✨ *Shafsky Aviation Services*\n\nPlease select a valid journey type:\n\n1️⃣ Arrival\n2️⃣ Departure\n3️⃣ Transit"
            )
            return {"status": "invalid_journey_type", "success": False}

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
        wa_delivery.send_buttons(
            phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text=f"{journey_name} Travel Type",
            fallback_text=(
                f"*Shafsky Aviation Services*\n\n"
                f"*{journey_name}* selected.\n\n"
                "Please select your travel type by replying with a number:\n\n"
                "1️⃣ Domestic\n"
                "2️⃣ International"
            ),
            client=whatsapp_client,
        )

        return {"status": "travel_type_prompt_sent", "success": True}

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
                "✨ *Shafsky Aviation Services*\n\nPlease select a valid travel type:\n\n1️⃣ Domestic\n2️⃣ International"
            )
            return {"status": "invalid_travel_type", "success": False}

        jt = (conv.flight_details_json or {}).get("journey_type", "DEPARTURE") if isinstance(conv.flight_details_json, dict) else "DEPARTURE"
        conv.flight_details_json = {
            "journey_type": jt,
            "travel_type": tt,
            "flight_type": tt
        }
        db.commit()

        cls._transition_state(db, conv, "AIRPORT_SELECTION")

        msg = (
            f"✨ *Shafsky Aviation Services*\n\n"
            f"*{tt.title()}* selected.\n\n"
            "Please enter your Airport Name, City, or IATA Code (e.g., *Delhi*, *DEL*, *Lucknow*):"
        )
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "airport_prompt_sent", "success": True}

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

        wa_delivery.send_list(
            phone=conv.phone_number,
            body_text=body_text,
            button_title="Select Transit",
            sections=sections,
            header_text="Transit Options",
            fallback_text=(
                "*Shafsky Aviation Services*\n\n"
                "Please select your transit type by replying with a number:\n\n"
                "1️⃣ Domestic → Domestic\n"
                "2️⃣ Domestic → International\n"
                "3️⃣ International → Domestic\n"
                "4️⃣ International → International"
            ),
            client=whatsapp_client,
        )

        return {"status": "transit_type_prompt_sent", "success": True}

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
                "✨ *Shafsky Aviation Services*\n\nPlease select a valid transit type (1-4):\n\n1️⃣ Domestic → Domestic\n2️⃣ Domestic → International\n3️⃣ International → Domestic\n4️⃣ International → International"
            )
            return {"status": "invalid_transit_type", "success": False}

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
            f"✨ *Shafsky Aviation Services*\n\n"
            f"Transit Type: *{display_label}*\n\n"
            "Please enter your Airport Name, City, or IATA Code (e.g., *Delhi*, *DEL*, *Mumbai*):"
        )
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "airport_prompt_sent", "success": True}

    @classmethod
    def _state_airport_selection(cls, db: Session, conv: WhatsAppConversation, query: str) -> Dict[str, Any]:
        """
        Database-driven airport resolution:
        Searches ONLY Shafsky-supported airports configured in the database (`supported_airports`).
        Rejects non-configured or worldwide airports.
        """
        query_clean = query.strip()
        logger.info(f"[WhatsApp Routing] state={conv.current_state} input={query_clean}")
        if not query_clean:
            whatsapp_client.send_text_message(conv.phone_number, "✨ *Shafsky Aviation Services*\n\nPlease enter an Airport Name, City, or IATA Code (e.g. Delhi, DEL, Lucknow).")
            return {"status": "empty_airport_query", "success": False}

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
            msg = "✨ *Shafsky Aviation Services*\n\nWe regret to inform you that Airport Services are currently unavailable at this airport. Please enter another airport, or type *BACK* to return to the previous menu."
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "unsupported_airport", "success": False}

        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        jt = metadata.get("journey_type", "DEPARTURE")
        tt = metadata.get("travel_type", "DOMESTIC")
        tt, route_err = cls._authoritative_catalog_travel_type(db, conv, jt, tt)
        if route_err:
            return cls._send_route_classification_error(conv, route_err)
        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else metadata

        logger.info(f"[WhatsApp Airport] resolved={airport.iata_code}")
        logger.info(f"[WhatsApp Airport] journey_type={jt} travel_type={tt}")

        conv.selected_airport_name = airport.airport_name
        conv.selected_airport_iata = airport.iata_code
        conv.selected_airport_city = airport.city
        conv.selected_airport_country = airport.country

        applicable_terminals = cls._get_applicable_terminals(db, airport, jt, tt)
        if len(applicable_terminals) > 1:
            cls._transition_state(db, conv, "TERMINAL_SELECTION")
            logger.info(f"[WhatsApp Airport] multiple terminals detected={applicable_terminals}, next_state=TERMINAL_SELECTION")
            return cls._prompt_terminal_selection(conv, airport, applicable_terminals)

        if len(applicable_terminals) == 1:
            new_meta = dict(metadata)
            new_meta["terminal"] = applicable_terminals[0]
            conv.flight_details_json = new_meta
            flag_modified(conv, "flight_details_json")
            db.commit()
        else:
            if "terminal" in metadata:
                new_meta = dict(metadata)
                new_meta.pop("terminal", None)
                conv.flight_details_json = new_meta
                flag_modified(conv, "flight_details_json")
                db.commit()

        cls._transition_state(db, conv, "SERVICE_SELECTION")
        logger.info(f"[WhatsApp Airport] next_state={conv.current_state}")
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
            metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
            jt = metadata.get("journey_type", "DEPARTURE")
            tt = metadata.get("travel_type", "DOMESTIC")
            tt, route_err = cls._authoritative_catalog_travel_type(db, conv, jt, tt)
            if route_err:
                return cls._send_route_classification_error(conv, route_err)
            metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else metadata
            airport = db.execute(select(SupportedAirport).where(SupportedAirport.iata_code == conv.selected_airport_iata)).scalar_one_or_none()
            if airport:
                applicable_terminals = cls._get_applicable_terminals(db, airport, jt, tt)
                if len(applicable_terminals) > 1:
                    cls._transition_state(db, conv, "TERMINAL_SELECTION")
                    return cls._prompt_terminal_selection(conv, airport, applicable_terminals)
                if len(applicable_terminals) == 1:
                    new_meta = dict(metadata)
                    new_meta["terminal"] = applicable_terminals[0]
                    conv.flight_details_json = new_meta
                    flag_modified(conv, "flight_details_json")
                    db.commit()
                else:
                    if "terminal" in metadata:
                        new_meta = dict(metadata)
                        new_meta.pop("terminal", None)
                        conv.flight_details_json = new_meta
                        flag_modified(conv, "flight_details_json")
                        db.commit()

            cls._transition_state(db, conv, "SERVICE_SELECTION")
            return cls._send_airport_services_menu(db, conv)

        whatsapp_client.send_text_message(conv.phone_number, "Please select *Confirm* or *Change Airport*.")
        return {"status": "invalid_airport_confirmation"}

    @classmethod
    def _get_applicable_terminals(cls, db: Session, airport: SupportedAirport, journey_type: str, travel_type: str) -> List[str]:
        """
        Retrieves the list of distinct active terminals configured for this airport + journey + travel type.
        """
        jt = (journey_type or "DEPARTURE").upper()
        tt = (travel_type or "DOMESTIC").upper()

        # Rule: Delhi (DEL) International flights/services operate exclusively from Terminal 3 (no international in T1/T2)
        if airport and airport.iata_code.upper() == "DEL" and ("INTERNATIONAL" in tt or tt == "INTL"):
            return ["Terminal 3"]

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

        raw_packages = cls._get_authoritative_airport_packages(db, airport.id, jt, flight_types, terminal=None)
        terminals = sorted(list(set([
            aps.terminal.strip() for aps, svc in raw_packages 
            if aps.terminal and aps.terminal.strip()
        ])))
        return terminals

    @classmethod
    def _prompt_terminal_selection(cls, conv: WhatsAppConversation, airport: SupportedAirport, terminals: List[str]) -> Dict[str, Any]:
        """Prompts customer to select their terminal."""
        body_text = (
            f"✨ *{airport.airport_name}*\n\n"
            "Please select your terminal:"
        )

        buttons = []
        for i, term in enumerate(terminals[:3], 1):
            btn_id = f"btn_term_{i}"
            buttons.append({"id": btn_id, "title": term[:20]})

        res = whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Select Terminal"
        )

        if not res.get("success"):
            fallback = body_text + "\n\n"
            for i, term in enumerate(terminals, 1):
                fallback += f"{i}️⃣ {term}\n"
            fallback += "\nPlease reply with the number of your choice (e.g. 1)."
            whatsapp_client.send_text_message(conv.phone_number, fallback)

        return {"status": "terminal_selection_prompt_sent", "success": True}

    @classmethod
    def _state_terminal_selection(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Processes terminal selection and transitions to SERVICE_SELECTION."""
        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        iata = conv.selected_airport_iata
        airport = db.execute(select(SupportedAirport).where(SupportedAirport.iata_code == iata)).scalar_one_or_none()
        if not airport:
            cls._transition_state(db, conv, "AIRPORT_SELECTION")
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your Airport Name, City, or IATA Code:")
            return {"status": "reprompt_airport", "success": False}

        jt_term = metadata.get("journey_type", "DEPARTURE")
        tt_term = metadata.get("travel_type", "DOMESTIC")
        tt_term, route_err = cls._authoritative_catalog_travel_type(db, conv, jt_term, tt_term)
        if route_err:
            return cls._send_route_classification_error(conv, route_err)
        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else metadata
        terminals = cls._get_applicable_terminals(db, airport, jt_term, tt_term)

        norm = (user_text or "").strip().upper()
        input_norm = (input_id or "").strip().lower()

        selected_term = None

        if input_norm.startswith("btn_term_"):
            try:
                idx = int(input_norm.replace("btn_term_", "")) - 1
                if 0 <= idx < len(terminals):
                    selected_term = terminals[idx]
            except Exception:
                pass

        if not selected_term:
            if norm.isdigit():
                idx = int(norm) - 1
                if 0 <= idx < len(terminals):
                    selected_term = terminals[idx]

        if not selected_term:
            for term in terminals:
                t_up = term.upper()
                if norm == t_up or norm in t_up:
                    selected_term = term
                    break
                if "T3" in norm and "TERMINAL 3" in t_up:
                    selected_term = term
                    break
                if ("T1" in norm or "T2" in norm) and ("TERMINAL 1" in t_up or "TERMINAL 2" in t_up):
                    selected_term = term
                    break

        if not selected_term:
            fallback = (
                "✨ *Shafsky Aviation Services*\n\n"
                "Please select a valid terminal:\n\n"
            )
            for i, term in enumerate(terminals, 1):
                fallback += f"{i}️⃣ {term}\n"
            fallback += "\nPlease reply with the number of your choice (e.g. 1)."
            whatsapp_client.send_text_message(conv.phone_number, fallback)
            return {"status": "invalid_terminal", "success": False}

        new_meta = dict(metadata)
        new_meta["terminal"] = selected_term
        conv.flight_details_json = new_meta
        flag_modified(conv, "flight_details_json")
        db.commit()

        cls._transition_state(db, conv, "SERVICE_SELECTION")
        return cls._send_airport_services_menu(db, conv)

    @classmethod
    def _get_authoritative_airport_packages(
        cls,
        db: Session,
        airport_id: Any,
        journey_type: str,
        flight_types: List[str],
        terminal: Optional[str] = None
    ) -> List[Tuple[AirportService, Service]]:
        """Retrieves authoritative packages for the airport + journey + flight types."""
        PACKAGE_SLUGS = {
            "silver", "silver-service", "gold", "gold-service",
            "elite", "elite-service", "elite_plus", "elite-plus",
            "platinum", "platinum-service", "essential", "premium", "vip"
        }
        STANDALONE_SLUGS = {"meet_greet", "fast_track", "lounge", "porter", "buggy", "wheelchair", "transport"}

        stmt = (
            select(AirportService, Service)
            .join(Service, AirportService.service_id == Service.id)
            .where(
                AirportService.airport_id == airport_id,
                AirportService.journey_type == journey_type,
                AirportService.flight_type.in_(flight_types),
                AirportService.is_available.is_(True),
                Service.is_active.is_(True)
            )
            .order_by(AirportService.display_priority, Service.display_order)
        )
        all_rows = db.execute(stmt).all()

        if not all_rows:
            return []

        if terminal:
            term_norm = terminal.strip().upper()
            matching_term = []
            for aps, svc in all_rows:
                if aps.terminal:
                    aps_term = aps.terminal.strip().upper()
                    if (term_norm in aps_term or 
                        ("T3" in term_norm and "TERMINAL 3" in aps_term) or 
                        ("T1" in term_norm and "TERMINAL 1" in aps_term) or 
                        ("T2" in term_norm and "TERMINAL 2" in aps_term)):
                        matching_term.append((aps, svc))
                else:
                    matching_term.append((aps, svc))
            if matching_term:
                all_rows = matching_term

        package_rows = []
        for aps, svc in all_rows:
            slug = (svc.slug or "").lower().strip()
            name = (svc.name or "").strip()
            is_pkg = slug in PACKAGE_SLUGS or ("Service" in name and slug not in STANDALONE_SLUGS)
            if is_pkg:
                package_rows.append((aps, svc))

        if not package_rows and journey_type == "TRANSIT":
            return [(r[0], r[1]) for r in all_rows]

        return package_rows

    @classmethod
    def _authoritative_catalog_travel_type(
        cls,
        db: Session,
        conv: WhatsAppConversation,
        journey_type: str,
        travel_type: str,
    ) -> Tuple[str, Optional[str]]:
        """Derives DOMESTIC/INTERNATIONAL from route if origin/destination known."""
        from app.services.service_airport_rules import (
            derive_flight_type_from_route,
            normalize_iata,
            normalize_journey_type,
        )

        jt = normalize_journey_type(journey_type)
        tt = (travel_type or "DOMESTIC").upper()
        if jt == "TRANSIT":
            return tt, None

        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        origin = normalize_iata(
            metadata.get("origin_iata")
            or metadata.get("departure_iata")
            or metadata.get("origin_code")
        )
        dest = normalize_iata(
            metadata.get("destination_iata")
            or metadata.get("arrival_iata")
            or metadata.get("dest_code")
            or metadata.get("destination_code")
        )
        if not origin or not dest:
            return tt, None

        try:
            derived = derive_flight_type_from_route(db, origin, dest, jt)
        except ValueError as exc:
            return tt, str(exc)

        if derived and derived != tt:
            new_meta = dict(metadata)
            new_meta["travel_type"] = derived
            new_meta["flight_type"] = derived
            conv.flight_details_json = new_meta
            flag_modified(conv, "flight_details_json")
        return derived or tt, None

    @classmethod
    def _send_route_classification_error(cls, conv: WhatsAppConversation, message: str) -> Dict[str, Any]:
        wa_delivery.send_text(
            conv.phone_number,
            f"*Shafsky Aviation Services*\n\n{message}",
            client=whatsapp_client,
        )
        return {"status": "route_classification_error", "success": False, "error": message}

    @classmethod
    def _send_airport_services_menu(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        """Queries and presents configured services/packages with compact dynamic inheritance."""
        iata = conv.selected_airport_iata
        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        jt = metadata.get("journey_type", "DEPARTURE").upper()
        tt = metadata.get("travel_type", "DOMESTIC").upper()
        tt, route_err = cls._authoritative_catalog_travel_type(db, conv, jt, tt)
        if route_err:
            return cls._send_route_classification_error(conv, route_err)
        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else metadata

        airport = db.execute(select(SupportedAirport).where(SupportedAirport.iata_code == iata)).scalar_one_or_none()
        if not airport:
            wa_delivery.send_text(
                conv.phone_number,
                "*Shafsky Aviation Services*\n\nWe regret to inform you that Airport Services are currently unavailable at this airport. Please type *BACK* to select a different airport.",
                client=whatsapp_client,
            )
            return {"status": "unsupported_airport"}

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

        selected_terminal = metadata.get("terminal")
        rows = cls._get_authoritative_airport_packages(
            db, airport.id, jt, flight_types, terminal=selected_terminal
        )
        logger.info(f"[WhatsApp Airport Flow] package_query_count={len(rows)}")

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
            res = wa_delivery.send_buttons(
                phone=conv.phone_number,
                body_text=empty_msg,
                buttons=buttons,
                header_text="No Services Found",
                fallback_text=(
                    f"{empty_msg}\n\n"
                    "1. Change Travel Type\n"
                    "2. Change Airport\n"
                    "3. Main Menu\n\n"
                    "Reply *1*, *2*, or *3*."
                ),
                client=whatsapp_client,
            )
            logger.info("[WhatsApp Airport Flow] response_send=SUCCESS")
            return {"status": "no_services_found", "success": True}

        tt_display = "Domestic" if tt == "DOMESTIC" else ("International" if tt == "INTERNATIONAL" else tt.replace("_", " → ").title())

        available_services = []
        for aps, svc in rows:
            features = aps.features if isinstance(getattr(aps, "features", None), list) else []
            available_services.append({
                "id": str(svc.id),
                "title": svc.name,
                "price": aps.price,
                "description": aps.short_description or svc.description or "Airport service",
                "features": features,
            })

        body_text = wa_copy.airport_packages_body(
            airport_name=airport.airport_name,
            iata=airport.iata_code or iata,
            journey_label=jt.title(),
            travel_label=tt_display,
            services=available_services,
            terminal=selected_terminal,
        )

        inheritance_info = wa_copy.compute_package_inheritance(available_services)
        list_rows = []
        for i, svc in enumerate(available_services[:10]):
            info = inheritance_info[i] if i < len(inheritance_info) else None
            list_rows.append({
                "id": f"svc_id_{svc['id']}",
                "title": wa_copy.list_row_title(svc["title"], svc["price"]),
                "description": wa_copy.list_row_description(svc["price"], svc.get("features"), info)[:72]
            })

        sections = [{"title": "Select Package", "rows": list_rows}]
        fallback = body_text + "\n\nPlease reply with the number of your choice (e.g. 1)."

        wa_delivery.send_list(
            phone=conv.phone_number,
            body_text=body_text,
            button_title="View Packages",
            sections=sections,
            header_text="Service Packages",
            fallback_text=fallback,
            client=whatsapp_client,
        )
        cls._store_wa_menu(db, conv, available_services)

        logger.info("[WhatsApp Airport Flow] response_send=SUCCESS")
        return {"status": "services_menu_sent", "success": True}

    @classmethod
    def _send_service_menu(cls, db: Session, conv: WhatsAppConversation, category_name: str) -> Dict[str, Any]:
        """Displays services for non-airport categories (Travel, Charter)."""
        category_obj = next((c for c in OFFICIAL_CATEGORIES if c["name"] == category_name), None)
        valid_db_cats: List[str] = list(category_obj["db_categories"]) if category_obj and isinstance(category_obj["db_categories"], list) else [category_name]

        try:
            all_services = ServiceConfigService.get_admin_catalog(db)
        except Exception:
            all_services = DEFAULT_SERVICE_CATALOG
        cat_services = [s for s in all_services if str(s.get("category") or "") in valid_db_cats or str(s.get("category") or "") == category_name]
        if not cat_services:
            cat_services = [s for s in DEFAULT_SERVICE_CATALOG if str(s.get("category") or "") in valid_db_cats or str(s.get("category") or "") == category_name]

        if not cat_services:
            wa_delivery.send_text(
                conv.phone_number,
                f"*{category_name}* is available on request.\n\n"
                "Type *BACK* to choose another category, or *HELP* to reach our team.",
                client=whatsapp_client,
            )
            return {"status": "no_services_found", "success": True}

        menu_items = []
        for svc in cat_services[:10]:
            raw_price = svc.get("base_price") or svc.get("price") or 0.0
            price_val = float(raw_price) if isinstance(raw_price, (int, float, str)) else 0.0
            menu_items.append({
                "id": str(svc.get("id")),
                "title": svc.get("title", svc.get("name", "Service")),
                "price": price_val,
                "description": svc.get("description") or "",
                "name": svc.get("title", svc.get("name", "Service")),
                "base_price": price_val,
            })

        body_text = wa_copy.catalog_services_body(category_name, menu_items)
        rows = []
        for svc in menu_items:
            rows.append({
                "id": f"svc_id_{svc.get('id')}",
                "title": wa_copy.list_row_title(str(svc["title"]), svc["price"]),
                "description": f"₹{int(svc['price']):,} - {str(svc.get('description') or '')[:40]}"
            })

        sections = [{"title": category_name[:24], "rows": rows}]
        fallback = body_text + "\n\nPlease reply with the number of your choice (e.g. 1)."

        wa_delivery.send_list(
            phone=conv.phone_number,
            body_text=body_text,
            button_title="Select Option",
            sections=sections,
            header_text=category_name,
            fallback_text=fallback,
            client=whatsapp_client,
        )
        cls._store_wa_menu(db, conv, menu_items)

        return {"status": "service_menu_sent", "success": True}

    @classmethod
    def _state_service_selection(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Handles service package selection from list reply or text reply."""
        category_name = conv.selected_category or "Airport Services"
        text_u = user_text.strip().upper()

        available_services: List[Dict[str, Any]] = []
        selected_svc = None
        stored_menu = cls._get_wa_menu(conv)

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

        if text_u in ["VIEW PACKAGES", "VIEW_PACKAGES", "BTN_VIEW_PACKAGES", "PACKAGES", "VIEW", "VIEW PACKAGE", "SERVICES", "SERVICE PACKAGES", "PACKAGES LIST"] or (input_id and "view_packages" in input_id.lower()):
            if category_name == "Airport Services":
                return cls._send_airport_services_menu(db, conv)
            else:
                return cls._send_service_menu(db, conv, category_name)

        selected_svc = None
        stored_menu = cls._get_wa_menu(conv)

        if category_name == "Airport Services":
            metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
            jt = metadata.get("journey_type", "DEPARTURE").upper()
            tt = metadata.get("travel_type", "DOMESTIC").upper()

            if text_u in ["DOMESTIC", "INTERNATIONAL", "INTL"]:
                new_tt = "DOMESTIC" if "DOMESTIC" in text_u else "INTERNATIONAL"
                new_meta = dict(metadata)
                new_meta["travel_type"] = new_tt
                new_meta["flight_type"] = new_tt
                conv.flight_details_json = new_meta
                flag_modified(conv, "flight_details_json")
                db.commit()
                return cls._send_airport_services_menu(db, conv)

            tt, route_err = cls._authoritative_catalog_travel_type(db, conv, jt, tt)
            if route_err:
                return cls._send_route_classification_error(conv, route_err)
            metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else metadata

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
                selected_terminal = metadata.get("terminal") or getattr(conv, "selected_terminal", None)
                for aps, svc in cls._get_authoritative_airport_packages(
                    db, airport.id, jt, flight_types, terminal=selected_terminal
                ):
                    available_services.append({
                        "id": str(svc.id),
                        "title": svc.name,
                        "price": aps.price,
                        "description": aps.short_description or svc.description or "VIP Service"
                    })

            if not available_services:
                return cls._send_airport_services_menu(db, conv)

            if input_id:
                raw_id = input_id.replace("svc_id_", "")
                selected_svc = next((s for s in available_services if str(s["id"]) == raw_id or str(s.get("id")) == input_id), None)
            if not selected_svc:
                clean = user_text.strip().lower()
                pick_from = stored_menu if stored_menu else available_services
                if clean.isdigit():
                    idx = int(clean) - 1
                    if 0 <= idx < len(pick_from):
                        selected_svc = pick_from[idx]
                else:
                    for s in pick_from:
                        s_title = str(s.get("title") or s.get("name") or "").lower()
                        if s_title in clean or clean in s_title or clean == s_title.replace(" service", ""):
                            selected_svc = s
                            break

        else:
            category_obj = next((c for c in OFFICIAL_CATEGORIES if c["name"] == category_name), None)
            valid_db_cats: List[str] = list(category_obj["db_categories"]) if category_obj and isinstance(category_obj["db_categories"], list) else [category_name]
            try:
                all_services = ServiceConfigService.get_admin_catalog(db)
            except Exception:
                all_services = DEFAULT_SERVICE_CATALOG
            cat_services = [s for s in all_services if str(s.get("category") or "") in valid_db_cats or str(s.get("category") or "") == category_name]

            if input_id:
                raw_id = input_id.replace("svc_id_", "")
                selected_svc = next((s for s in cat_services if str(s.get("id")) == raw_id or str(s.get("id")) == input_id), None)
            if not selected_svc:
                clean = user_text.strip().lower()
                pick_from = stored_menu if stored_menu else cat_services
                if clean.isdigit():
                    idx = int(clean) - 1
                    if 0 <= idx < len(pick_from):
                        selected_svc = pick_from[idx]
                else:
                    for s in pick_from:
                        title = str(s.get("title", s.get("name", ""))).lower()
                        if title in clean or clean in title:
                            selected_svc = s
                            break

        if not selected_svc:
            if category_name == "Airport Services":
                return cls._send_airport_services_menu(db, conv)
            else:
                whatsapp_client.send_text_message(conv.phone_number, "Service not found. Please select a valid service from the list.")
                return {"status": "invalid_service", "success": False}

        svc_id = str(selected_svc.get("id"))
        svc_title = str(selected_svc.get("title", selected_svc.get("name", "VIP Service")))
        raw_price = selected_svc.get("base_price") or selected_svc.get("price") or 2500.0
        price = float(raw_price) if isinstance(raw_price, (int, float, str)) else 2500.0

        conv.selected_service_id = svc_id
        conv.selected_service_name = svc_title
        passengers = max(1, conv.passenger_count or 1)
        conv.total_amount = price * passengers

        meta = dict(conv.flight_details_json) if isinstance(conv.flight_details_json, dict) else {}
        meta["unit_price"] = price
        meta["base_price"] = price
        conv.flight_details_json = meta
        flag_modified(conv, "flight_details_json")
        db.commit()

        if category_name == "Airport Services":
            meta = dict(conv.flight_details_json) if isinstance(conv.flight_details_json, dict) else {}
            pending_fl = meta.get("_pending_verified_flight")
            if (
                isinstance(pending_fl, dict)
                and pending_fl.get("flight_number")
                and pending_fl.get("origin_iata")
                and pending_fl.get("destination_iata")
            ):
                from app.services.service_airport_rules import derive_flight_type_from_route
                curr_tt = meta.get("travel_type", "DOMESTIC")
                jt = meta.get("journey_type", "DEPARTURE")
                try:
                    fl_tt = derive_flight_type_from_route(db, pending_fl.get("origin_iata"), pending_fl.get("destination_iata"), jt)
                except Exception:
                    fl_tt = None

                if fl_tt == curr_tt:
                    cls._commit_accepted_flight(db, conv, pending_fl, verification_status="verified")
                    db.commit()
                    inclusions_block = wa_copy.selected_package_details_text(
                        selected_svc, all_services=stored_menu or available_services or [selected_svc]
                    )
                    detail_part = f"\n\n{inclusions_block}" if inclusions_block else ""
                    msg = (
                        f"Selected: *{svc_title}* ({wa_copy.format_inr(price)}/person)"
                        f"{detail_part}\n\n"
                        f"✈️ Flight: *{conv.flight_num}* (Verified)\n\n"
                        "Please enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):"
                    )
                    whatsapp_client.send_text_message(conv.phone_number, msg)
                    return {"status": "date_prompt_sent", "success": True}

            cls._transition_state(db, conv, "FLIGHT_INPUT")
            inclusions_block = wa_copy.selected_package_details_text(
                selected_svc, all_services=stored_menu or available_services or [selected_svc]
            )
            detail_part = f"\n\n{inclusions_block}" if inclusions_block else ""
            msg = (
                f"Selected: *{svc_title}* ({wa_copy.format_inr(price)}/person)"
                f"{detail_part}\n\n"
                "Please enter your Flight Number (e.g., *EK501*, *AI2424*, *6E224*):"
            )
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "flight_prompt_sent", "success": True}

        elif category_name == "Private Charter":
            cls._transition_state(db, conv, "CHARTER_ORIGIN")
            whatsapp_client.send_text_message(conv.phone_number, f"Selected Charter: *{svc_title}*\n\nPlease enter your departure city / airport:")
            return {"status": "charter_origin_prompt", "success": True}

        else:
            cls._transition_state(db, conv, "DATE_SELECTION")
            whatsapp_client.send_text_message(conv.phone_number, f"Selected Service: *{svc_title}*\n\nPlease enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):")
            return {"status": "date_prompt_sent", "success": True}
