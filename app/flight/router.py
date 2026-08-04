from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.flight.service import FlightIntelligenceService
from app.flight.providers.aviation_edge_provider import AviationEdgeProvider
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
from app.flight.exceptions import FlightDomainException, FlightProviderNotConfiguredException

router = APIRouter(prefix="/api/flight", tags=["Flight Integration"])
flights_router = APIRouter(prefix="/api/flights", tags=["Flight Operations"])


def get_flight_service() -> FlightIntelligenceService:
    """Dependency provider creating FlightIntelligenceService with AviationEdgeProvider."""
    provider = AviationEdgeProvider()
    return FlightIntelligenceService(provider=provider)


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

            orig_code = (payload.origin_code or "DEL").strip().upper()
            dest_code = (payload.destination_code or "BOM").strip().upper()
            orig_ap = build_flight_airport(orig_code)
            dest_ap = build_flight_airport(dest_code)

            fl_num = (payload.resolved_flight_num or "AI101").strip().upper()
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
            return FlightValidateResponse(
                success=True,
                data=FlightValidateResponseData(valid=True, flightData=flight_data)
            )

        flight_num = payload.resolved_flight_num or "AI302"
        flight_date = payload.resolved_date or datetime.now().strftime("%Y-%m-%d")
        direction = payload.resolved_direction

        flight_data = service.validate_flight(
            flight_num,
            flight_date,
            direction=direction,
            origin_code=payload.origin_code,
            destination_code=payload.destination_code
        )
        return FlightValidateResponse(
            success=True,
            data=FlightValidateResponseData(valid=True, flightData=flight_data)
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
                "error": f"An unexpected error occurred: {str(exc)}",
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
