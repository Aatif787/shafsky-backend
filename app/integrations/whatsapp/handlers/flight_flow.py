"""
Flight Flow Mixin for WhatsApp Booking State Machine.
Handles:
- Pure local flight number format validation
- Authoritative AviationStack live flight lookup
- Airport mismatch resolution & override confirmation
- Smart Travel-Type auto-alignment (Domestic <-> International)
- Flight confirmation & transition to Date selection
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select

from app.models.whatsapp_models import WhatsAppConversation
from app.models.journey_models import SupportedAirport
from app.integrations.whatsapp.client import whatsapp_client
from app.integrations.whatsapp import copy as wa_copy
from app.integrations.whatsapp.handlers.base import BaseFlowMixin

logger = logging.getLogger(__name__)


class FlightFlowMixin(BaseFlowMixin):
    """Mixin for AviationStack flight lookup, mismatch resolution, and smart travel-type auto-alignment."""

    @classmethod
    def _validate_flight_number_local(cls, flight_num_input: str) -> Optional[str]:
        """First-pass format filter only. Never treated as verification."""
        from app.flight.aviationstack_service import normalize_flight_number_input
        return normalize_flight_number_input(flight_num_input)

    @classmethod
    def _send_flight_retry_options(cls, conv: WhatsAppConversation, body_text: str) -> None:
        buttons = [
            {"id": "btn_reenter_flight", "title": "Re-enter Flight"},
            {"id": "btn_change_airport", "title": "Change Airport"},
        ]
        res = whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Flight Verification",
        )
        if not res.get("success"):
            whatsapp_client.send_text_message(
                conv.phone_number,
                f"{body_text}\n\nReply *Re-enter* to try another flight number, or *Change Airport*."
            )

    @classmethod
    def _send_flight_mismatch_options(cls, conv: WhatsAppConversation, body_text: str) -> None:
        buttons = [
            {"id": "btn_reenter_flight", "title": "Re-enter Flight"},
            {"id": "btn_confirm_mismatch", "title": "Confirm & Continue"},
            {"id": "btn_change_airport", "title": "Change Airport"},
        ]
        res = whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Airport Mismatch",
        )
        if not res.get("success"):
            whatsapp_client.send_text_message(
                conv.phone_number,
                f"{body_text}\n\n"
                "Reply *Re-enter* to enter another flight, *Confirm* to proceed anyway, "
                "or *Change Airport* to update your selected airport."
            )

    @classmethod
    def _send_flight_type_mismatch_message(
        cls,
        conv: WhatsAppConversation,
        flight: Dict[str, Any],
        flight_number: str,
        selected_type: str,
        actual_type: str,
    ) -> None:
        sel_title = selected_type.title()
        act_title = actual_type.title()

        fn = flight.get("flight_number") or flight_number
        dep = flight.get("departure") or {}
        arr = flight.get("arrival") or {}
        dep_iata = dep.get("iata") or ""
        arr_iata = arr.get("iata") or ""
        dep_name = dep.get("city") or dep.get("airport") or dep_iata
        arr_name = arr.get("city") or arr.get("airport") or arr_iata
        route_str = f"{dep_name} ({dep_iata}) → {arr_name} ({arr_iata})"

        body_text = (
            "⚠️ *Flight Type Mismatch*\n\n"
            f"You selected {sel_title}, but the flight number you entered is an {act_title} flight.\n\n"
            f"✈️ *Flight:* {fn}\n"
            f"📍 *Route:* {route_str}\n"
            f"🌍 *Actual Type:* {act_title}\n\n"
            "Please choose how you'd like to continue:"
        )

        switch_btn_title = "Switch to Intl" if actual_type == "INTERNATIONAL" else "Switch to Domestic"
        buttons = [
            {"id": "btn_switch_travel_type", "title": switch_btn_title},
            {"id": "btn_reenter_flight", "title": "Re-enter Flight"},
            {"id": "btn_change_airport", "title": "Change Airport"},
        ]

        res = whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Flight Type Mismatch",
        )
        if not res.get("success"):
            fallback_text = (
                f"{body_text}\n\n"
                f"1️⃣ Switch to {act_title}\n"
                "2️⃣ Re-enter Flight\n"
                "3️⃣ Change Airport\n\n"
                "Please reply with *1*, *2*, or *3*, or tap an option above."
            )
            whatsapp_client.send_text_message(conv.phone_number, fallback_text)

    @classmethod
    def _prompt_change_airport_from_flight(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        meta = dict(conv.flight_details_json) if isinstance(conv.flight_details_json, dict) else {}
        meta.pop("_pending_mismatch_flight", None)
        meta.pop("_pending_verified_flight", None)
        meta.pop("terminal", None)
        conv.flight_details_json = meta
        conv.flight_num = None
        conv.selected_airport_iata = None
        conv.selected_airport_name = None
        conv.selected_airport_city = None
        conv.selected_airport_country = None
        flag_modified(conv, "flight_details_json")
        cls._transition_state(db, conv, "AIRPORT_SELECTION")
        whatsapp_client.send_text_message(
            conv.phone_number,
            "Please enter your Airport Name, City, or IATA Code (e.g., Delhi, DEL, Lucknow):"
        )
        return {"status": "reprompt_airport", "success": True}

    @classmethod
    def _pending_flight_blob(cls, flight: Dict[str, Any], val_res: Any) -> Dict[str, Any]:
        dep = flight.get("departure") if isinstance(flight.get("departure"), dict) else {}
        arr = flight.get("arrival") if isinstance(flight.get("arrival"), dict) else {}
        airline_raw = flight.get("airline")
        airline_dict = airline_raw if isinstance(airline_raw, dict) else {}
        airline_name = airline_raw if isinstance(airline_raw, str) else airline_dict.get("name")
        airline_code = flight.get("airline_iata") or airline_dict.get("iata")

        fn_str = val_res.get("flight_number") if isinstance(val_res, dict) else str(val_res) if val_res else None
        code_str = val_res.get("airline_code") if isinstance(val_res, dict) else None
        return {
            "flight_number": flight.get("flight_number") or fn_str,
            "airline_code": airline_code or code_str,
            "airline_name": airline_name,
            "origin_iata": dep.get("iata"),
            "destination_iata": arr.get("iata"),
            "origin_city": dep.get("city"),
            "destination_city": arr.get("city"),
            "origin_airport": dep.get("airport"),
            "destination_airport": arr.get("airport"),
            "departure_scheduled": dep.get("scheduled"),
            "arrival_scheduled": arr.get("scheduled"),
            "flight_status": flight.get("status"),
            "verification_provider": "aviationstack",
            "api_flight": flight,
        }

    @classmethod
    def _commit_accepted_flight(
        cls,
        db: Session,
        conv: WhatsAppConversation,
        pending: Dict[str, Any],
        *,
        verification_status: str,
        extra_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        meta = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        new_meta = dict(meta)
        new_meta.update({
            "flight_number": pending.get("flight_number"),
            "airline_code": pending.get("airline_code"),
            "airline_name": pending.get("airline_name"),
            "origin_iata": pending.get("origin_iata"),
            "destination_iata": pending.get("destination_iata"),
            "origin_city": pending.get("origin_city"),
            "destination_city": pending.get("destination_city"),
            "origin_airport": pending.get("origin_airport"),
            "destination_airport": pending.get("destination_airport"),
            "departure_scheduled": pending.get("departure_scheduled"),
            "arrival_scheduled": pending.get("arrival_scheduled"),
            "flight_status": pending.get("flight_status"),
            "verification_status": verification_status,
            "verification_provider": pending.get("verification_provider") or "aviationstack",
            "verification_api_performed": True,
            "status": verification_status,
        })
        if extra_meta:
            new_meta.update(extra_meta)
        new_meta.pop("_pending_verified_flight", None)
        new_meta.pop("_pending_mismatch_flight", None)

        jt = new_meta.get("journey_type", "DEPARTURE")
        if jt != "TRANSIT":
            try:
                from app.services.service_airport_rules import derive_flight_type_from_route
                derived_tt = derive_flight_type_from_route(
                    db, new_meta.get("origin_iata"), new_meta.get("destination_iata"), jt
                )
                if derived_tt:
                    new_meta["travel_type"] = derived_tt
                    new_meta["flight_type"] = derived_tt
            except ValueError:
                pass

        effective_tt = new_meta.get("travel_type") or "DOMESTIC"

        if pending.get("aligned_service_id"):
            conv.selected_service_id = str(pending["aligned_service_id"])
            if pending.get("aligned_service_name"):
                conv.selected_service_name = str(pending["aligned_service_name"])
            if pending.get("aligned_unit_price") is not None:
                unit_p = float(pending["aligned_unit_price"])
                new_meta["unit_price"] = unit_p
                new_meta["base_price"] = unit_p
                passengers = max(1, conv.passenger_count or 1)
                conv.total_amount = unit_p * passengers
        else:
            # Re-align service package to the effective travel type (e.g., International) at selected airport
            if conv.selected_airport_iata:
                airport_obj = db.execute(
                    select(SupportedAirport).where(SupportedAirport.iata_code == conv.selected_airport_iata)
                ).scalar_one_or_none()
                if airport_obj:
                    intl_or_dom = [effective_tt, "ALL"]
                    matching_packages = cls._get_authoritative_airport_packages(
                        db, airport_obj.id, jt, intl_or_dom, terminal=new_meta.get("terminal")
                    )
                    current_svc_name = conv.selected_service_name or new_meta.get("package") or ""
                    current_tier = wa_copy._extract_tier_name(current_svc_name).lower() if current_svc_name else ""
                    aligned_svc = None
                    for aps, s in matching_packages:
                        s_name = (s.name or "").lower()
                        if current_tier and current_tier in s_name:
                            aligned_svc = (aps, s)
                            break
                    if not aligned_svc and matching_packages:
                        aligned_svc = matching_packages[0]

                    if aligned_svc:
                        aligned_aps, aligned_s = aligned_svc
                        unit_p = float(aligned_aps.price)
                        conv.selected_service_id = str(aligned_s.id)
                        conv.selected_service_name = aligned_s.name
                        new_meta["unit_price"] = unit_p
                        new_meta["base_price"] = unit_p
                        new_meta["package"] = aligned_s.name
                        passengers = max(1, conv.passenger_count or 1)
                        conv.total_amount = unit_p * passengers

        # Fallback if unit_price was already set in metadata
        unit_p = new_meta.get("unit_price") or new_meta.get("base_price")
        if unit_p is not None and (conv.total_amount is None or conv.total_amount <= 0):
            passengers = max(1, conv.passenger_count or 1)
            conv.total_amount = float(unit_p) * passengers

        conv.flight_num = pending.get("flight_number")
        conv.flight_details_json = new_meta
        flag_modified(conv, "flight_details_json")
        cls._transition_state(db, conv, "DATE_SELECTION")

    @classmethod
    def _continue_mismatch_override(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        meta = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        pending = meta.get("_pending_mismatch_flight") if isinstance(meta, dict) else None
        if not isinstance(pending, dict) or not pending.get("flight_number"):
            whatsapp_client.send_text_message(
                conv.phone_number,
                "Please re-enter your flight number so we can verify it before continuing.",
            )
            return {"status": "flight_reverify_required", "success": False}

        selected_iata = conv.selected_airport_iata
        selected_name = conv.selected_airport_name
        selected_service = conv.selected_service_name
        cls._commit_accepted_flight(
            db,
            conv,
            pending,
            verification_status="mismatch_customer_confirmed",
            extra_meta={
                "mismatch_override": True,
                "verification_mismatch": True,
                "verification_matched_selected_airport": False,
                "customer_confirmed_despite_mismatch": True,
                "selected_service_airport": selected_iata,
            },
        )
        conv.selected_airport_iata = selected_iata
        conv.selected_airport_name = selected_name
        conv.selected_service_name = selected_service
        db.commit()
        whatsapp_client.send_text_message(
            conv.phone_number,
            f"Flight *{conv.flight_num}* noted. You chose to continue even though the "
            f"verified route does not match *{selected_iata}*.\n\n"
            "Please enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):",
        )
        return {"status": "mismatch_customer_confirmed", "success": True}

    @classmethod
    def _state_flight_input(
        cls,
        db: Session,
        conv: WhatsAppConversation,
        flight_num_input: str,
        input_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Format filter, then authoritative AviationStack lookup."""
        from app.flight import aviationstack_service as as_svc

        text_u = (input_id or flight_num_input or "").strip().upper()
        if text_u in (
            "BTN_CHANGE_AIRPORT",
            "CHANGE AIRPORT",
            "CHANGE_AIRPORT",
        ) or "CHANGE AIRPORT" in text_u:
            return cls._prompt_change_airport_from_flight(db, conv)
        if text_u in (
            "BTN_REENTER_FLIGHT",
            "RE-ENTER FLIGHT",
            "REENTER FLIGHT",
            "RE-ENTER",
        ):
            meta = dict(conv.flight_details_json) if isinstance(conv.flight_details_json, dict) else {}
            meta.pop("_pending_mismatch_flight", None)
            meta.pop("_pending_verified_flight", None)
            conv.flight_details_json = meta
            flag_modified(conv, "flight_details_json")
            db.commit()
            whatsapp_client.send_text_message(
                conv.phone_number,
                "Please enter your Flight Number (e.g., *EK501*, *AI2424*, *6E224*):",
            )
            return {"status": "reprompt_flight", "success": True}
        if text_u in ("BTN_CONFIRM_MISMATCH", "CONFIRM & CONTINUE", "CONFIRM AND CONTINUE"):
            return cls._continue_mismatch_override(db, conv)

        val_res = cls._validate_flight_number_local(flight_num_input)
        if not val_res:
            whatsapp_client.send_text_message(
                conv.phone_number,
                as_svc.customer_failure_message(as_svc.REASON_INVALID_FLIGHT_NUMBER),
            )
            return {"status": "invalid_flight_format", "success": False}

        norm_flight_num = val_res.get("flight_number") if isinstance(val_res, dict) else val_res

        conv.flight_num = None
        old_meta = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        jt = old_meta.get("journey_type", "DEPARTURE")
        tt = old_meta.get("travel_type", "DOMESTIC")
        terminal = old_meta.get("terminal")
        unit_price = old_meta.get("unit_price") or old_meta.get("base_price")
        travel_date = old_meta.get("travel_date") or old_meta.get("service_date") or old_meta.get("date")

        verify = as_svc.verify_flight_for_whatsapp(
            norm_flight_num,
            selected_airport_iata=conv.selected_airport_iata,
            journey_type=jt,
            selected_airport_name=conv.selected_airport_name,
            travel_date=travel_date,
        )

        if not verify.get("success"):
            reason = verify.get("reason") or as_svc.REASON_API_ERROR
            pending_meta = dict(old_meta)
            pending_meta.pop("_pending_verified_flight", None)
            pending_meta["verification_status"] = "not_verified"
            pending_meta["status"] = "flight_unverified"
            pending_meta["verification_api_performed"] = True
            pending_meta["verification_provider"] = "aviationstack"

            if reason == as_svc.REASON_AIRPORT_MISMATCH:
                flight = verify.get("flight") or {}
                if flight.get("departure") and flight.get("arrival") and flight.get("flight_number"):
                    pending_meta["_pending_mismatch_flight"] = cls._pending_flight_blob(flight, norm_flight_num)
                    pending_meta["verification_mismatch"] = True
                else:
                    pending_meta.pop("_pending_mismatch_flight", None)
                conv.flight_details_json = pending_meta
                flag_modified(conv, "flight_details_json")
                db.commit()
                msg = as_svc.build_whatsapp_mismatch_message(
                    flight=verify.get("flight"),
                    flight_number=norm_flight_num,
                    selected_airport_iata=conv.selected_airport_iata or "",
                    selected_airport_name=conv.selected_airport_name,
                )
                if pending_meta.get("_pending_mismatch_flight"):
                    cls._send_flight_mismatch_options(conv, msg)
                else:
                    cls._send_flight_retry_options(conv, msg)
                return {"status": "flight_airport_mismatch", "success": False, "reason": reason}

            pending_meta.pop("_pending_mismatch_flight", None)
            conv.flight_details_json = pending_meta
            flag_modified(conv, "flight_details_json")

            cls._send_flight_retry_options(conv, as_svc.customer_failure_message(reason))
            status_map = {
                as_svc.REASON_FLIGHT_NOT_FOUND: "flight_not_found",
                as_svc.REASON_MALFORMED_RESPONSE: "flight_incomplete",
                as_svc.REASON_AMBIGUOUS: "flight_ambiguous",
                as_svc.REASON_TIMEOUT: "flight_verify_timeout",
                as_svc.REASON_RATE_LIMITED: "flight_verify_rate_limited",
                as_svc.REASON_NOT_CONFIGURED: "flight_verify_not_configured",
            }
            return {
                "status": status_map.get(reason, "flight_verify_failed"),
                "success": False,
                "reason": reason,
            }

        flight = verify.get("flight") or {}
        dep = flight.get("departure") or {}
        arr = flight.get("arrival") or {}
        if not dep.get("iata") or not arr.get("iata") or not flight.get("flight_number"):
            conv.flight_num = None
            cls._send_flight_retry_options(
                conv, as_svc.customer_failure_message(as_svc.REASON_MALFORMED_RESPONSE)
            )
            return {"status": "flight_incomplete", "success": False, "reason": as_svc.REASON_MALFORMED_RESPONSE}

        pending = cls._pending_flight_blob(flight, val_res)
        dep_iata = dep.get("iata")
        arr_iata = arr.get("iata")

        # Strict consistency validation: compare user selected type vs actual verified flight type
        actual_flight_type = None
        if jt != "TRANSIT":
            try:
                from app.services.service_airport_rules import derive_flight_type_from_route
                actual_flight_type = derive_flight_type_from_route(db, dep_iata, arr_iata, jt)
            except Exception as exc:
                logger.error(f"[Flight Verification] Failed to derive flight type from route: {exc}")
                actual_flight_type = None

        selected_type = (tt or "DOMESTIC").upper()
        if (
            jt != "TRANSIT"
            and actual_flight_type in ("DOMESTIC", "INTERNATIONAL")
            and selected_type in ("DOMESTIC", "INTERNATIONAL")
            and actual_flight_type != selected_type
        ):
            # STOP FLOW IMMEDIATELY on flight type mismatch
            logger.warning(
                f"[Flight Type Mismatch] User selected {selected_type}, but flight {norm_flight_num} "
                f"route {dep_iata}->{arr_iata} is {actual_flight_type}. Halting booking flow."
            )
            pending["actual_flight_type"] = actual_flight_type
            pending["selected_flight_type"] = selected_type

            new_meta = dict(old_meta)
            new_meta.pop("_pending_verified_flight", None)
            new_meta["_pending_mismatch_flight"] = pending
            new_meta["verification_status"] = "flight_type_mismatch"
            new_meta["status"] = "flight_type_mismatch"
            conv.flight_num = None
            conv.flight_details_json = new_meta
            flag_modified(conv, "flight_details_json")
            cls._transition_state(db, conv, "FLIGHT_TYPE_MISMATCH")
            db.commit()

            cls._send_flight_type_mismatch_message(
                conv, flight, norm_flight_num, selected_type, actual_flight_type
            )
            return {
                "status": "flight_type_mismatch",
                "success": False,
                "selected_type": selected_type,
                "actual_type": actual_flight_type,
            }

        # Route matches selected travel type -> proceed with normal verification
        new_meta = dict(old_meta)
        for stale_key in (
            "origin_iata", "destination_iata", "origin_city", "destination_city",
            "origin_airport", "destination_airport", "flight_number", "airline_name",
            "airline_code", "departure_scheduled", "arrival_scheduled",
        ):
            new_meta.pop(stale_key, None)
        new_meta["journey_type"] = jt
        new_meta["travel_type"] = selected_type
        new_meta["flight_type"] = selected_type
        new_meta["terminal"] = terminal
        new_meta["verification_status"] = "pending_confirmation"
        new_meta["status"] = "awaiting_flight_confirmation"
        new_meta["_pending_verified_flight"] = pending
        new_meta.pop("_pending_mismatch_flight", None)
        if unit_price is not None:
            new_meta["unit_price"] = unit_price
            new_meta["base_price"] = unit_price
        conv.flight_details_json = new_meta
        flag_modified(conv, "flight_details_json")
        cls._transition_state(db, conv, "FLIGHT_CONFIRMATION")
        db.commit()

        verified_body = as_svc.build_whatsapp_verified_message(
            flight=flight,
            selected_airport_iata=conv.selected_airport_iata or "",
            selected_airport_name=conv.selected_airport_name,
            journey_type=jt,
        )
        body_text = f"{verified_body}\n\nPlease confirm your flight details to proceed."
        buttons = [
            {"id": "btn_confirm_flight", "title": "Confirm Flight"},
            {"id": "btn_reenter_flight", "title": "Re-enter Flight"},
            {"id": "btn_change_airport", "title": "Change Airport"},
        ]
        res = whatsapp_client.send_interactive_buttons(
            to_phone=conv.phone_number,
            body_text=body_text,
            buttons=buttons,
            header_text="Flight Verified",
        )
        if not res.get("success"):
            fallback_text = (
                f"{verified_body}\n\n"
                "Reply *Confirm* to proceed, or *Re-enter* to change flight number."
            )
            whatsapp_client.send_text_message(conv.phone_number, fallback_text)

        return {"status": "flight_verified", "success": True}

    @classmethod
    def _state_flight_confirmation(cls, db: Session, conv: WhatsAppConversation, user_text: str, input_id: Optional[str]) -> Dict[str, Any]:
        """Commits API-verified flight details only after the customer confirms."""
        text_u = (input_id or user_text).strip().upper()

        if text_u in ("BTN_CHANGE_AIRPORT", "CHANGE AIRPORT", "CHANGE_AIRPORT") or "CHANGE AIRPORT" in text_u:
            return cls._prompt_change_airport_from_flight(db, conv)

        if text_u in ("BTN_CHANGE_PACKAGE", "CHANGE PACKAGE", "CHANGE_PACKAGE", "PACKAGES", "CHOOSE PACKAGE", "VIEW PACKAGES"):
            meta = dict(conv.flight_details_json) if isinstance(conv.flight_details_json, dict) else {}
            pending = meta.get("_pending_verified_flight") if isinstance(meta, dict) else {}
            if isinstance(pending, dict) and pending.get("aligned_travel_type"):
                meta["travel_type"] = pending["aligned_travel_type"]
                meta["flight_type"] = pending["aligned_travel_type"]
                conv.flight_details_json = meta
                flag_modified(conv, "flight_details_json")
                db.commit()
            cls._transition_state(db, conv, "SERVICE_SELECTION")
            return cls._send_airport_services_menu(db, conv)

        if "RE-ENTER" in text_u or "REENTER" in text_u or text_u == "BTN_REENTER_FLIGHT" or (
            "CHANGE" in text_u and "AIRPORT" not in text_u and "PACKAGE" not in text_u and "CONFIRM" not in text_u
        ) or text_u == "NO":
            conv.flight_num = None
            meta = dict(conv.flight_details_json) if isinstance(conv.flight_details_json, dict) else {}
            meta.pop("_pending_verified_flight", None)
            meta.pop("_pending_mismatch_flight", None)
            meta["verification_status"] = "not_verified"
            conv.flight_details_json = meta
            flag_modified(conv, "flight_details_json")
            cls._transition_state(db, conv, "FLIGHT_INPUT")
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your flight number (e.g., *EK501*, *AI2424*, *6E224*):")
            return {"status": "reprompt_flight", "success": True}

        if "CONFIRM" in text_u or "YES" in text_u or text_u == "1" or text_u == "BTN_CONFIRM_FLIGHT" or "PROCEED" in text_u:
            meta = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
            pending = meta.get("_pending_verified_flight") if isinstance(meta, dict) else None
            if not isinstance(pending, dict) or meta.get("verification_status") != "pending_confirmation":
                cls._transition_state(db, conv, "FLIGHT_INPUT")
                conv.flight_num = None
                whatsapp_client.send_text_message(
                    conv.phone_number,
                    "Please re-enter your flight number so we can verify it before continuing.",
                )
                return {"status": "flight_reverify_required", "success": False}

            cls._commit_accepted_flight(db, conv, pending, verification_status="verified")
            db.commit()
            msg = (
                f"Flight verified: *{conv.flight_num}*\n\n"
                "Please enter your Date of Travel in DD/MM/YYYY format (e.g., 25/08/2026):"
            )
            whatsapp_client.send_text_message(conv.phone_number, msg)
            return {"status": "date_prompt_sent", "success": True}

        whatsapp_client.send_text_message(conv.phone_number, "Please select *Confirm & Proceed*, *Change Package*, or *Re-enter Flight*.")
        return {"status": "invalid_flight_confirmation", "success": False}

    @classmethod
    def _state_flight_type_mismatch(
        cls,
        db: Session,
        conv: WhatsAppConversation,
        user_text: str,
        input_id: Optional[str],
    ) -> Dict[str, Any]:
        """
        Handles interactive responses when a customer flight type mismatch was detected:
        Option 1: Switch to International / Switch to Domestic
        Option 2: Re-enter Flight
        Option 3: Change Airport
        """
        text_u = (input_id or user_text).strip().upper()
        meta = dict(conv.flight_details_json) if isinstance(conv.flight_details_json, dict) else {}
        pending = meta.get("_pending_mismatch_flight")

        # Option 3: Change Airport
        if (
            text_u in ("BTN_CHANGE_AIRPORT", "CHANGE AIRPORT", "CHANGE_AIRPORT", "3")
            or "CHANGE AIRPORT" in text_u
        ):
            return cls._prompt_change_airport_from_flight(db, conv)

        # Option 2: Re-enter Flight
        if (
            text_u in ("BTN_REENTER_FLIGHT", "RE-ENTER FLIGHT", "REENTER FLIGHT", "RE-ENTER", "REENTER", "2")
            or "RE-ENTER" in text_u
            or "REENTER" in text_u
        ):
            meta.pop("_pending_mismatch_flight", None)
            meta.pop("_pending_verified_flight", None)
            meta["verification_status"] = "not_verified"
            conv.flight_num = None
            conv.flight_details_json = meta
            flag_modified(conv, "flight_details_json")
            cls._transition_state(db, conv, "FLIGHT_INPUT")
            db.commit()
            whatsapp_client.send_text_message(
                conv.phone_number,
                "Please enter your Flight Number (e.g., *EK501*, *AI2424*, *6E224*):",
            )
            return {"status": "reprompt_flight", "success": True}

        # Option 1: Switch Travel Type
        if (
            text_u in (
                "BTN_SWITCH_TRAVEL_TYPE",
                "BTN_SWITCH_INTERNATIONAL",
                "BTN_SWITCH_DOMESTIC",
                "1",
            )
            or "SWITCH" in text_u
            or "INTERNATIONAL" in text_u
            or "DOMESTIC" in text_u
        ):
            if not isinstance(pending, dict) or not pending.get("flight_number"):
                cls._transition_state(db, conv, "FLIGHT_INPUT")
                conv.flight_num = None
                whatsapp_client.send_text_message(
                    conv.phone_number,
                    "Please enter your Flight Number (e.g., *EK501*, *AI2424*, *6E224*):",
                )
                return {"status": "reprompt_flight", "success": True}

            target_tt = pending.get("actual_flight_type")
            if not target_tt:
                current_tt = meta.get("travel_type", "DOMESTIC")
                target_tt = "INTERNATIONAL" if current_tt == "DOMESTIC" else "DOMESTIC"

            # 1. Update travel_type and flight_type
            meta["travel_type"] = target_tt
            meta["flight_type"] = target_tt

            # 2. Preserve already verified flight
            meta["_pending_verified_flight"] = pending
            meta.pop("_pending_mismatch_flight", None)
            meta["verification_status"] = "pending_confirmation"

            # 3. Invalidate old incompatible service/pricing
            conv.selected_service_id = None
            conv.selected_service_name = None
            conv.total_amount = None
            meta.pop("unit_price", None)
            meta.pop("base_price", None)
            meta.pop("package", None)

            # 4. Resolve terminal for the target travel type
            jt = meta.get("journey_type", "DEPARTURE")
            if conv.selected_airport_iata:
                airport_obj = db.execute(
                    select(SupportedAirport).where(SupportedAirport.iata_code == conv.selected_airport_iata)
                ).scalar_one_or_none()
                if airport_obj:
                    applicable_terminals = cls._get_applicable_terminals(db, airport_obj, jt, target_tt)
                    api_flight = pending.get("api_flight") or {}
                    dep_term = (api_flight.get("departure") or {}).get("terminal")
                    arr_term = (api_flight.get("arrival") or {}).get("terminal")
                    fl_term = dep_term if jt == "DEPARTURE" else arr_term

                    matched_term = None
                    if fl_term and applicable_terminals:
                        fl_term_clean = str(fl_term).strip().upper()
                        for term in applicable_terminals:
                            if fl_term_clean in term.upper():
                                matched_term = term
                                break

                    if matched_term:
                        meta["terminal"] = matched_term
                    elif len(applicable_terminals) == 1:
                        meta["terminal"] = applicable_terminals[0]
                    elif len(applicable_terminals) > 1:
                        meta.pop("terminal", None)
                        conv.flight_details_json = meta
                        flag_modified(conv, "flight_details_json")
                        db.commit()
                        cls._transition_state(db, conv, "TERMINAL_SELECTION")
                        return cls._prompt_terminal_selection(conv, airport_obj, applicable_terminals)
                    else:
                        meta.pop("terminal", None)

            conv.flight_details_json = meta
            flag_modified(conv, "flight_details_json")
            db.commit()

            # 5. Transition to SERVICE_SELECTION with authoritative packages for target travel type
            cls._transition_state(db, conv, "SERVICE_SELECTION")
            return cls._send_airport_services_menu(db, conv)

        # Invalid response in mismatch state: re-prompt choices
        flight_data = (pending.get("api_flight") if isinstance(pending, dict) else None) or {}
        fn = pending.get("flight_number") or "" if isinstance(pending, dict) else ""
        sel_t = str(pending.get("selected_flight_type") or meta.get("travel_type") or "DOMESTIC") if isinstance(pending, dict) else "DOMESTIC"
        act_t = str(pending.get("actual_flight_type") or "INTERNATIONAL") if isinstance(pending, dict) else "INTERNATIONAL"
        cls._send_flight_type_mismatch_message(conv, flight_data, fn, sel_t, act_t)
        return {"status": "invalid_mismatch_choice", "success": False}

