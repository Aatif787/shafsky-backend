"""Service mapping layer for WhatsApp operational notification routing.

Maps heterogeneous booking categories, service types, packages, and enquiry
payloads into the canonical NOTIFICATION_SERVICE_KEYS recognized by Super Admin
routing tables:
- Meet & Greet -> Meet & Greet
- Lounge -> Lounge
- Hotel -> Hotel Services (quote/enquiry) / Hotel Booking (confirmed/direct)
- Transportation -> Transportation Services (and Ground Transport)
- Charter -> Charter (and Private Charter)
- Ticketing -> Ticketing
- Round Trip -> Round Trip
- Cargo -> Cargo & Logistics
- Medical -> Medical Assistance
- Travel Support -> Travel Support

If a service cannot be mapped, logs the routing failure without sending
to an unrelated recipient.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from app.schemas.notification_routing import (
    NOTIFICATION_SERVICE_KEYS,
    NOTIFICATION_SERVICE_KEY_SET,
)

logger = logging.getLogger("shafsky.notification_service_mapping")

MEET_AND_GREET_PACKAGES = frozenset({
    "silver",
    "gold",
    "platinum",
    "elite",
    "elite_plus",
    "elite-plus",
    "elite plus",
    "meet_and_greet",
    "meet and greet",
    "meet & greet",
    "vip meet & greet",
    "multi_service",
})

MEET_AND_GREET_NAMES = (
    "meet & greet",
    "meet and greet",
    "platinum",
    "gold",
    "elite plus",
    "elite_plus",
    "elite",
    "silver",
)

LOUNGE_KEYWORDS = (
    "lounge",
    "airport lounge",
    "vip lounge",
    "lounge access",
    "business lounge",
    "executive lounge",
    "arrival lounge",
)

TRANSPORT_KEYWORDS = (
    "transport",
    "transportation",
    "ground transport",
    "ground_transport",
    "transfer",
    "chauffeur",
    "sedan",
    "suv",
    "van",
)

CHARTER_KEYWORDS = (
    "charter",
    "air charter",
    "private charter",
    "aircraft charter",
    "jet",
    "turboprop",
    "helicopter",
)

HOTEL_KEYWORDS = (
    "hotel",
    "hotels",
    "hotel booking",
    "hotel services",
    "hotel reservation",
    "transit hotel",
    "luxury hotel",
)

TICKETING_KEYWORDS = (
    "ticketing",
    "air ticketing",
    "flight ticket",
    "flight booking",
)

ROUND_TRIP_KEYWORDS = (
    "round trip",
    "round-trip",
    "roundtrip",
)

CARGO_KEYWORDS = (
    "cargo",
    "logistics",
    "air cargo",
    "freight",
    "cargo & logistics",
)

MEDICAL_KEYWORDS = (
    "medical",
    "air ambulance",
    "medical escort",
    "stretcher transport",
    "wheelchair",
)

TRAVEL_SUPPORT_KEYWORDS = (
    "travel support",
    "travel services",
    "visa assistance",
    "visa",
    "travel insurance",
    "in-flight catering",
    "meal arrangement",
)


class NotificationServiceMapper:
    """Unified resolution and mapping engine for operational WhatsApp alerts."""

    @classmethod
    def resolve_target_service_key(
        cls,
        service_or_context: Union[str, Dict[str, Any], None] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Resolves a single canonical service key, or None if unmapped."""
        candidates = cls.resolve_candidate_service_keys(service_or_context, context)
        return candidates[0] if candidates else None

    @classmethod
    def resolve_candidate_service_keys(
        cls,
        service_or_context: Union[str, Dict[str, Any], None] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Resolves prioritized candidate service keys for route matching.

        Returns an ordered list of matching keys in NOTIFICATION_SERVICE_KEYS.
        If the service is unknown/unmapped, logs a routing warning and returns [].
        """
        raw_service: Optional[str] = None
        ctx: Dict[str, Any] = {}

        if isinstance(service_or_context, dict):
            ctx = dict(service_or_context)
            raw_service = str(
                ctx.get("service_category")
                or ctx.get("serviceCategory")
                or ctx.get("service_name")
                or ctx.get("serviceName")
                or ctx.get("selected_service_name")
                or ""
            ).strip()
        elif isinstance(service_or_context, str):
            raw_service = service_or_context.strip()
            if context and isinstance(context, dict):
                ctx = dict(context)
        elif context and isinstance(context, dict):
            ctx = dict(context)
            raw_service = str(
                ctx.get("service_category")
                or ctx.get("serviceCategory")
                or ctx.get("service_name")
                or ctx.get("serviceName")
                or ctx.get("selected_service_name")
                or ""
            ).strip()

        if not raw_service and not ctx:
            return []

        svc_cat = str(ctx.get("service_category") or ctx.get("serviceCategory") or raw_service or "").strip()
        svc_name = str(ctx.get("service_name") or ctx.get("serviceName") or ctx.get("selected_service_name") or "").strip()
        svc_type = str(ctx.get("service_type") or ctx.get("serviceType") or ctx.get("package") or "").strip()
        details = ctx.get("details") or ctx.get("metadata_json") or {}
        if not isinstance(details, dict):
            details = {}

        cat_lower = svc_cat.lower()
        name_lower = svc_name.lower()
        type_lower = svc_type.lower()
        pkg_lower = str(details.get("package") or "").strip().lower()

        # 1. Exact match pass: If the caller explicitly passed a valid service key and no context contradicts it
        # (e.g. unit tests or specific route callers passing "Ground Transport" or "Private Charter")
        # We handle this while still ensuring Meet & Greet packages under Airport Assistance resolve to Meet & Greet.
        is_meet_and_greet = (
            cat_lower in ("meet & greet", "meet and greet", "meet_and_greet", "meet-and-greet")
            or any(kw in name_lower for kw in MEET_AND_GREET_NAMES)
            or type_lower in MEET_AND_GREET_PACKAGES
            or pkg_lower in MEET_AND_GREET_PACKAGES
        )

        is_lounge = (
            cat_lower in ("lounge", "airport lounge", "vip lounge", "lounge access")
            or any(kw in name_lower for kw in LOUNGE_KEYWORDS)
            or any(kw in type_lower for kw in LOUNGE_KEYWORDS)
            or bool(details.get("lounge_type"))
        )

        # A) Meet & Greet (even if category is "Airport Assistance")
        if is_meet_and_greet and not is_lounge:
            return ["Meet & Greet", "Airport Assistance", "Airport Services"]

        # B) Lounge
        if is_lounge:
            return ["Lounge"]

        # C) Airport Assistance (non-Meet & Greet, e.g. Fast Track, Baggage Assistance, or generic)
        if cat_lower in ("airport assistance", "airport services", "fast track", "baggage assistance"):
            if "fast track" in type_lower or "baggage" in type_lower or "airport assistance" in cat_lower:
                return ["Airport Assistance", "Airport Services"]
            return ["Airport Assistance", "Airport Services", "Meet & Greet"]

        # D) Hotel
        if cat_lower in HOTEL_KEYWORDS or any(kw in name_lower for kw in HOTEL_KEYWORDS) or any(kw in type_lower for kw in HOTEL_KEYWORDS):
            status = str(ctx.get("status") or "").upper()
            stage = str(ctx.get("notification_stage") or "").upper()
            amount = float(ctx.get("total_amount") or ctx.get("amount") or 0.0)
            is_quote = (
                bool(ctx.get("quote_request"))
                or status in ("QUOTE_REQUESTED", "AWAITING_QUOTE", "REQUEST_REQUIRED")
                or stage == "ENQUIRY"
                or (amount <= 0 and stage != "PAYMENT_CONFIRMED" and status != "CONFIRMED")
            )
            if cat_lower == "hotel booking":
                return ["Hotel Booking", "Hotel", "Hotels", "Hotel Services"]
            if cat_lower == "hotel services":
                return ["Hotel Services", "Hotel", "Hotels", "Hotel Booking"]
            if is_quote:
                return ["Hotel Services", "Hotel Booking", "Hotel", "Hotels"]
            return ["Hotel Booking", "Hotel Services", "Hotel", "Hotels"]

        # E) Transportation
        if (
            cat_lower in TRANSPORT_KEYWORDS
            or cat_lower in ("hotel & transportation", "transportation services")
            or any(kw in name_lower for kw in TRANSPORT_KEYWORDS)
            or any(kw in type_lower for kw in TRANSPORT_KEYWORDS)
            or bool(details.get("vehicle_name"))
            or bool(details.get("vehicle_preference"))
            or bool(details.get("pickup_location"))
        ):
            if cat_lower == "ground transport":
                return ["Ground Transport", "Transportation Services", "Transport", "Hotel & Transportation"]
            return ["Transportation Services", "Ground Transport", "Transport", "Hotel & Transportation"]

        # F) Charter
        if (
            cat_lower in CHARTER_KEYWORDS
            or any(kw in name_lower for kw in CHARTER_KEYWORDS)
            or any(kw in type_lower for kw in CHARTER_KEYWORDS)
            or "charter" in str(ctx.get("booking_ref") or "").lower()
            or "chr-" in str(ctx.get("booking_ref") or "").lower()
            or "request_reference" in ctx
        ):
            if cat_lower == "private charter":
                return ["Private Charter", "Charter"]
            return ["Charter", "Private Charter"]

        # G) Round Trip
        if cat_lower in ROUND_TRIP_KEYWORDS or any(kw in name_lower for kw in ROUND_TRIP_KEYWORDS):
            return ["Round Trip"]

        # H) Ticketing
        if cat_lower in TICKETING_KEYWORDS or any(kw in name_lower for kw in TICKETING_KEYWORDS):
            return ["Ticketing"]

        # I) Cargo & Logistics
        if cat_lower in CARGO_KEYWORDS or any(kw in name_lower for kw in CARGO_KEYWORDS):
            if cat_lower == "cargo":
                return ["Cargo", "Cargo & Logistics"]
            return ["Cargo & Logistics", "Cargo"]

        # J) Medical Assistance
        if cat_lower in MEDICAL_KEYWORDS or any(kw in name_lower for kw in MEDICAL_KEYWORDS):
            if cat_lower == "medical services":
                return ["Medical Services", "Medical Assistance"]
            return ["Medical Assistance", "Medical Services"]

        # K) Travel Support / Travel Services
        if cat_lower in TRAVEL_SUPPORT_KEYWORDS or any(kw in name_lower for kw in TRAVEL_SUPPORT_KEYWORDS):
            if cat_lower == "travel services":
                return ["Travel Services", "Travel Support"]
            return ["Travel Support", "Travel Services"]

        # L) Check exact case-insensitive match against NOTIFICATION_SERVICE_KEYS
        for known_key in NOTIFICATION_SERVICE_KEYS:
            if known_key.lower() == raw_service.lower():
                return [known_key]

        # Unknown / Unmapped service
        logger.warning(
            "[NotificationServiceMapper] Routing failure: unable to map service '%s' (name='%s', type='%s', ref='%s'). "
            "Alert will NOT be sent to unrelated recipients.",
            raw_service,
            svc_name,
            svc_type,
            ctx.get("booking_ref") or ctx.get("request_reference") or "N/A",
        )
        return []
