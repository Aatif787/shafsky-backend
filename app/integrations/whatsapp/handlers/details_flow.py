"""
Details Flow Mixin for WhatsApp Booking State Machine.
Handles:
- Timezone resolution
- Flight datetime alignment to user-chosen travel date
- Strict date validation & advance notice / cutoff enforcement
- Passenger count validation & total amount recalculation
- Customer name collection
- Customer email verification (RFC 5322 & placeholder filtering)
- Customer phone collection
- Special requirements collection
- Booking summary presentation
"""

import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select

from app.models.whatsapp_models import WhatsAppConversation
from app.models.journey_models import SupportedAirport
from app.integrations.whatsapp.client import whatsapp_client
from app.utils.customer_email import (
    is_acceptable_customer_email as is_acceptable_whatsapp_customer_email,
    REAL_EMAIL_HELP as _REAL_EMAIL_HELP,
    EMAIL_FORMAT_HELP as _EMAIL_FORMAT_HELP,
)

logger = logging.getLogger(__name__)


class DetailsFlowMixin:
    """Mixin for travel date, passenger count, customer details, and booking summary."""

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
    def _align_scheduled_datetimes_to_date(
        cls,
        metadata: Dict[str, Any],
        travel_date: date,
        tz: timezone,
    ) -> Dict[str, Any]:
        """
        Authoritatively aligns API-derived departure_scheduled and arrival_scheduled
        timestamps to the customer's selected travel date, preserving exact flight
        operating times and overnight flight offsets.
        """
        from app.services.booking_cutoff import parse_scheduled_datetime

        new_meta = dict(metadata)
        new_meta["travel_date"] = travel_date.isoformat()

        dep_raw = new_meta.get("departure_scheduled")
        arr_raw = new_meta.get("arrival_scheduled")
        dep_dt = parse_scheduled_datetime(dep_raw, tz)
        arr_dt = parse_scheduled_datetime(arr_raw, tz)

        if dep_dt:
            aligned_dep = dep_dt.replace(
                year=travel_date.year,
                month=travel_date.month,
                day=travel_date.day,
            )
            new_meta["departure_scheduled"] = aligned_dep.isoformat()

            if arr_dt:
                day_offset = max(0, (arr_dt.date() - dep_dt.date()).days)
                target_arr_date = travel_date + timedelta(days=day_offset)
                aligned_arr = arr_dt.replace(
                    year=target_arr_date.year,
                    month=target_arr_date.month,
                    day=target_arr_date.day,
                )
                new_meta["arrival_scheduled"] = aligned_arr.isoformat()
        elif arr_dt:
            aligned_arr = arr_dt.replace(
                year=travel_date.year,
                month=travel_date.month,
                day=travel_date.day,
            )
            new_meta["arrival_scheduled"] = aligned_arr.isoformat()
        elif new_meta.get("flight_time") or new_meta.get("service_time"):
            time_str = new_meta.get("flight_time") or new_meta.get("service_time")
            try:
                parts = [int(p) for p in str(time_str).split(":")[:2]]
                dt_constructed = datetime(
                    travel_date.year,
                    travel_date.month,
                    travel_date.day,
                    parts[0],
                    parts[1],
                    tzinfo=tz,
                )
                new_meta["departure_scheduled"] = dt_constructed.isoformat()
                new_meta["arrival_scheduled"] = (dt_constructed + timedelta(hours=2)).isoformat()
            except Exception:
                pass

        return new_meta

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
        """
        clean_date = (date_input or "").strip()
        if not clean_date:
            error_msg = (
                "Please enter the date in DD/MM/YYYY format.\n"
                "Example: 25/08/2026"
            )
            return False, None, error_msg, "invalid_date_format"

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

        if parsed_date < today_in_tz:
            error_msg = (
                "❌ This date has already passed.\n"
                "Please enter a valid future date.\n\n"
                "Example: 25/08/2026"
            )
            return False, None, error_msg, "past_date_rejected"

        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        service_time_str = metadata.get("flight_time") or metadata.get("service_time")
        dep_raw = metadata.get("departure_scheduled")
        arr_raw = metadata.get("arrival_scheduled")

        from app.services.booking_cutoff import (
            airport_min_notice_hours,
            parse_scheduled_datetime,
            EXECUTIVE_PHONE,
            WHATSAPP_BRAND,
        )
        required = airport_min_notice_hours(
            metadata.get("travel_type") or metadata.get("flight_type") or metadata.get("transit_type")
        )
        kind = "international" if required >= 24 else "domestic"

        scheduled_dt = None
        if service_time_str:
            try:
                time_parts = [int(p) for p in str(service_time_str).split(":")[:2]]
                scheduled_dt = datetime(
                    parsed_date.year, parsed_date.month, parsed_date.day,
                    time_parts[0], time_parts[1],
                    tzinfo=service_tz
                )
            except Exception:
                pass
        elif dep_raw or arr_raw:
            raw_sched = dep_raw or arr_raw
            parsed_sched = parse_scheduled_datetime(raw_sched, service_tz)
            if parsed_sched:
                scheduled_dt = parsed_sched.replace(
                    year=parsed_date.year,
                    month=parsed_date.month,
                    day=parsed_date.day,
                )

        if scheduled_dt is not None:
            if scheduled_dt <= now_in_tz or (scheduled_dt - now_in_tz).total_seconds() < required * 3600:
                error_msg = (
                    f"❌ Airport services need at least {required} hours' notice "
                    f"for {kind} flights.\n\n"
                    f"Need this urgently? Please call our executive on *{EXECUTIVE_PHONE}*.\n\n"
                    f"{WHATSAPP_BRAND}"
                )
                return False, None, error_msg, "cutoff_violation"

        return True, parsed_date, None, "valid_date"

    @classmethod
    def _state_date_selection(cls, db: Session, conv: WhatsAppConversation, date_input: str) -> Dict[str, Any]:
        """Strict date validation handler for WhatsApp booking flow."""
        is_valid, parsed_date, err_msg, status_code = cls._validate_whatsapp_date(db, conv, date_input)
        if not is_valid:
            whatsapp_client.send_text_message(conv.phone_number, err_msg)
            return {"status": status_code, "success": False}

        date_formatted = parsed_date.strftime("%d %B %Y")
        conv.booking_date = date_formatted

        meta = dict(conv.flight_details_json) if isinstance(conv.flight_details_json, dict) else {}
        service_tz = cls._get_service_timezone(
            db.scalar(
                select(SupportedAirport.timezone).where(
                    SupportedAirport.iata_code == (conv.selected_airport_iata or "").upper()
                )
            ) if conv.selected_airport_iata else None
        )
        meta = cls._align_scheduled_datetimes_to_date(meta, parsed_date, service_tz)
        meta["booking_date"] = date_formatted
        conv.flight_details_json = meta
        flag_modified(conv, "flight_details_json")
        db.commit()

        cls._transition_state(db, conv, "PASSENGER_COUNT")

        msg = f"Date Saved: *{date_formatted}*\n\nHow many passengers will be travelling? (Enter a number, e.g., 2):"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "passenger_count_prompt_sent", "success": True}

    @classmethod
    def _state_passenger_count(cls, db: Session, conv: WhatsAppConversation, count_input: str) -> Dict[str, Any]:
        digits = "".join(filter(str.isdigit, count_input))
        if not digits or int(digits) < 1 or int(digits) > 50:
            whatsapp_client.send_text_message(conv.phone_number, "Please enter a valid passenger count (1-50).")
            return {"status": "invalid_passenger_count", "success": False}

        count = int(digits)
        conv.passenger_count = count

        meta = dict(conv.flight_details_json) if isinstance(conv.flight_details_json, dict) else {}
        unit_price = meta.get("unit_price") or meta.get("base_price")

        if unit_price is None and conv.total_amount and float(conv.total_amount) > 0:
            unit_price = float(conv.total_amount)
            meta["unit_price"] = unit_price
            meta["base_price"] = unit_price
            conv.flight_details_json = meta
            flag_modified(conv, "flight_details_json")

        if unit_price is not None:
            conv.total_amount = float(unit_price) * count
        elif conv.total_amount and float(conv.total_amount) > 0:
            conv.total_amount = float(conv.total_amount) * count

        db.commit()

        cls._transition_state(db, conv, "CUSTOMER_NAME")

        msg = f"Passengers: *{count}*\n\nMay I have your full name?"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "name_prompt_sent", "success": True}

    @classmethod
    def _state_customer_name(cls, db: Session, conv: WhatsAppConversation, name_input: str) -> Dict[str, Any]:
        clean_name = name_input.strip()
        if len(clean_name) < 2:
            whatsapp_client.send_text_message(conv.phone_number, "Please enter your valid full name.")
            return {"status": "invalid_name", "success": False}

        conv.customer_name = clean_name
        cls._transition_state(db, conv, "CUSTOMER_EMAIL")

        msg = f"Name: *{clean_name}*\n\nPlease provide your email address for booking confirmation:"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "email_prompt_sent", "success": True}

    @classmethod
    def _state_customer_email(cls, db: Session, conv: WhatsAppConversation, email_input: str) -> Dict[str, Any]:
        clean_email = email_input.strip()
        ok, reason = is_acceptable_whatsapp_customer_email(clean_email)
        if not ok:
            help_msg = _EMAIL_FORMAT_HELP if reason == "invalid_syntax" else _REAL_EMAIL_HELP
            whatsapp_client.send_text_message(conv.phone_number, help_msg)
            return {"status": "invalid_email", "success": False, "reason": reason}

        conv.customer_email = clean_email
        cls._transition_state(db, conv, "CUSTOMER_PHONE")

        msg = f"Email Saved: *{clean_email}*\n\nPlease provide your contact phone number (or type 'Same' to use this WhatsApp number):"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "phone_prompt_sent", "success": True}

    @classmethod
    def _state_customer_phone(cls, db: Session, conv: WhatsAppConversation, phone_input: str) -> Dict[str, Any]:
        clean_p = phone_input.strip().lower()
        if clean_p in ["same", "same number", "this", "my number", "yes", "ok"]:
            conv.customer_phone = conv.phone_number
        else:
            digits = "".join(filter(str.isdigit, phone_input))
            if len(digits) < 7:
                whatsapp_client.send_text_message(conv.phone_number, "Please enter a valid contact phone number with country code (or type 'Same').")
                return {"status": "invalid_phone", "success": False}
            conv.customer_phone = digits

        cls._transition_state(db, conv, "ADDITIONAL_REQUIREMENTS")

        msg = "Do you have any special requirements or notes? (Type *None* if no special requests):"
        whatsapp_client.send_text_message(conv.phone_number, msg)
        return {"status": "notes_prompt_sent", "success": True}

    @classmethod
    def _state_additional_requirements(cls, db: Session, conv: WhatsAppConversation, notes_input: str) -> Dict[str, Any]:
        clean_notes = notes_input.strip()
        conv.additional_requirements = "None" if clean_notes.lower() in ["none", "no", "n/a", "-"] else clean_notes
        cls._transition_state(db, conv, "BOOKING_REVIEW")

        return cls._send_booking_summary(db, conv)

    @classmethod
    def _send_booking_summary(cls, db: Session, conv: WhatsAppConversation) -> Dict[str, Any]:
        """Displays booking summary before creation."""
        metadata = conv.flight_details_json if isinstance(conv.flight_details_json, dict) else {}
        jt = metadata.get("journey_type")
        tt = metadata.get("travel_type")
        unit_price = metadata.get("unit_price") or metadata.get("base_price")
        passengers = max(1, conv.passenger_count or 1)

        # Fallback: dynamically resolve price if unit_price or total_amount is missing
        if (unit_price is None or conv.total_amount is None or float(conv.total_amount) <= 0) and conv.selected_airport_iata:
            try:
                from app.models.journey_models import SupportedAirport
                from app.integrations.whatsapp import copy as wa_copy
                airport_obj = db.execute(
                    select(SupportedAirport).where(SupportedAirport.iata_code == conv.selected_airport_iata)
                ).scalar_one_or_none()
                if airport_obj:
                    intl_or_dom = [tt or "DOMESTIC", "ALL"]
                    matching = cls._get_authoritative_airport_packages(
                        db, airport_obj.id, jt or "DEPARTURE", intl_or_dom, terminal=metadata.get("terminal")
                    )
                    if matching:
                        current_tier = wa_copy._extract_tier_name(conv.selected_service_name or "").lower()
                        picked = None
                        for aps, s in matching:
                            if current_tier and current_tier in (s.name or "").lower():
                                picked = (aps, s)
                                break
                        if not picked:
                            picked = matching[0]
                        aps, s = picked
                        unit_price = float(aps.price)
                        conv.selected_service_id = str(s.id)
                        conv.selected_service_name = s.name
                        metadata["unit_price"] = unit_price
                        metadata["base_price"] = unit_price
                        metadata["package"] = s.name
                        conv.flight_details_json = metadata
                        flag_modified(conv, "flight_details_json")
            except Exception:
                pass

        if unit_price is not None and passengers > 0:
            conv.total_amount = float(unit_price) * passengers
            db.commit()

        summary_lines = [
            "📋 *BOOKING SUMMARY — Shafsky Aviation Services*\n",
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

        is_charter_enquiry = (conv.selected_category or "").strip().lower() == "private charter"
        summary_lines.append(
            "Please review your details and submit your charter enquiry:"
            if is_charter_enquiry else
            "Please review your details to confirm your booking request:"
        )

        body_text = "\n".join(summary_lines)
        buttons = [
            {"id": "btn_confirm_booking", "title": "Submit Enquiry" if is_charter_enquiry else "Confirm Booking"},
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

        return {"status": "summary_sent", "success": True}
