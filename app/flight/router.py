from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

import os
from app.config import settings
from app.flight.service import FlightIntelligenceService
from app.flight.providers.aerodatabox_provider import AeroDataBoxProvider
from app.flight.providers.aviation_edge_provider import AviationEdgeProvider
from app.flight.providers.aviationstack_provider import AviationStackProvider
from app.flight.schemas import (
    AircraftDetails,
    AirlineDetails,
    DurationDetails,
    FlightInfo,
    FlightStatusData,
    FlightTelemetry,
    FlightValidateRequest,
    FlightValidateResponse,
    FlightValidateResponseData,
    LocationEndpointDetails
)
import logging
import re
from app.flight.exceptions import (
    FlightDomainException,
    FlightProviderNotConfiguredException,
    FlightRateLimitExceededException,
)

logger = logging.getLogger("shafsky.flight.router")

router = APIRouter(prefix="/api/flight", tags=["Flight Integration"])
flights_router = APIRouter(prefix="/api/flights", tags=["Flight Operations"])


def get_flight_service() -> FlightIntelligenceService:
    """Dependency provider creating FlightIntelligenceService with primary and fallback FlightProviders."""
    provider_name = getattr(settings, "FLIGHT_PROVIDER", "").lower()
    if os.getenv("TESTING") == "1":
        if provider_name not in ("aviationstack", "aerodatabox"):
            provider_name = "aviation_edge"
    elif not provider_name:
        provider_name = "aerodatabox"

    if provider_name == "aviation_edge":
        primary = AviationEdgeProvider()
        fallback = None
    elif provider_name == "aviationstack":
        primary = AviationStackProvider()
        fallback = AeroDataBoxProvider()
    else:
        primary = AeroDataBoxProvider()
        fallback = AviationStackProvider()
    return FlightIntelligenceService(provider=primary, fallback_provider=fallback)


def check_flight_expiry(
    flight_data: FlightStatusData,
    direction: Optional[str],
    flight_date: str,
) -> tuple[bool, Optional[str]]:
    """
    Returns (is_expired, rejection_message).
    Evaluates whether the flight event corresponding to the selected direction has already passed.
    """
    import re
    from datetime import datetime, timezone, timedelta
    from app.services.booking_cutoff import lookup_airport_timezone, airport_tzinfo

    dir_clean = (direction or "").strip().lower()
    if dir_clean not in ("departure", "arrival", "transit"):
        return False, None

    now_utc = datetime.now(timezone.utc)
    target_date_clean = (flight_date or "").strip() or now_utc.strftime("%Y-%m-%d")

    def _extract_and_anchor(raw_dt_str: Optional[str], target_date: str, is_next_day: bool = False) -> Optional[datetime]:
        if not raw_dt_str:
            return None
        s = str(raw_dt_str).strip()
        time_match = re.search(r'(?:T|\s)?(\d{1,2}:\d{2}(?::\d{2})?)', s)
        clock_time = time_match.group(1) if time_match else "00:00:00"
        if len(clock_time) == 5:
            clock_time += ":00"
        base_d = target_date
        if is_next_day:
            try:
                base_d = (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            except Exception:
                pass
        try:
            return datetime.fromisoformat(f"{base_d}T{clock_time}")
        except Exception:
            return None

    dep_raw = flight_data.departure.scheduled if flight_data.departure else None
    arr_raw = flight_data.arrival.scheduled if flight_data.arrival else None
    is_arr_next_day = False
    if dep_raw and arr_raw:
        dep_m = re.search(r'(\d{1,2}:\d{2})', str(dep_raw))
        arr_m = re.search(r'(\d{1,2}:\d{2})', str(arr_raw))
        if dep_m and arr_m and arr_m.group(1) < dep_m.group(1):
            is_arr_next_day = True

    # 1. Evaluate Departure
    if dir_clean == "departure":
        dep_ap = flight_data.departure.airport if flight_data.departure else None
        tz_name = (flight_data.departure.timezone if flight_data.departure else None) or lookup_airport_timezone(None, dep_ap)
        tz = airport_tzinfo(tz_name)
        dep_dt = _extract_and_anchor(dep_raw, target_date_clean, is_next_day=False)
        if dep_dt:
            sched_dt = dep_dt.replace(tzinfo=tz)
            if sched_dt.astimezone(timezone.utc) <= now_utc:
                return True, "This flight has already departed. Please select a future flight."

            # Check live operational status only if the flight is scheduled for today in airport local time
            sched_local = sched_dt.astimezone(tz)
            now_local = now_utc.astimezone(tz)
            if sched_local.date() == now_local.date():
                status_clean = (flight_data.status or "").strip().upper()
                if status_clean in ("CANCELLED", "CANCELED", "DIVERTED"):
                    return True, "This flight has been cancelled. Please select an active future flight."
                if status_clean in ("DEPARTED", "ACTIVE", "EN ROUTE", "IN-AIR", "IN FLIGHT", "LANDED", "ARRIVED"):
                    return True, "This flight has already departed. Please select a future flight."

    # 2. Evaluate Arrival
    if dir_clean == "arrival":
        arr_ap = flight_data.arrival.airport if flight_data.arrival else None
        tz_name = (flight_data.arrival.timezone if flight_data.arrival else None) or lookup_airport_timezone(None, arr_ap)
        tz = airport_tzinfo(tz_name)
        arr_dt = _extract_and_anchor(arr_raw, target_date_clean, is_next_day=is_arr_next_day)
        if arr_dt:
            sched_dt = arr_dt.replace(tzinfo=tz)
            if sched_dt.astimezone(timezone.utc) <= now_utc:
                return True, "This flight has already arrived. Please select a future flight."

            sched_local = sched_dt.astimezone(tz)
            now_local = now_utc.astimezone(tz)
            if sched_local.date() == now_local.date():
                status_clean = (flight_data.status or "").strip().upper()
                if status_clean in ("CANCELLED", "CANCELED", "DIVERTED"):
                    return True, "This flight has been cancelled. Please select an active future flight."
                if status_clean in ("LANDED", "ARRIVED"):
                    return True, "This flight has already arrived. Please select a future flight."

    # 3. Evaluate Transit
    if dir_clean == "transit":
        arr_ap = flight_data.arrival.airport if flight_data.arrival else None
        tz_name = (flight_data.arrival.timezone if flight_data.arrival else None) or lookup_airport_timezone(None, arr_ap)
        tz = airport_tzinfo(tz_name)
        arr_dt = _extract_and_anchor(arr_raw, target_date_clean, is_next_day=is_arr_next_day)
        if arr_dt:
            sched_arr_dt = arr_dt.replace(tzinfo=tz)
            if sched_arr_dt.astimezone(timezone.utc) <= now_utc:
                return True, "This flight has already arrived/departed. Please select a future flight."

            sched_local = sched_arr_dt.astimezone(tz)
            now_local = now_utc.astimezone(tz)
            if sched_local.date() == now_local.date():
                status_clean = (flight_data.status or "").strip().upper()
                if status_clean in ("CANCELLED", "CANCELED", "DIVERTED"):
                    return True, "This flight has been cancelled. Please select an active future flight."
                if status_clean in ("LANDED", "ARRIVED"):
                    return True, "This flight has already arrived/departed. Please select a future flight."

    return False, None


@router.post("/validate", response_model=FlightValidateResponse)
@flights_router.post("/validate", response_model=FlightValidateResponse)
def validate_flight(
    payload: FlightValidateRequest,
    service: FlightIntelligenceService = Depends(get_flight_service)
):
    """Validate a commercial flight and retrieve operational schemas."""
    from datetime import datetime, timedelta
    from app.flight.airports import build_flight_airport
    from app.flight.duration import compute_flight_duration

    flight_num = (payload.resolved_flight_num or "").strip().upper()
    flight_date = payload.resolved_date or datetime.now().strftime("%Y-%m-%d")
    direction = payload.resolved_direction

    try:
        if payload.is_manual:
            dep_date = payload.date or datetime.now().strftime("%Y-%m-%d")
            dep_time = payload.depart_time or "12:00"
            arr_date = payload.arrival_date or dep_date
            arr_time = payload.arrival_time or "14:00"

            try:
                dep_dt = datetime.fromisoformat(f"{dep_date}T{dep_time}:00")
            except Exception:
                dep_dt = datetime.now()

            try:
                arr_dt = datetime.fromisoformat(f"{arr_date}T{arr_time}:00")
            except Exception:
                arr_dt = dep_dt + timedelta(hours=2)

            orig_code = (payload.resolved_origin_code or "").strip().upper()
            dest_code = (payload.resolved_destination_code or "").strip().upper()
            fl_num = (payload.resolved_flight_num or "").strip().upper()
            if not orig_code or not dest_code or not fl_num:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Manual flight entry requires flight number, origin code, and destination code.",
                )
            orig_ap = build_flight_airport(orig_code)
            dest_ap = build_flight_airport(dest_code)
            carrier_iata = fl_num[:2]

            airline_details = AirlineDetails(
                name=payload.airline_name or f"{carrier_iata} Airways",
                iata=carrier_iata,
                logo=f"https://images.aviation-edge.com/airline-logos/{carrier_iata}.png"
            )

            flight_info = FlightInfo(
                number=fl_num[2:],
                iata=fl_num
            )

            dep_details = LocationEndpointDetails(
                airport=orig_code,
                airport_name=orig_ap.name if orig_ap else None,
                city=orig_ap.city if orig_ap else None,
                country=orig_ap.country if orig_ap else None,
                scheduled=dep_dt.isoformat()
            )

            arr_details = LocationEndpointDetails(
                airport=dest_code,
                airport_name=dest_ap.name if dest_ap else None,
                city=dest_ap.city if dest_ap else None,
                country=dest_ap.country if dest_ap else None,
                scheduled=arr_dt.isoformat()
            )

            dur_mins, dur_text = compute_flight_duration(None, dep_dt, arr_dt, fl_num)

            duration_details = DurationDetails(
                minutes=dur_mins,
                formatted=dur_text
            )

            aircraft_details = AircraftDetails()

            flight_data = FlightStatusData(
                airline=airline_details,
                flight=flight_info,
                departure=dep_details,
                arrival=arr_details,
                duration=duration_details,
                aircraft=aircraft_details,
                status="Scheduled"
            )

            is_exp, exp_msg = check_flight_expiry(flight_data, direction, dep_date)
            if is_exp:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "success": False,
                        "error": exp_msg,
                        "code": "FLIGHT_EXPIRED",
                        "data": {
                            "valid": False,
                            "is_bookable": False,
                            "is_expired": True,
                            "rejection_reason": exp_msg,
                            "flightData": flight_data.model_dump(by_alias=True),
                        }
                    }
                )

            return FlightValidateResponse(
                success=True,
                data=FlightValidateResponseData(
                    valid=True,
                    is_bookable=True,
                    is_expired=False,
                    flightData=flight_data
                )
            )

        flight_num = (payload.resolved_flight_num or "").strip().upper()
        if not flight_num:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="flightNum is required.",
            )

        # Enforce strict commercial flight number format
        clean_fl = re.sub(r"[\s\-_]+", "", flight_num).upper()
        if not re.match(r"^(?:[A-Z]{2}|[A-Z]\d|\d[A-Z]|[A-Z]{3})\d{1,4}[A-Z]?$", clean_fl):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "success": False,
                    "error": f"Invalid flight number format: '{flight_num}'. Expected standard 2-3 character airline code followed by 1-4 numbers (e.g. AI101, 6E202, EK504).",
                    "code": "INVALID_FLIGHT_NUMBER",
                    "data": {"valid": False, "flightData": None}
                }
            )

        flight_date = payload.resolved_date or datetime.now().strftime("%Y-%m-%d")
        direction = payload.resolved_direction

        flight_data = service.validate_flight(
            clean_fl,
            flight_date,
            direction=direction,
            origin_code=payload.resolved_origin_code,
            destination_code=payload.resolved_destination_code,
            airport_code=payload.airport_code,
        )

        is_exp, exp_msg = check_flight_expiry(flight_data, direction, flight_date)
        if is_exp:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "success": False,
                    "error": exp_msg,
                    "code": "FLIGHT_EXPIRED",
                    "data": {
                        "valid": False,
                        "is_bookable": False,
                        "is_expired": True,
                        "rejection_reason": exp_msg,
                        "flightData": flight_data.model_dump(by_alias=True),
                    }
                }
            )

        return FlightValidateResponse(
            success=True,
            data=FlightValidateResponseData(
                valid=True,
                is_bookable=True,
                is_expired=False,
                flightData=flight_data
            )
        )
    except FlightRateLimitExceededException:
        logger.warning(
            "[ROUTER FLIGHT RATE LIMIT] External provider rate limited for %s. Prompting manual flight entry.",
            flight_num,
        )
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "success": False,
                "error": "Live flight tracking service rate limit reached. Please enter flight details manually or try again shortly.",
                "code": "RATE_LIMIT_EXCEEDED",
                "data": {"valid": False, "flightData": None}
            }
        )
    except FlightDomainException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": exc.message,
                "code": exc.code,
                "data": {"valid": False, "flightData": None}
            }
        )
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": "Flight lookup failed. Please try again or enter the flight details manually.",
                "code": "FLIGHT_ERROR",
                "data": {"valid": False, "flightData": None}
            }
        )


@flights_router.get("/search", response_model=List[FlightStatusData])
def search_flights(
    query: str,
    service: FlightIntelligenceService = Depends(get_flight_service)
):
    """Search for flights based on multi-parameter query criteria."""
    try:
        return service.search_flights(query)
    except FlightDomainException as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error": exc.message, "code": exc.code})


@flights_router.get("/{flight_num}", response_model=FlightStatusData)
@flights_router.get("/status/{flight_num}", response_model=FlightStatusData)
def get_flight_status(
    flight_num: str,
    service: FlightIntelligenceService = Depends(get_flight_service)
):
    """Retrieve status telemetry for a designated flight number."""
    try:
        return service.get_flight_status(flight_num)
    except FlightDomainException as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error": exc.message, "code": exc.code})


@flights_router.get("/live/{flight_num}", response_model=FlightTelemetry)
def get_live_telemetry(
    flight_num: str,
    service: FlightIntelligenceService = Depends(get_flight_service)
):
    """Retrieve live GPS coordinates and positional data."""
    try:
        return service.get_live_telemetry(flight_num)
    except FlightDomainException as exc:
        raise HTTPException(status_code=exc.status_code, detail={"error": exc.message, "code": exc.code})
