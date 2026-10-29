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

from decimal import Decimal
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
from app.integrations.whatsapp.handlers.airport_flow_ext import AirportServiceMenuMixin

logger = logging.getLogger(__name__)

# Common English spellings that must resolve to the seeded IATA city names.
_AIRPORT_QUERY_ALIASES = {
    "BANGALORE": "BLR",
    "BENGALURU": "BLR",
    "BOMBAY": "BOM",
    "CALCUTTA": "CCU",
    "MADRAS": "MAA",
    "TRIVANDRUM": "TRV",
    "COCHIN": "COK",
    "NEW DELHI": "DEL",
    "DELHI": "DEL",
}


OFFICIAL_CATEGORIES = [
    {"id": "cat_airport", "name": "Airport Services", "db_categories": ["Airport Assistance", "Airport Services"]},
    {"id": "cat_travel", "name": "Travel Services", "db_categories": ["Travel Support", "Travel Services"]},
    {"id": "cat_charter", "name": "Private Charter", "db_categories": ["Private Charter"]},
    {"id": "cat_hotel_transport", "name": "Hotel & Transportation", "db_categories": ["Ground Transport", "Transportation Services", "Travel Support", "Hotel Services"]},
]


class AirportFlowMixin(AirportServiceMenuMixin, BaseFlowMixin):
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
            {"id": "cat_airport", "title": "Airport Services", "description": "Meet & Greet at supported airports"},
            {"id": "cat_travel", "title": "Travel Services", "description": "Visa assistance and flight ticketing"},
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
            elif "2" in norm or "travel" in norm or "visa" in norm or "ticket" in norm:
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
            return cls._send_travel_services_menu(db, conv)

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

        airport = None
        try:
            alias_iata = _AIRPORT_QUERY_ALIASES.get(query_clean.upper())
            if alias_iata:
                airport = db.execute(
                    select(SupportedAirport).where(
                        SupportedAirport.is_active == True,
                        SupportedAirport.is_supported == True,
                        SupportedAirport.iata_code == alias_iata,
                    )
                ).scalars().first()
            if not airport:
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
