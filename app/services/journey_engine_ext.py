"""
Multi-Service Itinerary & Service Query Mixin for JourneyDetectionEngine.
"""

import logging
import secrets
from datetime import datetime, timezone
import uuid
from typing import List, Optional, Dict, Any
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.models.journey_models import ServiceQuery
from app.models.schema import Booking, BookingStatus
from app.services.service_airport_rules import (
    derive_flight_type_from_route,
    derive_transit_type_from_route,
    normalize_flight_type,
    normalize_iata,
    normalize_journey_type,
)
from app.schemas.journey_schemas import (
    AvailableServiceItem,
    FlightLegInput,
    MultiServiceAvailabilityRequest,
    ServiceItemAvailability,
    MultiServiceAvailabilityResponse,
    ServiceQueryCreate,
    ServiceQueryResponse,
)


class JourneyMultiServiceMixin:
    """Mixin providing multi-service itinerary resolution, availability checks, and service queries."""

    @classmethod
    def resolve_itinerary(
        cls,
        db: Session,
        legs: Optional[List[FlightLegInput]] = None,
        origin_code: Optional[str] = None,
        dest_code: Optional[str] = None,
        transit_codes: Optional[List[str]] = None,
        flight_num: Optional[str] = None,
        flight_date: Optional[str] = None,
        default_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Builds a canonical itinerary structure with 1 or more legs.
        Identifies:
        - First airport = Departure airport
        - Intermediate airport(s) = Transit airport(s)
        - Final airport = Arrival airport
        Supports:
        - Explicit legs list
        - Simple origin + dest + transit_codes
        - Flight number + flight date (minimum customer input)
        """
        canonical_legs: List[Dict[str, Any]] = []

        # 1. If explicit legs are provided
        if legs and len(legs) > 0:
            for idx, leg in enumerate(legs):
                orig = normalize_iata(leg.origin_code)
                dest = normalize_iata(leg.dest_code)
                if not orig or not dest:
                    continue
                canonical_legs.append({
                    "leg_index": idx,
                    "origin_code": orig,
                    "dest_code": dest,
                    "flight_num": (leg.flight_num or "").strip().upper() or None,
                    "departure_time": leg.departure_time,
                    "arrival_time": leg.arrival_time,
                    "service_date": leg.service_date or default_date,
                    "terminal": leg.terminal,
                })

        # 2. If origin + dest + transit_codes are provided
        elif origin_code and dest_code:
            norm_origin = normalize_iata(origin_code)
            norm_dest = normalize_iata(dest_code)
            raw_transits = [normalize_iata(t) for t in (transit_codes or []) if normalize_iata(t)]
            filtered_transits: List[str] = []
            for t in raw_transits:
                if t and t != norm_origin and t != norm_dest:
                    filtered_transits.append(t)

            if not filtered_transits:
                canonical_legs.append({
                    "leg_index": 0,
                    "origin_code": norm_origin,
                    "dest_code": norm_dest,
                    "flight_num": (flight_num or "").strip().upper() or None,
                    "service_date": flight_date or default_date,
                })
            else:
                stops = [norm_origin] + filtered_transits + [norm_dest]
                for idx in range(len(stops) - 1):
                    canonical_legs.append({
                        "leg_index": idx,
                        "origin_code": stops[idx],
                        "dest_code": stops[idx + 1],
                        "flight_num": (flight_num or "").strip().upper() if idx == 0 else None,
                        "service_date": flight_date or default_date,
                    })

        # 3. Flight number + date only (minimum customer input)
        elif flight_num:
            clean_fnum = flight_num.strip().upper()
            clean_fdate = flight_date or default_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            try:
                from app.flight.service import FlightIntelligenceService
                from app.flight.providers.aviation_edge_provider import AviationEdgeProvider
                from app.flight.providers.aviationstack_provider import AviationStackProvider
                import os
                provider_name = "aviation_edge" if os.getenv("TESTING") == "1" else "aviationstack"
                provider = AviationEdgeProvider() if provider_name == "aviation_edge" else AviationStackProvider()
                svc = FlightIntelligenceService(provider=provider)
                f_data = svc.validate_flight(clean_fnum, clean_fdate)
                if f_data and f_data.departure and f_data.departure.airport and f_data.arrival and f_data.arrival.airport:
                    canonical_legs.append({
                        "leg_index": 0,
                        "origin_code": normalize_iata(f_data.departure.airport),
                        "dest_code": normalize_iata(f_data.arrival.airport),
                        "flight_num": clean_fnum,
                        "service_date": clean_fdate,
                        "terminal": f_data.departure.terminal,
                    })
            except Exception:
                pass

        if not canonical_legs:
            norm_orig = normalize_iata(origin_code) or "DEL"
            norm_dst = normalize_iata(dest_code) or "BOM"
            canonical_legs.append({
                "leg_index": 0,
                "origin_code": norm_orig,
                "dest_code": norm_dst,
                "flight_num": (flight_num or "").strip().upper() or None,
                "service_date": flight_date or default_date,
            })

        departure_airport = canonical_legs[0]["origin_code"]
        arrival_airport = canonical_legs[-1]["dest_code"]

        transit_airports: List[str] = []
        if len(canonical_legs) > 1:
            for idx in range(len(canonical_legs) - 1):
                t_code = canonical_legs[idx]["dest_code"]
                if t_code not in transit_airports:
                    transit_airports.append(t_code)

        for leg in canonical_legs:
            orig_apt = cls.get_airport_by_iata(db, leg["origin_code"])
            dest_apt = cls.get_airport_by_iata(db, leg["dest_code"])
            leg["origin_name"] = orig_apt.airport_name if orig_apt else None
            leg["dest_name"] = dest_apt.airport_name if dest_apt else None
            leg["origin_supported"] = bool(orig_apt and orig_apt.is_supported and orig_apt.is_active)
            leg["dest_supported"] = bool(dest_apt and dest_apt.is_supported and dest_apt.is_active)
            if orig_apt and dest_apt:
                leg["is_domestic"] = (orig_apt.country == dest_apt.country)
            else:
                leg["is_domestic"] = True

        return {
            "departure_airport": departure_airport,
            "arrival_airport": arrival_airport,
            "transit_airports": transit_airports,
            "legs": canonical_legs,
            "is_connecting": len(canonical_legs) > 1,
            "total_legs": len(canonical_legs),
        }

    @classmethod
    def check_multi_service_availability(
        cls,
        db: Session,
        request: MultiServiceAvailabilityRequest,
    ) -> MultiServiceAvailabilityResponse:
        """
        Validates availability for any combination of services across an itinerary:
        - Departure -> first origin airport
        - Transit -> each applicable intermediate airport
        - Arrival -> final destination airport
        Returns structured itemized status: AVAILABLE or REQUEST_REQUIRED per service.
        Sums authoritative DB prices only for AVAILABLE services.
        """
        itinerary = cls.resolve_itinerary(
            db=db,
            legs=request.legs,
            origin_code=request.origin_code,
            dest_code=request.dest_code,
            transit_codes=request.transit_codes,
            flight_num=request.flight_num,
            flight_date=request.flight_date,
            default_date=request.service_date,
        )

        service_items: List[ServiceItemAvailability] = []
        guest_count = max(1, request.guest_count)
        default_svc_date = request.service_date or request.flight_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

        seen_selections = set()

        for sel in request.selected_services:
            raw_st = normalize_journey_type(sel.service_type)
            if raw_st not in ("DEPARTURE", "ARRIVAL", "TRANSIT"):
                continue

            target_airports: List[str] = []
            if raw_st == "DEPARTURE":
                target_airports = [itinerary["departure_airport"]]
            elif raw_st == "ARRIVAL":
                target_airports = [itinerary["arrival_airport"]]
            elif raw_st == "TRANSIT":
                if sel.airport_code:
                    norm_c = normalize_iata(sel.airport_code)
                    if norm_c:
                        target_airports = [norm_c]
                elif itinerary["transit_airports"]:
                    target_airports = list(itinerary["transit_airports"])
                else:
                    target_airports = []

            if raw_st == "TRANSIT" and not target_airports:
                item_key = (raw_st, "NO_TRANSIT")
                if item_key not in seen_selections:
                    seen_selections.add(item_key)
                    service_items.append(
                        ServiceItemAvailability(
                            service_type="TRANSIT",
                            airport_code="N/A",
                            airport_name=None,
                            city=None,
                            country=None,
                            is_airport_supported=False,
                            status="REQUEST_REQUIRED",
                            status_reason="Your itinerary does not contain any transit or layover airports.",
                            is_bookable_online=False,
                            currency="INR",
                        )
                    )
                continue

            for apt_code in target_airports:
                item_key = (raw_st, apt_code, (sel.package_slug or "").lower())
                if item_key in seen_selections:
                    continue
                seen_selections.add(item_key)

                airport = cls.get_airport_by_iata(db, apt_code)
                is_supported = bool(airport and airport.is_supported and airport.is_active)

                if not is_supported or not airport:
                    service_items.append(
                        ServiceItemAvailability(
                            service_type=raw_st,
                            airport_code=apt_code,
                            airport_name=airport.airport_name if airport else None,
                            city=airport.city if airport else None,
                            country=airport.country if airport else None,
                            is_airport_supported=False,
                            status="REQUEST_REQUIRED",
                            status_reason=f"Airport '{apt_code}' is currently not supported for instant online booking. A service enquiry is required.",
                            is_bookable_online=False,
                            currency="INR",
                        )
                    )
                    continue

                flight_type = None
                if raw_st == "DEPARTURE":
                    first_dest = itinerary["legs"][0]["dest_code"] if itinerary["legs"] else None
                    try:
                        flight_type = derive_flight_type_from_route(db, apt_code, first_dest, "DEPARTURE")
                    except Exception:
                        flight_type = "DOMESTIC"
                elif raw_st == "ARRIVAL":
                    last_origin = itinerary["legs"][-1]["origin_code"] if itinerary["legs"] else None
                    try:
                        flight_type = derive_flight_type_from_route(db, last_origin, apt_code, "ARRIVAL")
                    except Exception:
                        flight_type = "DOMESTIC"
                elif raw_st == "TRANSIT":
                    in_leg = next((l for l in itinerary["legs"] if l["dest_code"] == apt_code), None)
                    out_leg = next((l for l in itinerary["legs"] if l["origin_code"] == apt_code), None)
                    if in_leg and out_leg:
                        try:
                            flight_type = derive_transit_type_from_route(db, in_leg["origin_code"], out_leg["dest_code"])
                        except Exception:
                            flight_type = "DOMESTIC_DOMESTIC"
                    else:
                        flight_type = normalize_flight_type(request.flight_type) or "DOMESTIC_DOMESTIC"

                available_mappings = cls.get_services_for_airport(
                    db,
                    airport_iata=apt_code,
                    journey_type=raw_st,
                    flight_type=flight_type,
                    terminal=sel.terminal,
                )

                if not available_mappings:
                    service_items.append(
                        ServiceItemAvailability(
                            service_type=raw_st,
                            airport_code=apt_code,
                            airport_name=airport.airport_name,
                            city=airport.city,
                            country=airport.country,
                            is_airport_supported=True,
                            status="REQUEST_REQUIRED",
                            status_reason=f"No {raw_st.title()} services are configured at {airport.airport_name} ({apt_code}) for {flight_type}.",
                            flight_type=flight_type,
                            is_bookable_online=False,
                            currency="INR",
                        )
                    )
                    continue

                pkg_items: List[AvailableServiceItem] = []
                for aps in available_mappings:
                    svc = aps.service
                    if not svc or not svc.is_active:
                        continue
                    pkg_items.append(
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
                            min_booking_notice_hours=getattr(aps, "min_booking_notice_hours", 6) or 6,
                            display_priority=getattr(aps, "display_priority", 0) or 0,
                            price=float(aps.price),
                            currency=aps.currency or "INR",
                            is_bookable_online=True,
                        )
                    )

                target_mapping = None
                if sel.package_slug:
                    from app.services.booking_service import BookingService
                    norm_slug = BookingService.normalize_package_slug(sel.package_slug).lower()
                    target_mapping = next(
                        (
                            m for m in available_mappings
                            if m.service and BookingService.normalize_package_slug(m.service.slug).lower() == norm_slug
                        ),
                        None,
                    )

                if not target_mapping:
                    if available_mappings:
                        target_mapping = available_mappings[0]
                    else:
                        continue

                leg_match = next((l for l in itinerary["legs"] if l["origin_code"] == apt_code or l["dest_code"] == apt_code), None)
                svc_date = (leg_match or {}).get("service_date") or default_svc_date
                svc_time = request.service_time or "12:00"
                svc_dt = cls._parse_service_datetime(svc_date, svc_time)

                notice_hours = cls._airport_notice_hours(flight_type)
                is_bookable = True
                urgent = None
                hours_rem = None

                if svc_dt:
                    is_bookable, hours_rem = cls.check_booking_window(notice_hours, svc_dt)
                    if not is_bookable:
                        urgent = cls._build_urgent_assistance(hours_rem, notice_hours)

                unit_price = float(target_mapping.price)
                pkg_svc = target_mapping.service
                currency = target_mapping.currency or "INR"

                if not available_mappings and sel.terminal:
                    available_mappings = cls.get_services_for_airport(
                        db,
                        airport_iata=apt_code,
                        journey_type=raw_st,
                        flight_type=flight_type,
                    )

                tot = round(unit_price * guest_count, 2)
                service_items.append(
                    ServiceItemAvailability(
                        service_type=raw_st,
                        airport_code=apt_code,
                        airport_name=airport.airport_name,
                        city=airport.city,
                        country=airport.country,
                        is_airport_supported=True,
                        status="AVAILABLE",
                        status_reason="Available for online booking and instant dispatch.",
                        package_slug=pkg_svc.slug if pkg_svc else None,
                        package_name=pkg_svc.name if pkg_svc else None,
                        flight_type=flight_type,
                        terminal=target_mapping.terminal,
                        unit_price=unit_price,
                        total_price=tot,
                        currency=currency,
                        is_bookable_online=True,
                        available_packages=pkg_items,
                        notice_hours_required=notice_hours,
                        hours_remaining=hours_rem,
                        urgent_assistance=urgent,
                    )
                )

        all_avail = bool(service_items) and all(s.status == "AVAILABLE" for s in service_items)
        any_avail = any(s.status == "AVAILABLE" for s in service_items)
        none_avail = not any_avail
        avail_subtotal = round(sum(s.total_price for s in service_items if s.status == "AVAILABLE" and s.total_price is not None), 2)
        unavail_count = sum(1 for s in service_items if s.status == "REQUEST_REQUIRED")

        unavail_msg = None
        if none_avail:
            unavail_msg = "This airport is not currently serviceable. Submit your request and our team will check availability."
        elif unavail_count > 0:
            unavail_msg = f"{unavail_count} of your requested services require an operational request, while available services can be booked and paid for now."

        return MultiServiceAvailabilityResponse(
            success=True,
            itinerary=itinerary,
            services=service_items,
            all_available=all_avail,
            any_available=any_avail,
            none_available=none_avail,
            guest_count=guest_count,
            available_subtotal=avail_subtotal,
            total_payable=avail_subtotal,
            currency="INR",
            unavailable_services_count=unavail_count,
            unavailable_message=unavail_msg,
        )

    @classmethod
    def create_service_query(
        cls,
        db: Session,
        payload: ServiceQueryCreate,
    ) -> ServiceQueryResponse:
        """
        Creates a consolidated service query/enquiry record for unavailable services,
        reusing all passenger, flight, and itinerary data without duplicate requests.
        """
        clean_name = payload.passenger_name.strip()
        clean_email = payload.passenger_email.strip().lower()
        clean_phone = payload.passenger_phone.strip()
        clean_flight = payload.flight_num.strip().upper() if payload.flight_num else None
        clean_date = payload.service_date.strip() if payload.service_date else None
        clean_booking_ref = payload.booking_ref.strip() if payload.booking_ref else None
        clean_session_id = payload.session_id.strip() if payload.session_id else None

        # Duplicate checking: reuse existing pending request
        existing: Optional[ServiceQuery] = None
        if clean_booking_ref:
            existing = db.scalars(
                select(ServiceQuery).where(ServiceQuery.booking_ref == clean_booking_ref)
            ).first()
        elif clean_session_id:
            existing = db.scalars(
                select(ServiceQuery).where(ServiceQuery.session_id == clean_session_id)
            ).first()

        if not existing and clean_email and clean_flight and clean_date:
            existing = db.scalars(
                select(ServiceQuery).where(
                    ServiceQuery.passenger_email == clean_email,
                    ServiceQuery.flight_num == clean_flight,
                    ServiceQuery.service_date == clean_date,
                    ServiceQuery.status == "PENDING",
                )
            ).first()

        if existing:
            return ServiceQueryResponse(
                success=True,
                query_ref=existing.query_ref,
                message="Your service enquiry has been registered. Our team will review your request and contact you.",
                created_at=existing.created_at.isoformat(),
                unavailable_services=existing.unavailable_services or payload.unavailable_services,
            )

        date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        rand_suffix = secrets.token_hex(2).upper()
        query_ref = f"QRY-{date_stamp}-{rand_suffix}"

        while db.scalar(select(ServiceQuery).where(ServiceQuery.query_ref == query_ref)):
            rand_suffix = secrets.token_hex(2).upper()
            query_ref = f"QRY-{date_stamp}-{rand_suffix}"

        record = ServiceQuery(
            id=uuid.uuid4(),
            query_ref=query_ref,
            passenger_name=clean_name,
            passenger_email=clean_email,
            passenger_phone=clean_phone,
            flight_num=clean_flight,
            service_date=clean_date,
            booking_ref=clean_booking_ref,
            session_id=clean_session_id,
            itinerary=payload.itinerary,
            requested_services=payload.requested_services,
            unavailable_services=payload.unavailable_services,
            status="PENDING",
            notes=payload.notes,
            source="WEB",
        )

        existing_booking = db.scalar(
            select(Booking).where(Booking.booking_ref == query_ref)
        )
        if not existing_booking:
            dep_apt = (payload.itinerary or {}).get("departure_airport") or (payload.itinerary or {}).get("origin")
            arr_apt = (payload.itinerary or {}).get("arrival_airport") or (payload.itinerary or {}).get("destination")
            booking_record = Booking(
                id=uuid.uuid4(),
                booking_ref=query_ref,
                passenger_name=clean_name,
                passenger_email=clean_email,
                passenger_phone=clean_phone,
                service_category="Airport Assistance",
                flight_num=clean_flight,
                origin_code=dep_apt,
                dest_code=arr_apt,
                departure_time=None,
                arrival_time=None,
                service_type="Service Enquiry",
                selected_services={"services": payload.requested_services, "unavailable": payload.unavailable_services},
                service_options={},
                metadata_json={"itinerary": payload.itinerary, "source": "ENQUIRY_FLOW", "query_ref": query_ref},
                total_amount=0.0,
                currency="INR",
                status=BookingStatus.PENDING,
                notes=payload.notes or "Service enquiry submitted for non-serviceable airport availability check.",
            )
            db.add(booking_record)

        try:
            db.add(record)
            db.commit()
            db.refresh(record)
        except Exception:
            db.rollback()
            logger.exception("Failed to persist service query for %s", clean_email)
            raise

        try:
            from app.services.notification_service import NotificationService
            NotificationService.notify_booking_created(db, {
                "booking_ref": record.query_ref,
                "passenger_name": record.passenger_name,
                "passenger_email": record.passenger_email,
                "passenger_phone": record.passenger_phone,
                "passenger_count": 1,
                "flight_num": record.flight_num,
                "origin_code": (record.itinerary or {}).get("departure_airport"),
                "dest_code": (record.itinerary or {}).get("arrival_airport"),
                "airport_code": None,
                "journey_type": "SERVICE_QUERY",
                "service_category": "Airport Assistance",
                "service_type": "Service Query",
                "service_name": f"Service Query ({len(record.unavailable_services)} unavailable)",
                "total_amount": 0.0,
                "currency": "INR",
                "status": "PENDING",
                "notes": record.notes,
            })
            # A service query is a quotation request, never a confirmed booking.
            NotificationService.send_customer_whatsapp_enquiry_acknowledgement(db, {
                "booking_ref": record.query_ref,
                "passenger_name": record.passenger_name,
                "passenger_email": record.passenger_email,
                "passenger_phone": record.passenger_phone,
                "service_category": "Airport Assistance",
                "service_name": "Service Query",
                "service_type": "Service Query",
                "origin_code": (record.itinerary or {}).get("departure_airport"),
                "dest_code": (record.itinerary or {}).get("arrival_airport"),
                "status": "PENDING",
            })
        except Exception:
            pass

        return ServiceQueryResponse(
            success=True,
            query_ref=record.query_ref,
            message="Your service enquiry has been registered. Our team will review your request and contact you.",
            created_at=(record.created_at or datetime.now(timezone.utc)).isoformat(),
            unavailable_services=payload.unavailable_services,
        )

