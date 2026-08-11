"""
FastAPI Router for Journey Detection Engine — Phase 1.

Public endpoints (no authentication required for browsing):
- GET  /api/journey/airports              — List all active supported airports
- GET  /api/journey/airports/{iata_code}  — Get single airport details
- GET  /api/journey/services              — List all active services
- POST /api/journey/detect                — Journey detection with service availability
- GET  /api/journey/airports/{iata_code}/services — Services at a specific airport
- POST /api/journey/check-booking-window  — Validate booking window for a service
"""

from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.journey_engine import JourneyDetectionEngine
from app.services.service_config_service import ServiceConfigService
from app.schemas.journey_schemas import (
    SupportedAirportResponse,
    SupportedAirportListResponse,
    ServiceResponse,
    ServiceListResponse,
    AirportServiceResponse,
    AirportServiceListResponse,
    JourneyDetectionRequest,
    JourneyDetectionResponse,
    BookingWindowCheckRequest,
    BookingWindowCheckResponse,
    BookingValidationRequest,
    BookingValidationResponse,
)

router = APIRouter(prefix="/api/journey", tags=["Journey Detection Engine"])


# ─── Airport Endpoints ───

@router.get(
    "/airports",
    response_model=SupportedAirportListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Supported Airports",
    description="Returns all active airports where Shafsky operates. Public endpoint.",
)
def list_supported_airports(db: Session = Depends(get_db)):
    airports = JourneyDetectionEngine.get_supported_airports(db)
    return SupportedAirportListResponse(
        success=True,
        total=len(airports),
        data=[SupportedAirportResponse.model_validate(a) for a in airports],
    )


@router.get(
    "/airports/{iata_code}",
    response_model=SupportedAirportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Airport by IATA Code",
    description="Returns details for a specific supported airport. Public endpoint.",
)
def get_airport_by_iata(iata_code: str, db: Session = Depends(get_db)):
    airport = JourneyDetectionEngine.get_airport_by_iata(db, iata_code)
    if not airport:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Airport with IATA code '{iata_code.upper()}' not found.",
        )
    return SupportedAirportResponse.model_validate(airport)


# ─── Service Endpoints ───

@router.get(
    "/services",
    response_model=ServiceListResponse,
    status_code=status.HTTP_200_OK,
    summary="List All Services",
    description="Returns all globally active concierge services. Public endpoint.",
)
def list_services(db: Session = Depends(get_db)):
    services = JourneyDetectionEngine.get_all_services(db)
    return ServiceListResponse(
        success=True,
        total=len(services),
        data=[ServiceResponse.model_validate(s) for s in services],
    )


# ─── Airport-Service Mapping Endpoint ───

@router.get(
    "/airports/{iata_code}/services",
    response_model=AirportServiceListResponse,
    status_code=status.HTTP_200_OK,
    summary="Services at Airport",
    description="Returns available services at a specific airport. Optionally filter by journey_type (ARRIVAL, DEPARTURE, TRANSIT).",
)
def get_services_at_airport(
    iata_code: str,
    journey_type: Optional[str] = Query(None, description="Filter by journey type: ARRIVAL, DEPARTURE, TRANSIT"),
    flight_type: Optional[str] = Query(None, description="Filter by flight type: DOMESTIC, INTERNATIONAL"),
    terminal: Optional[str] = Query(None, description="Filter by terminal e.g. Terminal 1 & 2, Terminal 3"),
    include_inactive: bool = Query(False, description="Whether to include inactive/draft services"),
    db: Session = Depends(get_db),
):
    airport = JourneyDetectionEngine.get_airport_by_iata(db, iata_code)
    if not airport:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Airport with IATA code '{iata_code.upper()}' not found.",
        )

    mappings = JourneyDetectionEngine.get_services_for_airport(
        db, iata_code, journey_type, flight_type, terminal, include_inactive=include_inactive
    )

    data = []
    for m in mappings:
        item = AirportServiceResponse.model_validate(m)
        if m.service:
            item.service = ServiceResponse.model_validate(m.service)
        data.append(item)

    return AirportServiceListResponse(
        success=True,
        airport_iata=airport.iata_code,
        airport_name=airport.airport_name,
        journey_type=journey_type.upper() if journey_type else None,
        total=len(data),
        data=data,
    )


# ─── Journey Detection Endpoint ───

@router.post(
    "/detect",
    response_model=JourneyDetectionResponse,
    status_code=status.HTTP_200_OK,
    summary="Detect Journey & Load Services",
    description=(
        "Given departure/arrival airport codes, journey type, and travel date/time, "
        "detects the primary airport, loads available services, and checks booking windows. "
        "Returns unsupported airport info gracefully if airport is not in the database."
    ),
)
def detect_journey(
    data: JourneyDetectionRequest,
    db: Session = Depends(get_db),
):
    return JourneyDetectionEngine.detect_journey(
        db=db,
        departure_code=data.departure_code,
        arrival_code=data.arrival_code,
        journey_type=data.journey_type,
        service_date=data.service_date,
        service_time=data.service_time,
        requested_service_slug=data.requested_service_slug,
        terminal=data.terminal,
    )


# ─── Booking Window Check Endpoint ───

@router.post(
    "/check-booking-window",
    response_model=BookingWindowCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Check Booking Window",
    description=(
        "Validates whether a specific service can be booked online given the time constraints. "
        "If the booking falls inside the minimum notice window, returns urgent assistance info."
    ),
)
def check_booking_window(
    data: BookingWindowCheckRequest,
    db: Session = Depends(get_db),
):
    return JourneyDetectionEngine.check_service_booking_window(
        db=db,
        airport_iata=data.airport_iata,
        service_slug=data.service_slug,
        journey_type=data.journey_type,
        service_date=data.service_date,
        service_time=data.service_time,
    )


# ─── Booking Validation & Pre-Payment Endpoint ───

@router.post(
    "/validate-booking",
    status_code=status.HTTP_200_OK,
    summary="Validate Booking & Generate Pre-Payment Breakdown",
    description=(
        "Performs complete pre-payment booking validation: verifies airport support, service availability, "
        "lead time notice constraints, calculates dynamic price breakdown from DB, and generates authoritative validation response."
    ),
)
def validate_booking(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    if "selected_service_slugs" in payload and "airport_code" in payload:
        try:
            req = BookingValidationRequest(**payload)
            return JourneyDetectionEngine.validate_booking(
                db=db,
                airport_code=req.airport_code,
                journey_type=req.journey_type,
                service_date=req.service_date,
                service_time=req.service_time,
                selected_service_slugs=req.selected_service_slugs,
                guest_count=req.guest_count,
            )
        except Exception:
            pass

    return ServiceConfigService.validate_authoritative_booking(db, payload)
