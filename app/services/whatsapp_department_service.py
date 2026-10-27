"""
Service-Wise WhatsApp Notification Dispatcher for Shafsky Aviation.

Dispatches internal operational notifications for every booking and enquiry
to all 5 configured recipient numbers across all service categories:
- Meet & Greet, Lounge
- Ticketing, Transport, Hotels
- Air Charter, Cargo & Logistics, Special Services

Features:
- Stores 5 recipient numbers in environment variables (WHATSAPP_BOOKING_NOTIFY_PHONE_1..5).
- Automatically normalizes Indian 10-digit mobile numbers into international format (e.g. 919599087957).
- No recipient numbers are hardcoded in source code.
- Loops through all 5 recipients; failure of one recipient never blocks remaining numbers.
- Guarantees notification failures NEVER interrupt, cancel, or roll back a booking.
- Built-in idempotency deduplication prevents duplicate notifications on repeated events.
- Reuses the existing WhatsAppClient connection pool without modifying bot flows.
"""

import logging
import os
import re
import threading
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.integrations.whatsapp.client import whatsapp_client

logger = logging.getLogger("shafsky.whatsapp_department")


class Department(str, Enum):
    """Internal operations departments."""
    DEPARTMENT_1 = "Department 1 (Meet & Greet / Lounge)"
    DEPARTMENT_2 = "Department 2 (Ticketing / Transport / Hotels)"
    DEPARTMENT_3 = "Department 3 (Air Charter / Cargo / Special Services)"


class WhatsAppDepartmentService:
    """
    Evaluates service category from booking/enquiry payloads,
    resolves all 5 recipient numbers, applies idempotency checks,
    and dispatches structured notifications to all 5 recipients.
    """

    # In-memory idempotency fallback with thread lock
    _dispatched_events: Dict[str, float] = {}
    _lock = threading.Lock()
    IDEMPOTENCY_TTL_SECONDS: int = 86400  # 24 hours

    # Department 1 service keywords
    DEPT1_CATEGORIES = {
        "meet & greet",
        "meet and greet",
        "airport assistance",
        "airport services",
        "lounge",
        "airport lounge",
        "lounge access",
        "vip lounge",
        "fast track",
        "baggage assistance",
    }

    # Department 2 service keywords
    DEPT2_CATEGORIES = {
        "ticketing",
        "air ticketing",
        "flight ticket",
        "flight booking",
        "transport",
        "ground transport",
        "round trip",
        "round_trip",
        "roundtrip",
        "transportation services",
        "hotel & transportation",
        "hotel",
        "hotels",
        "hotel booking",
        "hotel services",
    }

    # Department 3 service keywords
    DEPT3_CATEGORIES = {
        "private charter",
        "air charter",
        "charter",
        "aircraft charter",
        "cargo & logistics",
        "cargo",
        "air cargo",
        "freight",
        "logistics",
        "special services",
        "special service",
        "medical assistance",
        "medical services",
        "travel support",
        "travel services",
    }

    @classmethod
    def clear_idempotency_cache(cls) -> None:
        """Utility for test suites to reset in-memory idempotency records."""
        with cls._lock:
            cls._dispatched_events.clear()

    @classmethod
    def _is_duplicate_and_claim(cls, idempotency_key: str) -> bool:
        """
        Atomically checks if an event has already been claimed/dispatched.
        Returns True if duplicate, False if this is the first time (and claims it).
        Uses Redis if available, with in-memory thread-safe fallback.
        """
        if not idempotency_key or not idempotency_key.strip():
            return False

        clean_key = idempotency_key.strip()

        # 1. Check Redis if available
        try:
            from app.core.redis import get_redis_client
            redis_client = get_redis_client()
            if redis_client is not None:
                redis_key = f"lock:wa_notify:{clean_key}"
                acquired = redis_client.set(redis_key, "dispatched", ex=cls.IDEMPOTENCY_TTL_SECONDS, nx=True)
                if not acquired:
                    return True
                return False
        except Exception as redis_err:
            logger.debug("[WhatsApp Idempotency] Redis check skipped/failed: %s", redis_err)

        # 2. In-memory fallback
        now = time.time()
        with cls._lock:
            # Clean up expired entries
            expired = [k for k, ts in cls._dispatched_events.items() if now - ts > cls.IDEMPOTENCY_TTL_SECONDS]
            for k in expired:
                del cls._dispatched_events[k]

            if clean_key in cls._dispatched_events:
                return True
            cls._dispatched_events[clean_key] = now
            return False

    @classmethod
    def normalize_phone_number(cls, raw: Optional[str]) -> Optional[str]:
        """
        Normalizes a phone number to international E.164 digits-only format without leading plus.
        Specifically converts 10-digit Indian mobile numbers (e.g. 9599087957 -> 919599087957).
        """
        cleaned = re.sub(r"\D", "", str(raw or "").strip())
        if not cleaned:
            return None

        # Strip leading trunk prefix 0 (e.g. 09599087957 -> 9599087957)
        if cleaned.startswith("0") and len(cleaned) == 11:
            cleaned = cleaned[1:]

        # If 10 digits (standard Indian mobile starting with 6, 7, 8, or 9), prepend country code 91
        if len(cleaned) == 10:
            cleaned = f"91{cleaned}"

        if len(cleaned) >= 10:
            return cleaned
        return None

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
        for var in env_vars:
            val = getattr(settings, var, None) or os.getenv(var, "")
            norm = cls.normalize_phone_number(val)
            if norm and norm not in recipients:
                recipients.append(norm)

        # Fallback if none of the 5 are configured
        if not recipients:
            legacy_officer = getattr(settings, "WHATSAPP_OFFICER_NOTIFY_PHONE", None) or os.getenv("WHATSAPP_OFFICER_NOTIFY_PHONE", "")
            norm = cls.normalize_phone_number(legacy_officer)
            if norm:
                recipients.append(norm)

        return recipients

    @classmethod
    def _mask_phone(cls, phone: str) -> str:
        """Masks phone number for safe logging."""
        cleaned = re.sub(r"\D", "", phone or "")
        if len(cleaned) > 6:
            return f"{cleaned[:3]}****{cleaned[-3:]}"
        return cleaned

    @classmethod
    def resolve_department(
        cls,
        service_category: Optional[str] = None,
        service_type: Optional[str] = None,
        raw_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Department, str]:
        """
        Determines the service category and canonical label based on verified
        payload data.
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
        """
        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "N/A").strip()
        customer_name = str(context.get("passenger_name") or context.get("passengerName") or "N/A").strip()
        customer_phone = str(context.get("passenger_phone") or context.get("passengerPhone") or "N/A").strip()
        customer_email = str(context.get("passenger_email") or context.get("passengerEmail") or "N/A").strip()
        pax_count = context.get("passenger_count") or context.get("passengerCount") or 1

        service_type = str(
            context.get("service_name")
            or context.get("service_type")
            or context.get("package")
            or service_label
        ).strip()

        airport = (
            context.get("airport_code")
            or context.get("origin_code")
            or context.get("origin")
            or "N/A"
        )
        dest = context.get("dest_code") or context.get("destination")
        flight_num = context.get("flight_num") or context.get("flightNum") or "N/A"
        date_time = (
            context.get("departure_time")
            or context.get("service_date")
            or context.get("serviceDate")
            or "N/A"
        )
        terminal = context.get("terminal")

        # Amount & Status
        raw_amount = context.get("total_amount") if context.get("total_amount") is not None else context.get("totalAmount")
        currency = context.get("currency") or "INR"
        status = str(context.get("status") or "PENDING").upper()

        if raw_amount is not None and float(raw_amount) > 0:
            currency_symbol = "₹" if currency.upper() == "INR" else f"{currency} "
            amount_str = f"{currency_symbol}{float(raw_amount):,.2f}"
        else:
            amount_str = "Quotation Required (₹0 Enquiry)"

        # Notes
        notes = (
            context.get("notes")
            or (context.get("details") or {}).get("additional_requirements")
            or (context.get("details") or {}).get("notes")
        )

        # Department headers
        dept_headers = {
            Department.DEPARTMENT_1: "✈️ *NEW BOOKING / ENQUIRY — Meet & Greet / Lounge*",
            Department.DEPARTMENT_2: "🚗 *NEW BOOKING / ENQUIRY — Ticketing / Transport / Hotels*",
            Department.DEPARTMENT_3: "🛩️ *NEW SERVICE ENQUIRY — Air Charter / Cargo / Special Services*",
        }
        header = dept_headers.get(department, "🚨 *NEW SERVICE NOTIFICATION*")

        lines = [
            header,
            "",
            f"• *Reference*: `{booking_ref}`",
            f"• *Service*: {service_label} — {service_type}",
            f"• *Customer*: {customer_name}",
            f"• *Contact*: {customer_phone} | {customer_email}",
            f"• *Guests / Pax*: {pax_count}",
        ]

        if dest and dest != airport:
            lines.append(f"• *Route*: {airport} ➔ {dest}")
        else:
            lines.append(f"• *Airport / Location*: {airport}")

        if flight_num and flight_num.upper() != "N/A":
            terminal_info = f" (Terminal {terminal})" if terminal else ""
            lines.append(f"• *Flight*: {flight_num}{terminal_info}")

        lines.append(f"• *Date / Time*: {date_time}")
        lines.append(f"• *Booking Value*: {amount_str}")
        lines.append(f"• *Status*: {status}")

        if notes and str(notes).strip():
            clean_notes = str(notes).strip().replace("\n", " ")
            if len(clean_notes) > 200:
                clean_notes = clean_notes[:197] + "..."
            lines.append(f"• *Notes*: {clean_notes}")

        return "\n".join(lines)

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

    @classmethod
    def dispatch_booking_or_enquiry_notification(cls, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluates context, performs idempotency check, and dispatches WhatsApp alert
        to all 5 configured recipient numbers. Continues on failure of any single number.
        Guaranteed to catch all exceptions and never raise.
        """
        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "N/A").strip()

        try:
            # 1. Idempotency Check: prevent duplicate notifications
            idempotency_key = f"wa_booking_notify:{booking_ref}"
            if cls._is_duplicate_and_claim(idempotency_key):
                logger.info(
                    "[WhatsApp Notify] Notification for booking %s was already dispatched. Skipping duplicate.",
                    booking_ref,
                )
                return {
                    "status": "SKIPPED",
                    "reason": "already_dispatched",
                    "booking_ref": booking_ref,
                }

            # 2. Resolve recipients
            recipients = cls.get_recipient_phone_numbers()
            if not recipients:
                logger.warning(
                    "[WhatsApp Notify] Notification skipped for %s: no recipient phones configured.",
                    booking_ref,
                )
                return {
                    "status": "BYPASSED",
                    "reason": "no_recipients_configured",
                    "booking_ref": booking_ref,
                }

            # 3. Check WhatsApp client configuration
            if not whatsapp_client.is_configured():
                logger.warning(
                    "[WhatsApp Notify] Notification skipped for %s: WhatsApp Cloud API is not configured.",
                    booking_ref,
                )
                return {
                    "status": "BYPASSED",
                    "reason": "whatsapp_client_unconfigured",
                    "booking_ref": booking_ref,
                }

            # 4. Resolve service category and format message
            category = context.get("service_category") or context.get("serviceCategory")
            service_type = context.get("service_type") or context.get("serviceType")
            dept, service_label = cls.resolve_department(category, service_type, context)
            message_body = cls.format_booking_notification(context, dept, service_label)

            # Optional template override if configured
            template_name = (
                getattr(settings, "WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE", None)
                or os.getenv("WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE", "")
            ).strip()

            # 5. Dispatch to ALL configured recipients (continue if any individual number fails)
            recipient_results: List[Dict[str, Any]] = []
            successful_count = 0

            for phone in recipients:
                masked = cls._mask_phone(phone)
                try:
                    if template_name:
                        res = whatsapp_client.send_message(
                            to_phone=phone,
                            template_name=template_name,
                            template_components=[
                                {
                                    "type": "body",
                                    "parameters": [{"type": "text", "text": message_body}],
                                }
                            ],
                        )
                    else:
                        res = whatsapp_client.send_text_message(phone, message_body)

                    if res.get("success"):
                        successful_count += 1
                        recipient_results.append({
                            "phone": masked,
                            "status": "DELIVERED",
                            "message_id": res.get("message_id"),
                        })
                    else:
                        logger.error(
                            "[WhatsApp Notify] Delivery failed for %s to %s: %s",
                            booking_ref,
                            masked,
                            res.get("error"),
                        )
                        recipient_results.append({
                            "phone": masked,
                            "status": "FAILED",
                            "error": res.get("error"),
                        })
                except Exception as item_err:
                    logger.error(
                        "[WhatsApp Notify] Exception during send for %s to %s: %s",
                        booking_ref,
                        masked,
                        item_err,
                    )
                    recipient_results.append({
                        "phone": masked,
                        "status": "FAILED",
                        "error": str(item_err),
                    })

            overall_status = "DELIVERED" if successful_count == len(recipients) else (
                "PARTIALLY_DELIVERED" if successful_count > 0 else "FAILED"
            )

            logger.info(
                "[WhatsApp Notify] %s notification completed for %s: %d/%d delivered. Status: %s",
                service_label,
                booking_ref,
                successful_count,
                len(recipients),
                overall_status,
            )

            return {
                "status": overall_status,
                "service_label": service_label,
                "booking_ref": booking_ref,
                "recipients_attempted": len(recipients),
                "recipients_delivered": successful_count,
                "recipient_results": recipient_results,
            }

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "[WhatsApp Notify] Unexpected exception during dispatch for %s: %s",
                booking_ref,
                str(exc),
            )
            return {
                "status": "FAILED",
                "error": str(exc),
            }

    @classmethod
    def dispatch_charter_notification(cls, charter_req: Any) -> Dict[str, Any]:
        """
        Dispatches Air Charter notification to all 5 configured recipient numbers.
        Applies idempotency deduplication and continues on failure of any single number.
        Never raises: fails gracefully with error logging.
        """
        ref = getattr(charter_req, "request_reference", "N/A")

        try:
            # 1. Idempotency Check
            idempotency_key = f"wa_charter_notify:{ref}"
            if cls._is_duplicate_and_claim(idempotency_key):
                logger.info(
                    "[WhatsApp Notify] Notification for charter %s was already dispatched. Skipping duplicate.",
                    ref,
                )
                return {
                    "status": "SKIPPED",
                    "reason": "already_dispatched",
                    "request_reference": ref,
                }

            # 2. Resolve recipients
            recipients = cls.get_recipient_phone_numbers()
            if not recipients:
                logger.warning(
                    "[WhatsApp Notify] Charter notification skipped for %s: no recipient phones configured.",
                    ref,
                )
                return {
                    "status": "BYPASSED",
                    "reason": "no_recipients_configured",
                    "request_reference": ref,
                }

            # 3. Check WhatsApp client configuration
            if not whatsapp_client.is_configured():
                logger.warning(
                    "[WhatsApp Notify] Charter notification skipped for %s: WhatsApp Cloud API is not configured.",
                    ref,
                )
                return {
                    "status": "BYPASSED",
                    "reason": "whatsapp_client_unconfigured",
                    "request_reference": ref,
                }

            # 4. Format message body
            message_body = cls.format_charter_notification(charter_req)

            template_name = (
                getattr(settings, "WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE", None)
                or os.getenv("WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE", "")
            ).strip()

            # 5. Dispatch to ALL configured recipients
            recipient_results: List[Dict[str, Any]] = []
            successful_count = 0

            for phone in recipients:
                masked = cls._mask_phone(phone)
                try:
                    if template_name:
                        res = whatsapp_client.send_message(
                            to_phone=phone,
                            template_name=template_name,
                            template_components=[
                                {
                                    "type": "body",
                                    "parameters": [{"type": "text", "text": message_body}],
                                }
                            ],
                        )
                    else:
                        res = whatsapp_client.send_text_message(phone, message_body)

                    if res.get("success"):
                        successful_count += 1
                        recipient_results.append({
                            "phone": masked,
                            "status": "DELIVERED",
                            "message_id": res.get("message_id"),
                        })
                    else:
                        logger.error(
                            "[WhatsApp Notify] Charter delivery failed for %s to %s: %s",
                            ref,
                            masked,
                            res.get("error"),
                        )
                        recipient_results.append({
                            "phone": masked,
                            "status": "FAILED",
                            "error": res.get("error"),
                        })
                except Exception as item_err:
                    logger.error(
                        "[WhatsApp Notify] Exception during charter send for %s to %s: %s",
                        ref,
                        masked,
                        item_err,
                    )
                    recipient_results.append({
                        "phone": masked,
                        "status": "FAILED",
                        "error": str(item_err),
                    })

            overall_status = "DELIVERED" if successful_count == len(recipients) else (
                "PARTIALLY_DELIVERED" if successful_count > 0 else "FAILED"
            )

            logger.info(
                "[WhatsApp Notify] Air Charter notification completed for %s: %d/%d delivered. Status: %s",
                ref,
                successful_count,
                len(recipients),
                overall_status,
            )

            return {
                "status": overall_status,
                "request_reference": ref,
                "recipients_attempted": len(recipients),
                "recipients_delivered": successful_count,
                "recipient_results": recipient_results,
            }

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "[WhatsApp Notify] Unexpected exception during charter dispatch for %s: %s",
                ref,
                str(exc),
            )
            return {
                "status": "FAILED",
                "error": str(exc),
            }
