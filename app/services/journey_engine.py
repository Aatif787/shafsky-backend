"""
Journey Detection Engine — Core Service Logic (Phase 1).

Responsibilities:
- Detect departure, arrival, and transit airports from the database
- Determine journey type (ARRIVAL / DEPARTURE / TRANSIT)
- Load matching available services for the relevant airport + journey type
- Validate booking window constraints
- Return structured response with graceful degradation for unsupported airports
"""

from typing import Optional, List
from datetime import datetime, timezone
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select

from app.models.journey_models import SupportedAirport, Service, AirportService
from app.schemas.journey_schemas import (
    AvailableServiceItem,
    DetectedAirportInfo,
    JourneyDetectionResponse,
    UrgentAssistanceInfo,
    BookingWindowCheckResponse,
    ServicePriceItem,
    PriceBreakdown,
    BookingValidationResponse,
)


class JourneyDetectionEngine:
    """
    Stateless service class for all journey detection and service availability logic.
    All methods are classmethods — no instance state required.
    """

    # ─── Contact Details (centralized) ───
    CONTACT_PHONE = "+91-XXXXXXXXXX"
    CONTACT_WHATSAPP = "+91-XXXXXXXXXX"

    # ─── Airport Resolution ───

    @classmethod
    def get_supported_airports(cls, db: Session) -> List[SupportedAirport]:
        """Returns all active airports."""
        stmt = (
            select(SupportedAirport)
            .where(SupportedAirport.is_active.is_(True))
            .order_by(SupportedAirport.airport_name)
        )
        return list(db.execute(stmt).scalars().all())

    @classmethod
    def get_airport_by_iata(cls, db: Session, iata_code: str) -> Optional[SupportedAirport]:
        """Resolves a single airport by IATA code (case-insensitive)."""
        normalized = iata_code.strip().upper()
        stmt = select(SupportedAirport).where(SupportedAirport.iata_code == normalized)
        return db.execute(stmt).scalar_one_or_none()

    @classmethod
    def is_airport_supported(cls, db: Session, iata_code: str) -> tuple[bool, Optional[SupportedAirport]]:
        """Returns (is_supported, airport_record_or_none)."""
        airport = cls.get_airport_by_iata(db, iata_code)
        if airport and airport.is_supported and airport.is_active:
            return True, airport
        return False, airport

    @classmethod
    def _to_detected_info(cls, airport: Optional[SupportedAirport]) -> Optional[DetectedAirportInfo]:
        """Converts a model instance to a response schema."""
        if not airport:
            return None
        return DetectedAirportInfo(
            iata_code=airport.iata_code,
            airport_name=airport.airport_name,
            city=airport.city,
            country=airport.country,
            timezone=airport.timezone,
            is_supported=airport.is_supported and airport.is_active,
        )

    # ─── Service Resolution ───

    @classmethod
    def get_all_services(cls, db: Session) -> List[Service]:
        """Returns all globally active services."""
        stmt = (
            select(Service)
            .where(Service.is_active.is_(True))
            .order_by(Service.display_order)
        )
        return list(db.execute(stmt).scalars().all())

    @classmethod
    def get_services_for_airport(
        cls,
        db: Session,
        airport_iata: str,
        journey_type: Optional[str] = None,
        flight_type: Optional[str] = None,
        terminal: Optional[str] = None,
        include_inactive: bool = False,
    ) -> List[AirportService]:
        """
        Loads AirportService mappings for an airport, filtered by journey_type, flight_type, and terminal if specified.
        """
        airport = cls.get_airport_by_iata(db, airport_iata)
        if not airport:
            return []

        stmt = (
            select(AirportService)
            .options(joinedload(AirportService.service))
            .where(AirportService.airport_id == airport.id)
        )
        if not include_inactive:
            stmt = stmt.where(AirportService.is_available.is_(True))

        if journey_type:
            normalized_type = journey_type.strip().upper()
            stmt = stmt.where(AirportService.journey_type == normalized_type)

        if flight_type:
            normalized_flight = flight_type.strip().upper()
            stmt = stmt.where(AirportService.flight_type.in_([normalized_flight, "ALL"]))

        if terminal:
            normalized_term = terminal.strip()
            if normalized_term in ("Terminal 3", "T3", "3"):
                stmt = stmt.where(AirportService.terminal.in_(["Terminal 3", "T3"]))
            elif normalized_term in ("Terminal 1 & 2", "T1 & T2", "T1", "T2", "1", "2", "Terminal 1", "Terminal 2"):
                stmt = stmt.where(AirportService.terminal.in_(["Terminal 1 & 2", "T1 & T2", "Terminal 1", "Terminal 2"]))
            else:
                stmt = stmt.where(AirportService.terminal == normalized_term)

        stmt = stmt.order_by(AirportService.display_priority)
        return list(db.execute(stmt).scalars().unique().all())

    # ─── Booking Window Validation ───

    @classmethod
    def _parse_service_datetime(cls, service_date: str, service_time: Optional[str]) -> Optional[datetime]:
        """Parses date + time into a UTC-aware datetime."""
        try:
            date_str = service_date.strip()
            time_str = (service_time or "12:00").strip()
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            return dt.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            return None

    @classmethod
    def check_booking_window(
        cls,
        min_booking_notice_hours: int,
        service_datetime: datetime,
    ) -> tuple[bool, Optional[float]]:
        """
        Returns (is_bookable_online, hours_remaining).
        If hours_remaining < min_booking_notice_hours, online booking is blocked.
        """
        now = datetime.now(timezone.utc)
        diff = service_datetime - now
        hours_remaining = diff.total_seconds() / 3600.0

        if hours_remaining < min_booking_notice_hours:
            return False, round(hours_remaining, 1)
        return True, round(hours_remaining, 1)

    @classmethod
    def _build_urgent_assistance(
        cls,
        hours_remaining: Optional[float],
        min_notice: int,
    ) -> UrgentAssistanceInfo:
        """Constructs the urgent assistance response object."""
        return UrgentAssistanceInfo(
            is_urgent=True,
            message=(
                f"Your flight departs in approximately {hours_remaining:.0f} hours. "
                f"This service requires at least {min_notice} hours advance notice for online booking. "
                "Please contact our 24/7 VIP Command Desk for instant assistance."
            ),
            hours_remaining=hours_remaining,
            min_notice_required_hours=min_notice,
            contact_phone=cls.CONTACT_PHONE,
            contact_whatsapp=cls.CONTACT_WHATSAPP,
            request_callback_available=True,
        )

    # ─── Main Journey Detection ───

    @classmethod
    def detect_journey(
        cls,
        db: Session,
        departure_code: Optional[str],
        arrival_code: Optional[str],
        journey_type: str,
        service_date: str,
        service_time: Optional[str] = None,
        requested_service_slug: Optional[str] = None,
        terminal: Optional[str] = None,
    ) -> JourneyDetectionResponse:
        """
        Main entry point. Given departure/arrival codes + journey type + date/time:
        1. Resolves airports from the database
        2. Determines which airport is the "primary" (where services are rendered)
        3. Loads available services for that airport + journey type
        4. Checks booking window for each service
        5. Populates prices & currencies
        6. Validates requested service availability if specified
        7. Returns structured response sorted by display_priority
        """
        normalized_type = journey_type.strip().upper() if journey_type else "ARRIVAL"
        if normalized_type not in ("ARRIVAL", "DEPARTURE", "TRANSIT"):
            normalized_type = "ARRIVAL"

        # Resolve airports
        dep_airport = cls.get_airport_by_iata(db, departure_code) if departure_code else None
        arr_airport = cls.get_airport_by_iata(db, arrival_code) if arrival_code else None

        dep_info = cls._to_detected_info(dep_airport)
        arr_info = cls._to_detected_info(arr_airport)

        # Determine primary airport (where Shafsky renders services)
        if normalized_type == "DEPARTURE":
            primary_airport = dep_airport
        elif normalized_type == "ARRIVAL":
            primary_airport = arr_airport
        elif normalized_type == "TRANSIT":
            transit_code = requested_service_slug if (requested_service_slug and len(requested_service_slug) == 3) else None
            primary_airport = cls.get_airport_by_iata(db, transit_code) if transit_code else (arr_airport or dep_airport)
        else:
            primary_airport = arr_airport or dep_airport

        primary_info = cls._to_detected_info(primary_airport)
        is_supported = bool(primary_airport and primary_airport.is_supported and primary_airport.is_active)

        # If unsupported, return early with empty services
        if not is_supported:
            return JourneyDetectionResponse(
                success=True,
                departure_airport=dep_info,
                arrival_airport=arr_info,
                transit_airport=primary_info if normalized_type == "TRANSIT" else None,
                journey_type=normalized_type,
                primary_airport=primary_info,
                is_supported=False,
                available_services=[],
                requested_service_slug=requested_service_slug,
                is_requested_service_available=False,
                unavailable_message="This service is currently unavailable for your selected journey.",
            )

        # Determine flight_type (DOMESTIC vs INTERNATIONAL)
        flight_type = None
        if dep_airport and arr_airport:
            flight_type = "INTERNATIONAL" if dep_airport.country != arr_airport.country else "DOMESTIC"
        elif dep_airport and arrival_code:
            arr_code_clean = arrival_code.strip().upper()
            if arr_code_clean != dep_airport.iata_code:
                indian_airports = {"DEL", "BOM", "BLR", "HYD", "CCU", "MAA", "AMD", "GOI", "GOX", "COK", "JAI", "ATQ", "LKO", "BBI", "IXC", "GAU", "IXE", "IXR", "TRV", "VTZ"}
                flight_type = "DOMESTIC" if arr_code_clean in indian_airports else "INTERNATIONAL"
        elif arr_airport and departure_code:
            dep_code_clean = departure_code.strip().upper()
            if dep_code_clean != arr_airport.iata_code:
                indian_airports = {"DEL", "BOM", "BLR", "HYD", "CCU", "MAA", "AMD", "GOI", "GOX", "COK", "JAI", "ATQ", "LKO", "BBI", "IXC", "GAU", "IXE", "IXR", "TRV", "VTZ"}
                flight_type = "DOMESTIC" if dep_code_clean in indian_airports else "INTERNATIONAL"

        # Check distinct non-null terminals for this airport/journey/flight configuration
        terminal_stmt = (
            select(AirportService.terminal)
            .where(
                AirportService.airport_id == primary_airport.id,
                AirportService.is_available.is_(True),
                AirportService.terminal.isnot(None),
            )
        )
        if normalized_type:
            terminal_stmt = terminal_stmt.where(AirportService.journey_type == normalized_type)
        if flight_type:
            terminal_stmt = terminal_stmt.where(AirportService.flight_type.in_([flight_type, "ALL"]))

        raw_terminals = list(db.execute(terminal_stmt).scalars().unique().all())
        available_terminals = [t for t in raw_terminals if t]
        available_terminals.sort()

        selected_terminal = None
        if terminal:
            term_clean = terminal.strip()
            if term_clean in ("Terminal 3", "T3", "3"):
                selected_terminal = "Terminal 3"
            elif term_clean in ("Terminal 1 & 2", "T1 & T2", "T1", "T2", "1", "2", "Terminal 1", "Terminal 2"):
                selected_terminal = "Terminal 1 & 2"
            else:
                selected_terminal = term_clean
        elif available_terminals:
            selected_terminal = available_terminals[0]

        # Load available services for the primary airport + journey type + flight type + selected_terminal
        airport_services = cls.get_services_for_airport(
            db, primary_airport.iata_code, journey_type=normalized_type, flight_type=flight_type, terminal=selected_terminal
        )

        # Parse service datetime for booking window checks
        service_dt = cls._parse_service_datetime(service_date, service_time)

        # Build available service items with booking window checks
        available_services: List[AvailableServiceItem] = []
        all_urgent = True  # Track if ALL services are urgent

        for aps in airport_services:
            svc = aps.service
            if not svc or not svc.is_active:
                continue

            is_bookable = True
            urgent = None
            hours_remaining = None

            if service_dt:
                is_bookable, hours_remaining = cls.check_booking_window(
                    aps.min_booking_notice_hours, service_dt
                )
                if not is_bookable:
                    urgent = cls._build_urgent_assistance(hours_remaining, aps.min_booking_notice_hours)
                else:
                    all_urgent = False
            else:
                all_urgent = False

            raw_price = getattr(aps, "price", 2499.00)
            price_val = float(raw_price) if raw_price is not None else 2499.00
            curr_val = str(getattr(aps, "currency", "INR") or "INR")

            available_services.append(
                AvailableServiceItem(
                    airport_service_id=aps.id,
                    service_id=svc.id,
                    name=svc.name,
                    slug=svc.slug,
                    description=svc.description,
                    short_description=getattr(aps, "short_description", None) or svc.description,
                    flight_type=getattr(aps, "flight_type", "DOMESTIC") or "DOMESTIC",
                    terminal=getattr(aps, "terminal", None),
                    features=getattr(aps, "features", []) or [],
                    additional_benefits=getattr(aps, "additional_benefits", []) or [],
                    icon=svc.icon,
                    journey_type=aps.journey_type,
                    min_booking_notice_hours=aps.min_booking_notice_hours,
                    display_priority=aps.display_priority,
                    price=price_val,
                    currency=curr_val,
                    is_bookable_online=is_bookable,
                    urgent_assistance=urgent,
                )
            )

        # Requirement #5: Sort strictly by display_priority stored in database
        available_services.sort(key=lambda s: s.display_priority)

        # Requirement #6: Validate requested service if specified
        is_req_available = True
        unavail_msg = None
        if requested_service_slug:
            clean_req_slug = requested_service_slug.strip().lower()
            matching_req = next((s for s in available_services if s.slug.lower() == clean_req_slug), None)
            if not matching_req:
                is_req_available = False
                unavail_msg = "This service is currently unavailable for your selected journey."

        # If ALL services are urgent, set a global urgency flag
        global_urgent = None
        if all_urgent and available_services and service_dt:
            now = datetime.now(timezone.utc)
            diff_hours = round((service_dt - now).total_seconds() / 3600.0, 1)
            global_urgent = cls._build_urgent_assistance(diff_hours, 0)
            global_urgent.message = (
                f"Your flight departs in approximately {diff_hours:.0f} hours. "
                "All services at this airport require more advance notice for online booking. "
                "Please contact our 24/7 VIP Command Desk."
            )

        return JourneyDetectionResponse(
            success=True,
            departure_airport=dep_info,
            arrival_airport=arr_info,
            transit_airport=primary_info if normalized_type == "TRANSIT" else None,
            journey_type=normalized_type,
            primary_airport=primary_info,
            is_supported=is_supported,
            available_terminals=available_terminals,
            selected_terminal=selected_terminal,
            available_services=available_services,
            urgent_assistance=global_urgent,
            requested_service_slug=requested_service_slug,
            is_requested_service_available=is_req_available,
            unavailable_message=unavail_msg,
        )

    # ─── Standalone Booking Window Check ───

    @classmethod
    def check_service_booking_window(
        cls,
        db: Session,
        airport_iata: str,
        service_slug: str,
        journey_type: str,
        service_date: str,
        service_time: str,
    ) -> BookingWindowCheckResponse:
        """
        Check whether a specific service can be booked online given the time constraints.
        """
        airport = cls.get_airport_by_iata(db, airport_iata)
        if not airport:
            return BookingWindowCheckResponse(
                success=False,
                is_bookable_online=False,
                urgent_assistance=UrgentAssistanceInfo(
                    message="Airport not found in our system.",
                    contact_phone=cls.CONTACT_PHONE,
                    contact_whatsapp=cls.CONTACT_WHATSAPP,
                ),
            )

        normalized_type = journey_type.strip().upper()
        stmt = (
            select(AirportService)
            .options(joinedload(AirportService.service))
            .join(Service)
            .where(
                AirportService.airport_id == airport.id,
                Service.slug == service_slug,
                AirportService.journey_type == normalized_type,
                AirportService.is_available.is_(True),
            )
        )
        aps = db.execute(stmt).scalar_one_or_none()

        if not aps:
            return BookingWindowCheckResponse(
                success=False,
                is_bookable_online=False,
                urgent_assistance=UrgentAssistanceInfo(
                    message="This service is not available at the selected airport for this journey type.",
                    contact_phone=cls.CONTACT_PHONE,
                    contact_whatsapp=cls.CONTACT_WHATSAPP,
                ),
            )

        service_dt = cls._parse_service_datetime(service_date, service_time)
        if not service_dt:
            return BookingWindowCheckResponse(
                success=False,
                is_bookable_online=False,
                urgent_assistance=UrgentAssistanceInfo(
                    message="Invalid date or time format provided.",
                    contact_phone=cls.CONTACT_PHONE,
                    contact_whatsapp=cls.CONTACT_WHATSAPP,
                ),
            )

        is_bookable, hours_remaining = cls.check_booking_window(
            aps.min_booking_notice_hours, service_dt
        )

        urgent = None
        if not is_bookable:
            urgent = cls._build_urgent_assistance(hours_remaining, aps.min_booking_notice_hours)

        return BookingWindowCheckResponse(
            success=True,
            is_bookable_online=is_bookable,
            hours_remaining=hours_remaining,
            min_notice_required_hours=aps.min_booking_notice_hours,
            urgent_assistance=urgent,
        )

    # ─── Booking Pre-Payment Validation & Reference Generation ───

    @classmethod
    def validate_booking(
        cls,
        db: Session,
        airport_code: str,
        journey_type: str,
        service_date: str,
        service_time: Optional[str] = "12:00",
        selected_service_slugs: Optional[List[str]] = None,
        guest_count: int = 1,
    ) -> BookingValidationResponse:
        """
        Validates an entire booking before payment:
        1. Checks airport support status
        2. Validates service availability & lead time rules
        3. Calculates dynamic price breakdown from DB
        4. Generates temporary booking reference code
        """
        import secrets, string
        date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        rand_suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        ref_code = f"SHK-{date_stamp}-{rand_suffix}"

        messages: List[str] = []
        is_valid = True

        # 1. Airport check
        airport = cls.get_airport_by_iata(db, airport_code)
        if not airport or not airport.is_supported or not airport.is_active:
            is_valid = False
            messages.append(f"Airport '{airport_code.upper()}' is currently unsupported for instant online dispatch.")
            return BookingValidationResponse(
                success=True,
                is_valid=False,
                booking_reference=ref_code,
                airport_code=airport_code.upper(),
                journey_type=journey_type.upper(),
                is_airport_supported=False,
                validation_messages=messages,
                price_breakdown=PriceBreakdown(
                    items=[],
                    subtotal=0.0,
                    tax_percent=18.0,
                    tax_amount=0.0,
                    total=0.0,
                    currency="INR"
                )
            )

        # 2. Service mappings lookup
        slugs = [s.strip().lower() for s in (selected_service_slugs or ["meet_greet"])]
        if not slugs:
            slugs = ["meet_greet"]

        available_mappings = cls.get_services_for_airport(db, airport.iata_code, journey_type=journey_type)
        service_map_by_slug = {aps.service.slug.lower(): aps for aps in available_mappings if aps.service}

        service_items: List[ServicePriceItem] = []
        subtotal = 0.0
        service_dt = cls._parse_service_datetime(service_date, service_time)

        for slug in slugs:
            aps = service_map_by_slug.get(slug)
            if not aps:
                is_valid = False
                messages.append(f"Service '{slug.replace('_', ' ').title()}' is unavailable for {journey_type.upper()} at {airport.iata_code}.")
                continue

            # Check notice window
            if service_dt:
                is_bookable, hours_rem = cls.check_booking_window(aps.min_booking_notice_hours, service_dt)
                if not is_bookable:
                    is_valid = False
                    messages.append(
                        f"Service '{aps.service.name}' requires at least {aps.min_booking_notice_hours} hours notice. "
                        f"(Approx {hours_rem:.0f}h remaining)"
                    )

            unit_price = float(getattr(aps, "price", 2499.00) or 2499.00)
            item_sub = unit_price * max(1, guest_count)
            subtotal += item_sub

            service_items.append(
                ServicePriceItem(
                    slug=aps.service.slug,
                    name=aps.service.name,
                    unit_price=unit_price,
                    quantity=max(1, guest_count),
                    item_subtotal=item_sub
                )
            )

        tax_percent = 18.0
        tax_amount = round(subtotal * (tax_percent / 100.0), 2)
        total = round(subtotal + tax_amount, 2)
        curr = getattr(available_mappings[0], "currency", "INR") if available_mappings else "INR"

        if is_valid and not messages:
            messages.append("All booking details verified successfully against live inventory.")

        return BookingValidationResponse(
            success=True,
            is_valid=is_valid,
            booking_reference=ref_code,
            airport_code=airport.iata_code,
            journey_type=journey_type.upper(),
            is_airport_supported=True,
            validation_messages=messages,
            price_breakdown=PriceBreakdown(
                items=service_items,
                subtotal=subtotal,
                tax_percent=tax_percent,
                tax_amount=tax_amount,
                total=total,
                currency=curr
            )
        )
