"""
Formatting and template generation mixin for WhatsApp department notifications.
"""

import logging
import os
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger("shafsky.whatsapp_department")


def _get_settings():
    try:
        import app.services.whatsapp_department_service as _wds
        if hasattr(_wds, "settings"):
            return _wds.settings
    except Exception:
        pass
    return settings


class Department(str, Enum):
    """Internal operations departments."""
    DEPARTMENT_1 = "Department 1 (Meet & Greet / Lounge)"
    DEPARTMENT_2 = "Department 2 (Ticketing / Transport / Hotels)"
    DEPARTMENT_3 = "Department 3 (Air Charter / Cargo / Special Services)"


class WhatsAppDepartmentFormattingMixin:
    """Provides formatting, template parameter building, and recipient resolution for operations alerts."""

    @classmethod
    def normalize_phone_number(cls, raw: Optional[str]) -> Optional[str]:
        """
        Normalizes a phone number to international E.164 digits-only format without leading plus.

        Handles the formats the website booking form and the WhatsApp bot can produce:
        * 10-digit Indian mobile numbers (9599087957 -> 919599087957).
        * Numbers that already carry the country code (919599087957 stays unchanged).
        * Indian trunk prefix variants (+91 0 98765 43210, 09876543210 -> 919876543210).
        * International numbers (+1 415 555 2671 -> 14155552671, +44 20 7946 0958).
        * International access prefix (0044... -> 44...).

        The country code is never duplicated, and numbers that cannot be a valid
        E.164 subscriber number are rejected instead of being sent to Meta.
        """
        cleaned = re.sub(r"\D", "", str(raw or "").strip())
        if not cleaned:
            return None

        # 00 international access prefix (0044 20 7946 0958 -> 442079460958).
        # Only when what follows is clearly an international number, not a bare
        # national number that happens to start with two zeroes.
        if cleaned.startswith("00") and len(cleaned) - 2 > 10:
            cleaned = cleaned[2:]

        # Indian trunk prefix 0: 09876543210 -> 9876543210, or 91 0 98765 43210 -> 91 98765 43210
        if cleaned.startswith("0"):
            cleaned = cleaned.lstrip("0")
        elif cleaned.startswith("91") and len(cleaned) == 13 and cleaned[2] == "0":
            cleaned = cleaned[:2] + cleaned[3:]

        if not cleaned:
            return None

        # Bare national numbers: India is the documented default of the booking form.
        if len(cleaned) == 10:
            cleaned = f"91{cleaned}"

        # E.164 allows 8-15 significant digits including the country code.
        if not 10 <= len(cleaned) <= 15:
            return None

        # Reject leading zeros that survived normalization (invalid country code).
        if cleaned.startswith("0"):
            return None

        return cleaned

    @classmethod
    def alternate_template_language(cls, template_lang: Optional[str]) -> Optional[str]:
        """Returns a second language code worth trying when Meta answers 132001.

        Meta registers templates per language: `en` and `en_US` are different entries of
        the same name. A deployment that uses one form for a template registered under the
        other gets 132001 ("template does not exist in the translation") for every
        recipient. 132001 is returned before a message is created, so trying the sibling
        code once is safe and cannot duplicate a delivery.
        """
        clean = (template_lang or "").strip()
        if not clean:
            return None
        if "_" in clean:
            base = clean.split("_", 1)[0].strip()
            return base or None
        return f"{clean}_US"

    @classmethod
    def get_recipient_phone_numbers(cls) -> List[str]:
        """
        Retrieves and normalizes the 5 configured recipient numbers from environment variables:
        - WHATSAPP_BOOKING_NOTIFY_PHONE_1
        - WHATSAPP_BOOKING_NOTIFY_PHONE_2
        - WHATSAPP_BOOKING_NOTIFY_PHONE_3
        - WHATSAPP_BOOKING_NOTIFY_PHONE_4
        - WHATSAPP_BOOKING_NOTIFY_PHONE_5
        Fallback: If none of the 5 are configured, checks legacy WHATSAPP_OFFICER_NOTIFY_PHONE.
        """
        env_vars = [
            "WHATSAPP_BOOKING_NOTIFY_PHONE_1",
            "WHATSAPP_BOOKING_NOTIFY_PHONE_2",
            "WHATSAPP_BOOKING_NOTIFY_PHONE_3",
            "WHATSAPP_BOOKING_NOTIFY_PHONE_4",
            "WHATSAPP_BOOKING_NOTIFY_PHONE_5",
        ]
        recipients: List[str] = []
        current_settings = _get_settings()
        for var in env_vars:
            val = getattr(current_settings, var, None)
            if val is None:
                val = os.getenv(var, "")
            norm = cls.normalize_phone_number(val)
            if norm and norm not in recipients:
                recipients.append(norm)

        # Fallback if none of the 5 are configured
        if not recipients:
            legacy_officer = getattr(current_settings, "WHATSAPP_OFFICER_NOTIFY_PHONE", None)
            if legacy_officer is None:
                legacy_officer = os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "")
            norm = cls.normalize_phone_number(legacy_officer)
            if norm:
                recipients.append(norm)

        return recipients

    @classmethod
    def _mask_phone(cls, phone: str) -> str:
        """Masks phone number for safe, non-sensitive logging."""
        cleaned = re.sub(r"\D", "", phone or "")
        if len(cleaned) > 6:
            return f"{cleaned[:3]}****{cleaned[-3:]}"
        return cleaned

    @classmethod
    def sanitize_template_param(cls, text: str) -> str:
        """
        Truncates and cleans text parameter to strictly respect Meta Cloud API 1024-character maximum.
        """
        clean = (text or "").strip()
        if len(clean) > cls.MAX_TEMPLATE_PARAM_CHARS:
            return clean[: cls.MAX_TEMPLATE_PARAM_CHARS - 3] + "..."
        return clean

    @classmethod
    def to_template_parameter(cls, text: str) -> str:
        """Returns a value that is legal inside a Meta BODY parameter.

        Meta rejects parameters containing new-line or tab characters, or more than
        four consecutive spaces (the API accepts the request but the message is
        rejected), so a formatted multi-line alert must be flattened before it is
        placed in {{1}}.
        """
        clean = cls.sanitize_template_param(str(text or ""))
        clean = clean.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
        clean = re.sub(r" {2,}", " ", clean).strip()
        if len(clean) > cls.MAX_TEMPLATE_PARAM_CHARS:
            clean = clean[: cls.MAX_TEMPLATE_PARAM_CHARS - 3].rstrip() + "..."
        return clean

    @classmethod
    def build_template_components(
        cls,
        message_body: str,
        template_name: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Builds Meta-compliant template components.
        - For the 11-variable clean operations template ('shafsky_clean_booking_alert'):
          Extracts and formats the 11 discrete parameters matching the clean operations card.
        - For the 9-variable structured operations template ('shafsky_operations_alert'):
          Extracts and formats the 9 discrete parameters (Reference, Service, Customer,
          Contact, Flight/Route, Date & Time, Passengers, Booking Value, Status).
        - For single-variable operations templates ('shafsky_ops_alert', 'shafsky_ops_booking'):
          Extracts a clean, concise single-line summary without newlines to prevent Meta 132018 errors
          and eliminate wall-of-text congestion.
        Ensures all parameters stay under Meta's 1024-character maximum and contain
        no new-line/tab characters or runs of more than four spaces.
        """
        t_name = (template_name or "").strip()
        if t_name in ("shafsky_clean_booking_alert", "shafsky_ops_booking_clean") and context:
            return cls._build_shafsky_clean_booking_alert_components(context)
        if t_name == "shafsky_operations_alert" and context:
            return cls._build_shafsky_operations_alert_components(context)

        param_text = cls.to_template_parameter(message_body)

        return [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": param_text}
                ],
            }
        ]

    @classmethod
    def _build_shafsky_clean_booking_alert_components(
        cls, context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "N/A").strip()
        customer_name = str(context.get("passenger_name") or context.get("passengerName") or "N/A").strip()
        customer_phone = str(context.get("passenger_phone") or context.get("passengerPhone") or "").strip()
        customer_email = str(context.get("passenger_email") or context.get("passengerEmail") or "").strip()

        details = context.get("details") if isinstance(context.get("details"), dict) else {}
        final_pax = (
            details.get("passenger_count")
            or details.get("passengers")
            or context.get("passenger_count")
            or context.get("passengerCount")
            or 1
        )

        category = context.get("service_category") or context.get("serviceCategory")
        service_type_raw = context.get("service_type") or context.get("serviceType")
        department, service_label = cls.resolve_department(category, service_type_raw, context)

        service_type = str(
            context.get("service_name")
            or service_type_raw
            or context.get("package")
            or service_label
        ).strip()

        if service_label and service_type and service_label.lower() not in service_type.lower():
            service_text = f"{service_label} — {service_type}"
        else:
            service_text = service_type or service_label

        if department == Department.DEPARTMENT_1:
            dept_title = "Meet & Greet / Lounge"
        elif department == Department.DEPARTMENT_2:
            dept_title = "Ticketing / Transport / Hotels"
        else:
            dept_title = "Air Charter / Cargo / Special Services"

        raw_origin = str(context.get("origin_code") or context.get("origin") or context.get("airport_code") or "").strip()
        raw_dest = str(context.get("dest_code") or context.get("destination") or "").strip()
        origin = raw_origin.upper() if len(raw_origin) == 3 else raw_origin
        dest = raw_dest.upper() if len(raw_dest) == 3 else raw_dest
        airport = origin
        flight_num = str(context.get("flight_num") or context.get("flightNum") or "").strip()
        terminal = str(context.get("terminal") or "").strip()

        journey_type_val = str(context.get("journey_type") or "").strip().upper()
        raw_service_clock = (
            (context.get("arrival_time") if journey_type_val == "ARRIVAL" else None)
            or context.get("departure_time")
            or context.get("arrival_time")
            or context.get("service_date")
            or context.get("serviceDate")
        )
        service_airport = (dest if journey_type_val == "ARRIVAL" else origin) or airport or origin or dest
        date_time = cls._display_when(raw_service_clock, airport_code=service_airport) or "Not specified"

        raw_amount = context.get("total_amount") if context.get("total_amount") is not None else context.get("totalAmount")
        currency = str(context.get("currency") or "INR").upper()
        status = str(context.get("status") or "PENDING").upper()

        if raw_amount is not None:
            try:
                amt_val = float(raw_amount)
                if amt_val > 0:
                    symbol = "₹" if currency == "INR" else f"{currency} "
                    amount_str = f"{symbol}{amt_val:,.2f}"
                else:
                    amount_str = "Quotation Required"
            except (ValueError, TypeError):
                amount_str = "Quotation Required"
        else:
            amount_str = "Quotation Required"

        contact_parts = []
        if customer_phone and customer_phone.upper() != "N/A":
            contact_parts.append(customer_phone)
        if customer_email and customer_email.upper() != "N/A":
            contact_parts.append(customer_email)
        contact_str = " | ".join(contact_parts) if contact_parts else "Not provided"

        route_str = f"{origin} → {dest}" if origin and dest and origin != dest else (origin or dest or airport or "Not specified")
        flight_parts = []
        if flight_num and flight_num.upper() != "N/A":
            flight_parts.append(flight_num)
        if terminal:
            term_str = terminal if terminal.lower().startswith("terminal") else f"Terminal {terminal}"
            flight_parts.append(term_str)
        flight_text = " ".join(flight_parts) if flight_parts else "Not specified"

        raw_params = [
            dept_title,
            booking_ref,
            service_text,
            customer_name,
            contact_str,
            str(final_pax),
            route_str,
            flight_text,
            date_time,
            amount_str,
            status,
        ]

        parameters = [
            {"type": "text", "text": cls.to_template_parameter(p)}
            for p in raw_params
        ]

        return [{"type": "body", "parameters": parameters}]

    @classmethod
    def _build_single_variable_summary(cls, context: Dict[str, Any]) -> str:
        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "N/A").strip()
        customer_name = str(context.get("passenger_name") or context.get("passengerName") or "N/A").strip()
        customer_phone = str(context.get("passenger_phone") or context.get("passengerPhone") or "").strip()

        category = context.get("service_category") or context.get("serviceCategory")
        service_type_raw = context.get("service_type") or context.get("serviceType")
        _, service_label = cls.resolve_department(category, service_type_raw, context)

        service_type = str(
            context.get("service_name")
            or service_type_raw
            or context.get("package")
            or service_label
        ).strip()

        if service_label and service_type and service_label.lower() not in service_type.lower():
            service_text = f"{service_label} — {service_type}"
        else:
            service_text = service_type or service_label

        raw_origin = str(context.get("origin_code") or context.get("origin") or context.get("airport_code") or "").strip()
        raw_dest = str(context.get("dest_code") or context.get("destination") or "").strip()
        origin = raw_origin.upper() if len(raw_origin) == 3 else raw_origin
        dest = raw_dest.upper() if len(raw_dest) == 3 else raw_dest
        flight_num = str(context.get("flight_num") or context.get("flightNum") or "").strip()

        loc_parts = []
        if origin and dest and origin != dest:
            loc_parts.append(f"{origin}→{dest}")
        elif origin:
            loc_parts.append(origin)
        if flight_num and flight_num.upper() != "N/A":
            loc_parts.append(flight_num)
        loc_str = f" ({', '.join(loc_parts)})" if loc_parts else ""

        journey_type_val = str(context.get("journey_type") or "").strip().upper()
        raw_service_clock = (
            (context.get("arrival_time") if journey_type_val == "ARRIVAL" else None)
            or context.get("departure_time")
            or context.get("arrival_time")
            or context.get("service_date")
            or context.get("serviceDate")
        )
        date_time = cls._display_when(raw_service_clock, airport_code=origin or dest) or "Date: N/A"

        raw_amount = context.get("total_amount") if context.get("total_amount") is not None else context.get("totalAmount")
        currency = str(context.get("currency") or "INR").upper()
        status = str(context.get("status") or "PENDING").upper()

        if raw_amount is not None:
            try:
                amt_val = float(raw_amount)
                amount_str = f"₹{amt_val:,.2f}" if (amt_val > 0 and currency == "INR") else (f"{currency} {amt_val:,.2f}" if amt_val > 0 else "Quote Req")
            except (ValueError, TypeError):
                amount_str = "Quote Req"
        else:
            amount_str = "Quote Req"

        phone_str = f" ({customer_phone})" if customer_phone and customer_phone != "N/A" else ""
        summary = f"{service_text} | Ref: {booking_ref} | Guest: {customer_name}{phone_str}{loc_str} | {date_time} | {amount_str} | {status}"
        return cls.to_template_parameter(summary)

    @classmethod
    def _build_shafsky_operations_alert_components(
        cls, context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "N/A").strip()
        customer_name = str(context.get("passenger_name") or context.get("passengerName") or "N/A").strip()
        customer_phone = str(context.get("passenger_phone") or context.get("passengerPhone") or "").strip()
        customer_email = str(context.get("passenger_email") or context.get("passengerEmail") or "").strip()

        details = context.get("details") if isinstance(context.get("details"), dict) else {}
        options = context.get("service_options") if isinstance(context.get("service_options"), dict) else {}

        final_pax = (
            details.get("passenger_count")
            or details.get("passengers")
            or context.get("passenger_count")
            or context.get("passengerCount")
            or 1
        )

        category = context.get("service_category") or context.get("serviceCategory")
        service_type_raw = context.get("service_type") or context.get("serviceType")
        _, service_label = cls.resolve_department(category, service_type_raw, context)

        service_type = str(
            context.get("service_name")
            or service_type_raw
            or context.get("package")
            or service_label
        ).strip()

        if service_label and service_type and service_label.lower() not in service_type.lower():
            service_text = f"{service_label} — {service_type}"
        else:
            service_text = service_type or service_label

        raw_origin = str(context.get("origin_code") or context.get("origin") or context.get("airport_code") or "").strip()
        raw_dest = str(context.get("dest_code") or context.get("destination") or "").strip()
        origin = raw_origin.upper() if len(raw_origin) == 3 else raw_origin
        dest = raw_dest.upper() if len(raw_dest) == 3 else raw_dest
        airport = origin
        flight_num = str(context.get("flight_num") or context.get("flightNum") or "").strip()
        terminal = str(context.get("terminal") or "").strip()

        journey_type_val = str(context.get("journey_type") or "").strip().upper()
        raw_service_clock = (
            (context.get("arrival_time") if journey_type_val == "ARRIVAL" else None)
            or context.get("departure_time")
            or context.get("arrival_time")
            or context.get("service_date")
            or context.get("serviceDate")
        )
        date_time = cls._display_when(raw_service_clock, airport_code=airport or origin or dest) or "Not specified"

        raw_amount = context.get("total_amount") if context.get("total_amount") is not None else context.get("totalAmount")
        currency = str(context.get("currency") or "INR").upper()
        status = str(context.get("status") or "PENDING").upper()

        if raw_amount is not None:
            try:
                amt_val = float(raw_amount)
                if amt_val > 0:
                    symbol = "₹" if currency == "INR" else f"{currency} "
                    amount_str = f"{symbol}{amt_val:,.2f}"
                else:
                    amount_str = "Quotation Required"
            except (ValueError, TypeError):
                amount_str = "Quotation Required"
        else:
            amount_str = "Quotation Required"

        contact_parts = []
        if customer_phone and customer_phone.upper() != "N/A":
            contact_parts.append(customer_phone)
        if customer_email and customer_email.upper() != "N/A":
            contact_parts.append(customer_email)
        contact_str = " | ".join(contact_parts) if contact_parts else "Not provided"

        route_str = f"{origin} → {dest}" if origin and dest and origin != dest else (origin or dest or airport)
        flight_parts = []
        if flight_num and flight_num.upper() != "N/A":
            flight_parts.append(flight_num)
        if route_str:
            flight_parts.append(route_str)
        if terminal:
            term_str = terminal if terminal.lower().startswith("terminal") else f"Terminal {terminal}"
            flight_parts.append(term_str)
        flight_text = " | ".join(flight_parts) if flight_parts else (route_str or "Not specified")

        raw_params = [
            booking_ref,
            service_text,
            customer_name,
            contact_str,
            flight_text,
            date_time,
            str(final_pax),
            amount_str,
            status,
        ]

        parameters = [
            {"type": "text", "text": cls.to_template_parameter(p)}
            for p in raw_params
        ]

        return [{"type": "body", "parameters": parameters}]

    @classmethod
    def resolve_department(
        cls,
        service_category: Optional[str] = None,
        service_type: Optional[str] = None,
        raw_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Department, str]:
        """
        Determines the service category and canonical label based on verified payload data.
        Returns: (Department, canonical_service_label)
        """
        cat_str = (service_category or "").strip().lower()
        type_str = (service_type or "").strip().lower()

        # Check raw context if available for deeper hints
        details = (raw_context or {}).get("details") or {}
        if isinstance(details, dict):
            if details.get("lounge_type") or details.get("lounge_category"):
                return Department.DEPARTMENT_1, "Lounge"
            if details.get("vehicle_preference") or details.get("vehicle_category") or details.get("pickup_location"):
                return Department.DEPARTMENT_2, "Ground Transport"

        # 1. Exact Category Matches
        if cat_str in cls.DEPT1_CATEGORIES:
            label = "Lounge" if "lounge" in cat_str else "Meet & Greet"
            return Department.DEPARTMENT_1, label

        if cat_str in cls.DEPT2_CATEGORIES:
            if any(k in cat_str for k in ("transport", "round trip", "roundtrip")):
                label = "Transport"
            elif any(k in cat_str for k in ("ticket", "flight")):
                label = "Ticketing"
            else:
                label = "Hotels"
            return Department.DEPARTMENT_2, label

        if cat_str in cls.DEPT3_CATEGORIES:
            if "charter" in cat_str:
                label = "Air Charter"
            elif any(k in cat_str for k in ("cargo", "freight", "logistics")):
                label = "Cargo & Logistics"
            else:
                label = "Special Services"
            return Department.DEPARTMENT_3, label

        # 2. Inspect Service Type / Package if Category was generic or empty
        combined = f"{cat_str} {type_str}"

        # Dept 1 check
        if any(k in combined for k in ("lounge", "meet", "greet", "assist", "fast track", "baggage")):
            return Department.DEPARTMENT_1, "Meet & Greet" if "lounge" not in combined else "Lounge"

        # Dept 2 check
        if any(k in combined for k in ("ticket", "sedan", "suv", "van", "transfer", "driver", "transport", "round trip", "roundtrip", "hotel")):
            if any(k in combined for k in ("ticket", "flight")):
                return Department.DEPARTMENT_2, "Ticketing"
            if any(k in combined for k in ("hotel",)):
                return Department.DEPARTMENT_2, "Hotels"
            return Department.DEPARTMENT_2, "Transport"

        # Dept 3 check
        if any(k in combined for k in ("charter", "jet", "helicopter", "turboprop", "cargo", "freight", "medical", "ambulance", "stretcher", "wheelchair", "visa", "insurance")):
            if any(k in combined for k in ("charter", "jet", "helicopter", "turboprop")):
                return Department.DEPARTMENT_3, "Air Charter"
            if any(k in combined for k in ("cargo", "freight")):
                return Department.DEPARTMENT_3, "Cargo & Logistics"
            return Department.DEPARTMENT_3, "Special Services"

        # Default fallback for unclassified airport assistance / direct bookings
        return Department.DEPARTMENT_1, "Meet & Greet"

    @classmethod
    def format_booking_notification(
        cls,
        context: Dict[str, Any],
        department: Department,
        service_label: str,
    ) -> str:
        """
        Formats a clean, professional, and readable WhatsApp markdown message
        tailored to operations staff.
        - For service enquiries (quote requests, transport/ticketing enquiries with reference rates):
          Maintains the bulleted enquiry structure expected by operations and verification tests.
        - For standard bookings / operations alerts:
          Renders the structured multi-line operational card layout.
        """
        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "N/A").strip()
        customer_name = str(context.get("passenger_name") or context.get("passengerName") or "N/A").strip()
        customer_phone = str(context.get("passenger_phone") or context.get("passengerPhone") or "").strip()
        customer_email = str(context.get("passenger_email") or context.get("passengerEmail") or "").strip()

        details = context.get("details") if isinstance(context.get("details"), dict) else {}
        options = context.get("service_options") if isinstance(context.get("service_options"), dict) else {}

        final_pax = (
            details.get("passenger_count")
            or details.get("passengers")
            or context.get("passenger_count")
            or context.get("passengerCount")
            or 1
        )

        service_type = str(
            context.get("service_name")
            or context.get("service_type")
            or context.get("package")
            or service_label
        ).strip()

        if service_label and service_type and service_label.lower() not in service_type.lower():
            service_text = f"{service_label} — {service_type}"
        else:
            service_text = service_type or service_label

        raw_origin = str(context.get("origin_code") or context.get("origin") or context.get("airport_code") or "").strip()
        raw_dest = str(context.get("dest_code") or context.get("destination") or "").strip()
        origin = raw_origin.upper() if len(raw_origin) == 3 else raw_origin
        dest = raw_dest.upper() if len(raw_dest) == 3 else raw_dest
        airport = origin
        flight_num = str(context.get("flight_num") or context.get("flightNum") or "").strip()
        terminal = str(context.get("terminal") or "").strip()

        journey_type_val = str(context.get("journey_type") or "").strip().upper()
        raw_service_clock = (
            (context.get("arrival_time") if journey_type_val == "ARRIVAL" else None)
            or context.get("departure_time")
            or context.get("arrival_time")
            or context.get("service_date")
            or context.get("serviceDate")
        )
        date_time = cls._display_when(raw_service_clock, airport_code=airport or origin or dest) or "Not specified"

        # Amount & Status
        raw_amount = context.get("total_amount") if context.get("total_amount") is not None else context.get("totalAmount")
        currency = str(context.get("currency") or "INR").upper()
        status = str(context.get("status") or "PENDING").upper()

        if raw_amount is not None:
            try:
                amt_val = float(raw_amount)
                if amt_val > 0:
                    symbol = "₹" if currency == "INR" else f"{currency} "
                    amount_str = f"{symbol}{amt_val:,.2f}"
                else:
                    amount_str = "Quotation Required"
            except (ValueError, TypeError):
                amount_str = "Quotation Required"
        else:
            amount_str = "Quotation Required"

        # Notes
        notes = (
            context.get("notes")
            or details.get("additional_requirements")
            or details.get("notes")
            or details.get("special_requests")
        )

        route_str = f"{origin} → {dest}" if origin and dest and origin != dest else (origin or dest or airport)
        has_flight = flight_num and flight_num.upper() != "N/A"

        # Vehicle and Provider details if available
        vehicle_name = (
            details.get("vehicle_preference")
            or details.get("vehicle_name")
            or details.get("vehicleName")
            or options.get("vehicle_name")
        )
        vehicle_id = details.get("vehicle_id") or details.get("vehicleId") or options.get("vehicle_id")
        vehicle_category = details.get("vehicle_category") or details.get("vehicleCategory") or options.get("vehicle_category")
        provider = details.get("provider") or options.get("provider")
        ref_price = details.get("reference_price") or options.get("reference_price")

        # Round Trip specific fields
        outbound_date = details.get("outbound_date")
        return_date = details.get("return_date")
        outbound_flight = details.get("outbound_flight")
        return_flight = details.get("return_flight")

        # Determine department and service-specific header
        if service_label == "Transport" or any(k in service_type.lower() for k in ("transport", "sedan", "suv", "van")):
            if any(k in service_type.lower() for k in ("round trip", "roundtrip")):
                header = "✈️ *NEW ROUND TRIP ENQUIRY — Ticketing / Transport / Hotels*"
            else:
                header = "🚗 *NEW TRANSPORT ENQUIRY — Ticketing / Transport / Hotels*"
        elif service_label == "Ticketing":
            header = "🎫 *NEW TICKETING ENQUIRY — Ticketing / Transport / Hotels*"
        elif service_label == "Lounge":
            header = "🛋️ *NEW LOUNGE ENQUIRY RECEIVED — Meet & Greet / Lounge*"
        elif department == Department.DEPARTMENT_1:
            header = "✈️ *NEW BOOKING / ENQUIRY — Meet & Greet / Lounge*"
        elif department == Department.DEPARTMENT_2:
            header = "🚗 *NEW BOOKING / ENQUIRY — Ticketing / Transport / Hotels*"
        else:
            header = "🛩️ *NEW SERVICE ENQUIRY — Air Charter / Cargo / Special Services*"

        contact_parts = []
        if customer_phone and customer_phone.upper() != "N/A":
            contact_parts.append(customer_phone)
        if customer_email and customer_email.upper() != "N/A":
            contact_parts.append(customer_email)
        contact_str = " | ".join(contact_parts) if contact_parts else "Not provided"

        lines = [
            header,
            "",
            f"• *Reference*: `{booking_ref}`",
            f"• *Service*: {service_text}",
            f"• *Customer*: {customer_name}",
            f"• *Contact*: {contact_str}",
            f"• *Guests / Pax / Passengers*: {final_pax}",
        ]

        if vehicle_name:
            v_str = f"{vehicle_name} (ID: {vehicle_id})" if vehicle_id else str(vehicle_name)
            lines.append(f"• *Vehicle*: {v_str}")
        if vehicle_category:
            lines.append(f"• *Category*: {vehicle_category}")
        if provider:
            lines.append(f"• *Provider*: {provider}")

        # For Airport Stationed Services (Meet & Greet / Lounge):
        # Always output "• *Airport / Location*: {loc}"
        if department == Department.DEPARTMENT_1 or service_label in ("Meet & Greet", "Lounge"):
            loc = airport or origin
            if loc:
                lines.append(f"• *Airport / Location*: {loc}")
        # For Travel / Route-based services (Ticketing, Round Trip, Charter, Transport):
        # Output "• *Route*: {origin} → {dest}" when destination is present
        elif dest and dest != origin and dest != airport:
            lines.append(f"• *Route*: {origin} → {dest}")
        elif airport or origin:
            lines.append(f"• *Airport / Location*: {airport or origin}")

        if has_flight:
            terminal_info = f" (Terminal {terminal})" if terminal else ""
            lines.append(f"• *Flight*: {flight_num}{terminal_info}")

        if outbound_flight or return_flight:
            f_parts = [f for f in (outbound_flight, return_flight) if f]
            lines.append(f"• *Round Trip Flights*: {' / '.join(f_parts)}")

        lines.append(f"• *Date / Time*: {date_time}")

        if outbound_date or return_date:
            d_parts = [d for d in (outbound_date, return_date) if d]
            lines.append(f"• *Round Trip Dates*: {' → '.join(d_parts)}")

        lines.append(f"• *Booking Value*: {amount_str}")

        if ref_price is not None and str(ref_price).strip() and str(ref_price) != "0":
            try:
                num_ref = float(ref_price)
                ref_str = f"₹{num_ref:,.2f}" if num_ref > 0 else str(ref_price)
            except (ValueError, TypeError):
                ref_str = str(ref_price)
            lines.append(f"• *Reference Rate*: {ref_str} (REFERENCE PRICING — NOT FINAL QUOTE)")

        lines.append(f"• *Status*: {status}")

        if notes and str(notes).strip():
            clean_notes = str(notes).strip().replace("\n", " ")
            lines.append(f"• *Notes*: {clean_notes}")

        return "\n".join(lines)
    @staticmethod
    def _display_when(value: Any, airport_code: Optional[str] = None) -> str:
        """Renders a stored date/time as a clean, local value for staff messages."""
        if value is None:
            return ""
        from datetime import datetime
        if isinstance(value, datetime):
            dt = value
        else:
            text = str(value).strip()
            if not text:
                return ""
            if len(text) == 10 and text[4] == "-" and text[7] == "-":
                return text
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
            except (ValueError, TypeError):
                return text[:40]

        try:
            from zoneinfo import ZoneInfo
            from app.services.booking_cutoff import lookup_airport_timezone

            tz_name = lookup_airport_timezone(None, airport_code) if airport_code else "Asia/Kolkata"
            target_tz = ZoneInfo(tz_name)
            if dt.tzinfo is not None:
                dt = dt.astimezone(target_tz)
        except Exception:
            pass

        return dt.strftime("%d %b %Y | %H:%M")

    @classmethod
    def format_booking_notification_summary(
        cls,
        context: Dict[str, Any],
        department: Department,
        service_label: str,
    ) -> str:
        """A compact, scannable line for the approved operations template.

        The full formatted alert is kept for the free-form path; the template
        wrapper already supplies the greeting and call to action, so this value
        carries only the facts an operator needs at a glance.
        """
        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "N/A").strip()
        customer_name = str(context.get("passenger_name") or context.get("passengerName") or "").strip()
        customer_phone = str(context.get("passenger_phone") or context.get("passengerPhone") or "").strip()
        service_type = str(
            context.get("service_name")
            or context.get("service_type")
            or context.get("package")
            or ""
        ).strip()
        airport = str(
            context.get("airport_code")
            or context.get("origin_code")
            or context.get("origin")
            or ""
        ).strip()
        origin = str(context.get("origin_code") or context.get("origin") or airport).strip()
        dest = str(context.get("dest_code") or context.get("destination") or "").strip()
        flight_num = str(context.get("flight_num") or context.get("flightNum") or "").strip()
        journey_type_val = str(context.get("journey_type") or "").strip().upper()
        raw_service_clock = (
            (context.get("arrival_time") if journey_type_val == "ARRIVAL" else None)
            or context.get("departure_time")
            or context.get("arrival_time")
            or context.get("service_date")
            or context.get("serviceDate")
        )
        date_time = cls._display_when(raw_service_clock, airport_code=airport or origin or dest)
        pax = context.get("passenger_count") or context.get("passengerCount") or 1
        status = str(context.get("status") or "PENDING").upper()

        raw_amount = context.get("total_amount") if context.get("total_amount") is not None else context.get("totalAmount")
        currency = str(context.get("currency") or "INR").upper()
        amount_text = "Quote"
        if raw_amount is not None:
            try:
                amount_value = float(raw_amount)
            except (TypeError, ValueError):
                amount_value = 0.0
            if amount_value > 0:
                amount_text = f"₹{amount_value:,.0f}" if currency == "INR" else f"{currency} {amount_value:,.0f}"

        is_enquiry = status in {"PENDING", "ENQUIRY", "NEW_BOOKING", "QUOTED"} or "enquir" in service_type.lower()
        parts: List[str] = ["NEW ENQUIRY" if is_enquiry else "NEW BOOKING", booking_ref]
        if service_type and service_label and service_label.lower() in service_type.lower():
            # "Ticketing · Air Ticketing" is redundant; the specific label is enough.
            parts.append(service_type)
        elif service_label or service_type:
            parts.append(
                f"{service_label} · {service_type}"
                if service_type and service_type.lower() != service_label.lower()
                else (service_label or service_type)
            )
        if customer_name and customer_name.upper() != "N/A":
            parts.append(customer_name)
        if customer_phone and customer_phone.upper() != "N/A":
            parts.append(customer_phone)
        route = f"{origin} → {dest}" if origin and dest and origin != dest else (airport or dest)
        if route and route.upper() != "N/A":
            parts.append(route)
        if date_time and date_time.upper() != "N/A":
            parts.append(date_time)
        if flight_num and flight_num.upper() != "N/A":
            parts.append(f"Flight {flight_num}")
        try:
            pax_value = int(pax)
        except (TypeError, ValueError):
            pax_value = 1
        if pax_value > 0:
            parts.append(f"{pax_value} pax")
        parts.append(amount_text)
        parts.append(status)
        return cls.to_template_parameter(" · ".join(part for part in parts if part))

    @classmethod
    def format_charter_notification(cls, charter_req: Any) -> str:
        """
        Formats a dedicated notification for Air Charter Requests.
        """
        ref = getattr(charter_req, "request_reference", "N/A")
        name = getattr(charter_req, "customer_name", "N/A")
        phone = f"+{getattr(charter_req, 'country_code', '')}{getattr(charter_req, 'phone', '')}".replace("++", "+")
        email = getattr(charter_req, "email", "N/A")
        trip_type = getattr(charter_req, "trip_type", "ONE_WAY")
        origin = getattr(charter_req, "origin", "N/A")
        destination = getattr(charter_req, "destination", "N/A")
        dep_date = getattr(charter_req, "departure_date", "N/A")
        dep_time = getattr(charter_req, "departure_time", "") or ""
        aircraft = getattr(charter_req, "aircraft_preference", None) or "Any Suitable Aircraft"
        passengers = getattr(charter_req, "passengers", {}) or {}
        pax_total = passengers.get("total", 1) if isinstance(passengers, dict) else 1
        special_requests = getattr(charter_req, "special_requests", None)

        lines = [
            "🛩️ *NEW AIR CHARTER REQUEST — Charter Desk*",
            "",
            f"• *Reference*: `{ref}`",
            f"• *Customer*: {name}",
            f"• *Contact*: {phone} | {email}",
            f"• *Trip Type*: {trip_type}",
            f"• *Route*: {origin} ➔ {destination}",
            f"• *Departure*: {dep_date} {dep_time}".strip(),
            f"• *Aircraft Preference*: {aircraft}",
            f"• *Passengers*: {pax_total}",
            f"• *Status*: REQUESTED",
        ]

        if special_requests and str(special_requests).strip():
            clean_notes = str(special_requests).strip().replace("\n", " ")
            if len(clean_notes) > 200:
                clean_notes = clean_notes[:197] + "..."
            lines.append(f"• *Special Requests*: {clean_notes}")

        return "\n".join(lines)
