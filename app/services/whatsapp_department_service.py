"""
Service-Wise WhatsApp Notification Dispatcher for Shafsky Aviation.

Dispatches internal operational notifications for every booking and enquiry
to all 5 configured recipient numbers across all 8 service categories:
- Meet & Greet
- Lounge
- Ticketing
- Transport
- Hotels
- Air Charter
- Cargo & Logistics
- Special Services

Features:
- Stores 5 recipient numbers in environment variables (WHATSAPP_BOOKING_NOTIFY_PHONE_1..5).
- Automatically normalizes Indian 10-digit mobile numbers into international format (e.g. 919599087957).
- No recipient numbers or credentials are hardcoded in source code.
- Loops through all 5 recipients; failure of one recipient never blocks remaining numbers.
- Guarantees notification failures NEVER interrupt, cancel, or roll back a booking or enquiry.
- Two-phase idempotency lifecycle (in-progress mutex -> durable completion) prevents duplicate sends
  without permanently suppressing retries on temporary API/network failures.
- PostgreSQL NotificationRecord persistence provides durable delivery history across Redis restarts.
- Distinguishes Meta API status accurately: ACCEPTED (with verified message_id), UNKNOWN (timeouts), and FAILED.
- Strict 1024-character Meta template parameter truncation to prevent Meta Parameter Length rejections.
- Configurable template language (WHATSAPP_DEPARTMENT_TEMPLATE_LANG, default en_US).
- Actionable logging for Meta error 131047 (24-hour messaging window enforcement).
- Reuses existing WhatsAppClient connection pool without modifying customer bot flows.
"""

import hashlib
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
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
    resolves all 5 recipient numbers, applies robust idempotency lifecycle checks,
    and dispatches structured notifications to all 5 recipients.
    """

    # In-memory idempotency state with thread lock: dict of key -> {"state": str, "expires": float}
    _dispatched_events: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()

    IDEMPOTENCY_LOCK_TTL_SECONDS: int = 120  # In-progress concurrency mutex
    IDEMPOTENCY_DISPATCHED_TTL_SECONDS: int = 86400  # 24 hours completed state
    IDEMPOTENCY_TIMEOUT_HOLD_SECONDS: int = 300  # 5 minutes hold on ambiguous timeout

    MAX_TEMPLATE_PARAM_CHARS: int = 1000  # Strictly below Meta Cloud API 1024-character limit

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
        "travel support",
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
        "travel services",
    }

    @classmethod
    def clear_idempotency_cache(cls) -> None:
        """Utility for test suites to reset in-memory idempotency records."""
        with cls._lock:
            cls._dispatched_events.clear()

    @classmethod
    def generate_idempotency_key(
        cls,
        entity_prefix: str,
        identifier: Optional[str],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generates a stable, collision-free event identifier.
        Uses reference if valid; otherwise creates a deterministic hash of core booking details
        so separate anonymous/unreferenced bookings never collide.
        """
        clean_id = (identifier or "").strip()
        if clean_id and clean_id.upper() != "N/A":
            return f"wa_{entity_prefix}_notify:{clean_id}"

        ctx = context or {}
        parts = [
            str(ctx.get("passenger_email") or ctx.get("email") or ""),
            str(ctx.get("passenger_phone") or ctx.get("phone") or ""),
            str(ctx.get("service_category") or ctx.get("trip_type") or ""),
            str(ctx.get("service_type") or ctx.get("origin") or ""),
            str(ctx.get("departure_time") or ctx.get("departure_date") or ""),
        ]
        digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:16]
        return f"wa_{entity_prefix}_notify:anon_{digest}"

    @classmethod
    def claim_idempotency(cls, idempotency_key: str) -> str:
        """
        Atomically checks and claims an event for dispatch.
        Returns:
            - 'ACQUIRED': Successfully acquired in-progress lock. Proceed to dispatch.
            - 'DUPLICATE': Event was already successfully dispatched within 24 hours.
            - 'IN_PROGRESS': Another concurrent request is actively dispatching right now.
            - 'UNKNOWN_TIMEOUT': Event recently timed out; held to avoid blind duplicate sending.
        """
        if not idempotency_key or not idempotency_key.strip():
            return "ACQUIRED"

        clean_key = idempotency_key.strip()

        # 1. Check PostgreSQL NotificationRecord persistence for durable completed record
        try:
            from app.database import SessionLocal
            from app.models.schema import NotificationRecord, NotificationStatus
            from sqlalchemy import select
            with SessionLocal() as db:
                existing = db.scalar(
                    select(NotificationRecord)
                    .where(NotificationRecord.template_type == "WHATSAPP_OPS_NOTIFICATION")
                    .where(NotificationRecord.payload["idempotency_key"].astext == clean_key)
                    .where(NotificationRecord.status.in_([NotificationStatus.COMPLETED, NotificationStatus.DELIVERED]))
                )
                if existing:
                    return "DUPLICATE"
        except Exception as db_err:
            logger.debug("[WhatsApp Idempotency] DB persistence check skipped: %s", db_err)

        # 2. Check Redis if available
        try:
            from app.core.redis import get_redis_client
            redis_client = get_redis_client()
            if redis_client is not None:
                redis_key = f"lock:{clean_key}"
                val = redis_client.get(redis_key)
                if val:
                    val_str = val.decode("utf-8") if isinstance(val, bytes) else val
                    if val_str == "dispatched":
                        return "DUPLICATE"
                    if val_str == "in_progress":
                        return "IN_PROGRESS"
                    if val_str == "unknown_timeout":
                        return "UNKNOWN_TIMEOUT"

                # Acquire atomic in-progress lock
                acquired = redis_client.set(redis_key, "in_progress", ex=cls.IDEMPOTENCY_LOCK_TTL_SECONDS, nx=True)
                if not acquired:
                    return "IN_PROGRESS"
                return "ACQUIRED"
        except Exception as redis_err:
            logger.debug("[WhatsApp Idempotency] Redis check skipped: %s", redis_err)

        # 3. Thread-safe in-memory fallback
        now = time.time()
        with cls._lock:
            # Purge expired
            expired = [k for k, item in cls._dispatched_events.items() if now > item.get("expires", 0)]
            for k in expired:
                del cls._dispatched_events[k]

            entry = cls._dispatched_events.get(clean_key)
            if entry:
                state = entry.get("state")
                if state == "dispatched":
                    return "DUPLICATE"
                if state == "in_progress":
                    return "IN_PROGRESS"
                if state == "unknown_timeout":
                    return "UNKNOWN_TIMEOUT"

            cls._dispatched_events[clean_key] = {
                "state": "in_progress",
                "expires": now + cls.IDEMPOTENCY_LOCK_TTL_SECONDS,
            }
            return "ACQUIRED"

    @classmethod
    def finalize_idempotency(
        cls,
        idempotency_key: str,
        outcome: str,
        recipient_results: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Finalizes idempotency state based on dispatch outcome:
        - ACCEPTED / PARTIALLY_ACCEPTED: Upgrades lock to 'dispatched' (24h TTL) and records in DB.
        - UNKNOWN (timeout): Sets 'unknown_timeout' hold (300s TTL) to prevent blind resending.
        - FAILED (non-timeout error): Deletes lock so the notification can be retried cleanly.
        """
        if not idempotency_key or not idempotency_key.strip():
            return

        clean_key = idempotency_key.strip()
        is_successful = outcome in ("ACCEPTED", "PARTIALLY_ACCEPTED", "DELIVERED", "PARTIALLY_DELIVERED")
        is_unknown = outcome == "UNKNOWN"

        now = time.time()

        # Update Redis
        try:
            from app.core.redis import get_redis_client
            redis_client = get_redis_client()
            if redis_client is not None:
                redis_key = f"lock:{clean_key}"
                if is_successful:
                    redis_client.set(redis_key, "dispatched", ex=cls.IDEMPOTENCY_DISPATCHED_TTL_SECONDS)
                elif is_unknown:
                    redis_client.set(redis_key, "unknown_timeout", ex=cls.IDEMPOTENCY_TIMEOUT_HOLD_SECONDS)
                else:
                    redis_client.delete(redis_key)
        except Exception as redis_err:
            logger.debug("[WhatsApp Idempotency] Redis finalize skipped: %s", redis_err)

        # Update in-memory fallback
        with cls._lock:
            if is_successful:
                cls._dispatched_events[clean_key] = {
                    "state": "dispatched",
                    "expires": now + cls.IDEMPOTENCY_DISPATCHED_TTL_SECONDS,
                }
            elif is_unknown:
                cls._dispatched_events[clean_key] = {
                    "state": "unknown_timeout",
                    "expires": now + cls.IDEMPOTENCY_TIMEOUT_HOLD_SECONDS,
                }
            else:
                cls._dispatched_events.pop(clean_key, None)

        # Durable PostgreSQL DB persistence on success or recorded failure
        try:
            from app.database import SessionLocal
            from app.models.schema import NotificationRecord, NotificationStatus
            with SessionLocal() as db:
                accepted_ids = [r.get("message_id") for r in recipient_results if r.get("message_id")]
                msg_ids_str = ",".join(str(i) for i in accepted_ids) if accepted_ids else None
                rec_status = NotificationStatus.COMPLETED if is_successful else (
                    NotificationStatus.PENDING if is_unknown else NotificationStatus.FAILED
                )
                note = NotificationRecord(
                    recipient_email=str((context or {}).get("passenger_email") or (context or {}).get("email") or "ops@shafsky.aero"),
                    recipient_phone="ALL_5_RECIPIENTS",
                    template_type="WHATSAPP_OPS_NOTIFICATION",
                    channel="WHATSAPP_OPS",
                    payload={
                        "idempotency_key": clean_key,
                        "outcome": outcome,
                        "recipient_results": recipient_results,
                        "context_summary": {
                            "booking_ref": (context or {}).get("booking_ref"),
                            "service_category": (context or {}).get("service_category"),
                        },
                    },
                    status=rec_status,
                    message_id=msg_ids_str,
                    delivered_at=datetime.now(timezone.utc) if is_successful else None,
                )
                db.add(note)
                db.commit()
        except Exception as db_err:
            logger.debug("[WhatsApp Idempotency] DB persistence save skipped: %s", db_err)

    @classmethod
    def normalize_phone_number(cls, raw: Optional[str]) -> Optional[str]:
        """
        Normalizes a phone number to international E.164 digits-only format without leading plus.
        Specifically converts 10-digit Indian mobile numbers (e.g. 9599087957 -> 919599087957)
        without duplicating the country code if already present.
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
            val = getattr(settings, var, None)
            if val is None:
                val = os.getenv(var, "")
            norm = cls.normalize_phone_number(val)
            if norm and norm not in recipients:
                recipients.append(norm)

        # Fallback if none of the 5 are configured
        if not recipients:
            legacy_officer = getattr(settings, "WHATSAPP_OFFICER_NOTIFY_PHONE", None)
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
    def build_template_components(cls, message_body: str) -> List[Dict[str, Any]]:
        """
        Builds Meta-compliant template components.
        Ensures all parameters strictly stay under Meta's 1024-character maximum.
        """
        param_text = cls.sanitize_template_param(message_body)
        return [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": param_text}
                ],
            }
        ]

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

        # Department and service-specific headers
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

        details = context.get("details") if isinstance(context.get("details"), dict) else {}
        options = context.get("service_options") if isinstance(context.get("service_options"), dict) else {}

        # Resolve passenger count (prefer details if present)
        final_pax = (
            details.get("passenger_count")
            or details.get("passengers")
            or pax_count
        )

        lines = [
            header,
            "",
            f"• *Reference*: `{booking_ref}`",
            f"• *Service*: {service_label} — {service_type}",
            f"• *Customer*: {customer_name}",
            f"• *Contact*: {customer_phone} | {customer_email}",
            f"• *Guests / Pax / Passengers*: {final_pax}",
        ]

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

        if vehicle_name:
            lines.append(f"• *Vehicle*: {vehicle_name}")
        if vehicle_id:
            lines.append(f"• *Vehicle ID*: {vehicle_id}")
        if vehicle_category:
            lines.append(f"• *Category*: {vehicle_category}")
        if provider:
            lines.append(f"• *Provider*: {provider}")

        if dest and dest != airport:
            lines.append(f"• *Route*: {airport} ➔ {dest}")
        else:
            lines.append(f"• *Airport / Location*: {airport}")

        if flight_num and flight_num.upper() != "N/A":
            terminal_info = f" (Terminal {terminal})" if terminal else ""
            lines.append(f"• *Flight*: {flight_num}{terminal_info}")

        if outbound_flight or return_flight:
            f_parts = [f for f in (outbound_flight, return_flight) if f]
            lines.append(f"• *Round Trip Flights*: {' / '.join(f_parts)}")

        lines.append(f"• *Date / Time*: {date_time}")

        if outbound_date or return_date:
            d_parts = [d for d in (outbound_date, return_date) if d]
            lines.append(f"• *Round Trip Dates*: {' ➔ '.join(d_parts)}")

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
        Evaluates context, performs two-phase idempotency check, and dispatches WhatsApp alert
        to all 5 configured recipient numbers. Continues on failure of any single number.
        Accurately distinguishes ACCEPTED, FAILED, and UNKNOWN delivery states.
        Guaranteed to catch all exceptions and never raise or crash booking creation.
        """
        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "N/A").strip()
        idempotency_key = cls.generate_idempotency_key("booking", booking_ref, context)

        try:
            # 1. Two-Phase Idempotency Check: prevent duplicate notifications
            claim_status = cls.claim_idempotency(idempotency_key)
            if claim_status in ("DUPLICATE", "IN_PROGRESS", "UNKNOWN_TIMEOUT"):
                logger.info(
                    "[WhatsApp Notify] Notification for booking %s skipped (idempotency state: %s).",
                    booking_ref,
                    claim_status,
                )
                return {
                    "status": "SKIPPED",
                    "reason": claim_status.lower(),
                    "booking_ref": booking_ref,
                }

            # 2. Resolve recipients
            recipients = cls.get_recipient_phone_numbers()
            if not recipients:
                logger.warning(
                    "[WhatsApp Notify] Notification skipped for %s: no recipient phones configured.",
                    booking_ref,
                )
                cls.finalize_idempotency(idempotency_key, "FAILED", [], context)
                return {
                    "status": "BYPASSED",
                    "reason": "no_recipients_configured",
                    "booking_ref": booking_ref,
                }

            # 3. Check WhatsApp client configuration
            if not whatsapp_client.is_configured():
                logger.warning(
                    "[WhatsApp Notify] Notification skipped for %s: WhatsApp Cloud API credentials not configured.",
                    booking_ref,
                )
                cls.finalize_idempotency(idempotency_key, "FAILED", [], context)
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

            template_name = getattr(settings, "WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE", None)
            if template_name is None:
                template_name = os.getenv("WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE", "")
            template_name = (template_name or "").strip()

            template_lang = getattr(settings, "WHATSAPP_DEPARTMENT_TEMPLATE_LANG", None)
            if template_lang is None:
                template_lang = os.getenv("WHATSAPP_DEPARTMENT_TEMPLATE_LANG", "en_US")
            template_lang = (template_lang or "en_US").strip()

            if not template_name:
                logger.warning(
                    "[WhatsApp Notify] WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE is not set. "
                    "Falling back to freeform text. NOTE: Meta Cloud API requires an approved template "
                    "when messaging recipients outside their 24-hour customer window (Error 131047)."
                )

            # 5. Dispatch to ALL configured recipients (continue if any individual number fails)
            recipient_results: List[Dict[str, Any]] = []
            successful_count = 0
            timeout_count = 0

            for phone in recipients:
                masked = cls._mask_phone(phone)
                try:
                    if template_name:
                        components = cls.build_template_components(message_body)
                        res = whatsapp_client.send_message(
                            to_phone=phone,
                            template_name=template_name,
                            template_language=template_lang,
                            template_components=components,
                        )
                    else:
                        res = whatsapp_client.send_text_message(phone, message_body)

                    msg_id = res.get("message_id")
                    is_success = res.get("success") is True
                    res_status = str(res.get("status") or "").lower()
                    res_err = str(res.get("error") or "")

                    # Verify Meta API response confirmed acceptance with message identifier
                    if is_success and msg_id:
                        successful_count += 1
                        recipient_results.append({
                            "phone": phone,
                            "masked_phone": masked,
                            "status": "ACCEPTED",
                            "message_id": msg_id,
                        })
                    elif res_status == "timeout_unverified" or "timeout" in res_err.lower():
                        timeout_count += 1
                        recipient_results.append({
                            "phone": phone,
                            "masked_phone": masked,
                            "status": "UNKNOWN",
                            "error": res.get("error") or "Timeout connecting to Meta WhatsApp API; delivery state unknown.",
                        })
                    else:
                        if "131047" in res_err:
                            logger.warning(
                                "[WhatsApp Notify] Meta rejected message to %s (Error 131047): "
                                "Recipient is outside 24h messaging window. Configure WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE with an approved Meta template.",
                                masked,
                            )
                        recipient_results.append({
                            "phone": phone,
                            "masked_phone": masked,
                            "status": "FAILED",
                            "error": res.get("error") or "Missing message identifier from Meta API",
                        })

                except Exception as item_err:
                    logger.error(
                        "[WhatsApp Notify] Exception during send for %s to %s: %s",
                        booking_ref,
                        masked,
                        item_err,
                    )
                    recipient_results.append({
                        "phone": phone,
                        "masked_phone": masked,
                        "status": "FAILED",
                        "error": str(item_err),
                    })

            # Determine overall outcome
            if successful_count == len(recipients):
                overall_status = "ACCEPTED"
            elif successful_count > 0:
                overall_status = "PARTIALLY_ACCEPTED"
            elif timeout_count > 0:
                overall_status = "UNKNOWN"
            else:
                overall_status = "FAILED"

            # Finalize idempotency state and persist durable record
            cls.finalize_idempotency(idempotency_key, overall_status, recipient_results, context)

            logger.info(
                "[WhatsApp Notify] %s notification completed for %s: %d/%d accepted by Meta. Status: %s",
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
                "recipients_accepted": successful_count,
                # Backward-compatible alias for existing consumers
                "recipients_delivered": successful_count,
                "recipient_results": recipient_results,
            }

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "[WhatsApp Notify] Unexpected exception during dispatch for %s: %s",
                booking_ref,
                str(exc),
            )
            cls.finalize_idempotency(idempotency_key, "FAILED", [], context)
            return {
                "status": "FAILED",
                "error": str(exc),
            }

    @classmethod
    def dispatch_charter_notification(cls, charter_req: Any) -> Dict[str, Any]:
        """
        Dispatches Air Charter notification to all 5 configured recipient numbers.
        Applies robust two-phase idempotency deduplication and error isolation.
        Accurately distinguishes ACCEPTED, FAILED, and UNKNOWN delivery states.
        Never raises: fails gracefully with error logging.
        """
        ref = getattr(charter_req, "request_reference", "N/A")
        charter_dict = {
            "request_reference": ref,
            "customer_name": getattr(charter_req, "customer_name", "N/A"),
            "email": getattr(charter_req, "email", "N/A"),
            "phone": getattr(charter_req, "phone", "N/A"),
            "trip_type": getattr(charter_req, "trip_type", "ONE_WAY"),
            "origin": getattr(charter_req, "origin", "N/A"),
            "destination": getattr(charter_req, "destination", "N/A"),
            "departure_date": getattr(charter_req, "departure_date", "N/A"),
        }
        idempotency_key = cls.generate_idempotency_key("charter", ref, charter_dict)

        try:
            # 1. Two-Phase Idempotency Check
            claim_status = cls.claim_idempotency(idempotency_key)
            if claim_status in ("DUPLICATE", "IN_PROGRESS", "UNKNOWN_TIMEOUT"):
                logger.info(
                    "[WhatsApp Notify] Notification for charter %s skipped (idempotency state: %s).",
                    ref,
                    claim_status,
                )
                return {
                    "status": "SKIPPED",
                    "reason": claim_status.lower(),
                    "request_reference": ref,
                }

            # 2. Resolve recipients
            recipients = cls.get_recipient_phone_numbers()
            if not recipients:
                logger.warning(
                    "[WhatsApp Notify] Charter notification skipped for %s: no recipient phones configured.",
                    ref,
                )
                cls.finalize_idempotency(idempotency_key, "FAILED", [], charter_dict)
                return {
                    "status": "BYPASSED",
                    "reason": "no_recipients_configured",
                    "request_reference": ref,
                }

            # 3. Check WhatsApp client configuration
            if not whatsapp_client.is_configured():
                logger.warning(
                    "[WhatsApp Notify] Charter notification skipped for %s: WhatsApp Cloud API credentials not configured.",
                    ref,
                )
                cls.finalize_idempotency(idempotency_key, "FAILED", [], charter_dict)
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

            template_lang = (
                getattr(settings, "WHATSAPP_DEPARTMENT_TEMPLATE_LANG", None)
                or os.getenv("WHATSAPP_DEPARTMENT_TEMPLATE_LANG", "en_US")
            ).strip()

            if not template_name:
                logger.warning(
                    "[WhatsApp Notify] WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE is not set for charter. "
                    "Falling back to freeform text. NOTE: Meta Cloud API requires an approved template "
                    "when messaging recipients outside their 24-hour customer window (Error 131047)."
                )

            # 5. Dispatch to ALL configured recipients
            recipient_results: List[Dict[str, Any]] = []
            successful_count = 0
            timeout_count = 0

            for phone in recipients:
                masked = cls._mask_phone(phone)
                try:
                    if template_name:
                        components = cls.build_template_components(message_body)
                        res = whatsapp_client.send_message(
                            to_phone=phone,
                            template_name=template_name,
                            template_language=template_lang,
                            template_components=components,
                        )
                    else:
                        res = whatsapp_client.send_text_message(phone, message_body)

                    msg_id = res.get("message_id")
                    is_success = res.get("success") is True
                    res_status = str(res.get("status") or "").lower()
                    res_err = str(res.get("error") or "")

                    if is_success and msg_id:
                        successful_count += 1
                        recipient_results.append({
                            "phone": masked,
                            "status": "ACCEPTED",
                            "message_id": msg_id,
                        })
                    elif res_status == "timeout_unverified" or "timeout" in res_err.lower():
                        timeout_count += 1
                        recipient_results.append({
                            "phone": masked,
                            "status": "UNKNOWN",
                            "error": res.get("error") or "Timeout connecting to Meta WhatsApp API; delivery state unknown.",
                        })
                    else:
                        if "131047" in res_err:
                            logger.warning(
                                "[WhatsApp Notify] Meta rejected message to %s (Error 131047): "
                                "Recipient is outside 24h messaging window. Configure WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE with an approved Meta template.",
                                masked,
                            )
                        recipient_results.append({
                            "phone": masked,
                            "status": "FAILED",
                            "error": res.get("error") or "Missing message identifier from Meta API",
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

            if successful_count == len(recipients):
                overall_status = "ACCEPTED"
            elif successful_count > 0:
                overall_status = "PARTIALLY_ACCEPTED"
            elif timeout_count > 0:
                overall_status = "UNKNOWN"
            else:
                overall_status = "FAILED"

            cls.finalize_idempotency(idempotency_key, overall_status, recipient_results, charter_dict)

            logger.info(
                "[WhatsApp Notify] Air Charter notification completed for %s: %d/%d accepted by Meta. Status: %s",
                ref,
                successful_count,
                len(recipients),
                overall_status,
            )

            return {
                "status": overall_status,
                "request_reference": ref,
                "recipients_attempted": len(recipients),
                "recipients_accepted": successful_count,
                # Backward-compatible alias
                "recipients_delivered": successful_count,
                "recipient_results": recipient_results,
            }

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception(
                "[WhatsApp Notify] Unexpected exception during charter dispatch for %s: %s",
                ref,
                str(exc),
            )
            cls.finalize_idempotency(idempotency_key, "FAILED", [], charter_dict)
            return {
                "status": "FAILED",
                "error": str(exc),
            }
