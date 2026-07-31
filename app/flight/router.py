from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from app.flight.service import FlightIntelligenceService
from app.flight.schemas import (
    FlightStatusData,
    FlightTelemetry,
    FlightValidateRequest,
    FlightValidateResponse,
    FlightValidateResponseData
)
from app.flight.exceptions import FlightDomainException

router = APIRouter(prefix="/api/flight", tags=["Flight Integration"])
flights_router = APIRouter(prefix="/api/flights", tags=["Flight Operations"])

# Dependency Injection for FlightIntelligenceService
def get_flight_service() -> FlightIntelligenceService:
    # Under clean architecture, the provider instance would be configured/bound here
    # or injected via an IoC container. Currently returns service with no active provider.
    return FlightIntelligenceService(provider=None)

@router.post("/validate", response_model=FlightValidateResponse)
def validate_flight(
    payload: FlightValidateRequest,
    service: FlightIntelligenceService = Depends(get_flight_service)
):
    """Validate a commercial flight and retrieve operational schemas."""
    from datetime import datetime
    from app.flight.schemas import FlightAirport, FlightCarrier, FlightStatusData
    from app.flight.exceptions import FlightProviderNotConfiguredException

    try:
        if payload.is_manual:
            # Construct a valid FlightStatusData object from manual fields directly
            origin = FlightAirport(
                code=(payload.origin_code or "DEL").strip().upper(),
                name=f"{(payload.origin_code or 'DEL').strip().upper()} Airport",
                city="Manual Entry Origin"
            )
            destination = FlightAirport(
                code=(payload.destination_code or "BOM").strip().upper(),
                name=f"{(payload.destination_code or 'BOM').strip().upper()} Airport",
                city="Manual Entry Destination"
            )
            carrier = FlightCarrier(
                iata=(payload.flight_num or "AI")[:2].strip().upper(),
                name=payload.airline_name or "Manual Carrier"
            )
            
            # Construct datetime strings from payload.date + time
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
                from datetime import timedelta
                arr_dt = dep_dt + timedelta(hours=2)

            flight_data = FlightStatusData(
                flight_num=(payload.flight_num or "AI101").strip().upper(),
                carrier=carrier,
                origin=origin,
                destination=destination,
                scheduled_departure=dep_dt,
                scheduled_arrival=arr_dt,
                terminal="Manual Terminal",
                status="Scheduled"
            )
            return FlightValidateResponse(
                success=True,
                data=FlightValidateResponseData(valid=True, flightData=flight_data)
            )

        # Non-manual path (auto validation): will trigger exception since provider is None
        flight_data = service.validate_flight(payload.flight_num, payload.date)
        return FlightValidateResponse(
            success=True,
            data=FlightValidateResponseData(valid=True, flightData=flight_data)
        )
    except FlightProviderNotConfiguredException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Flight Provider Not Configured. Please enter flight details manually."
        )
    except FlightDomainException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(exc)}"
        )

@flights_router.get("/status/{flight_num}", response_model=FlightStatusData)
def get_flight_status(
    flight_num: str,
    service: FlightIntelligenceService = Depends(get_flight_service)
):
    """Retrieve status telemetry for a designated flight number."""
    try:
        return service.get_flight_status(flight_num)
    except FlightDomainException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

@flights_router.get("/search", response_model=List[FlightStatusData])
def search_flights(
    query: str,
    service: FlightIntelligenceService = Depends(get_flight_service)
):
    """Search for flights based on multi-parameter query criteria."""
    try:
        return service.search_flights(query)
    except FlightDomainException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

@flights_router.get("/live/{flight_num}", response_model=FlightTelemetry)
def get_live_telemetry(
    flight_num: str,
    service: FlightIntelligenceService = Depends(get_flight_service)
):
    """Retrieve live GPS coordinates and positional data."""
    try:
        return service.get_live_telemetry(flight_num)
    except FlightDomainException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
