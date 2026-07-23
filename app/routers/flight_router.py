from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.database import get_db, SessionLocal
from app.schemas.flight import (
    FlightDurationRequest,
    FlightDurationResponse,
    FlightDurationData,
    FlightValidationRequest,
    FlightValidationResponse,
    FlightValidationData,
    LiveFlightStatusResponse,
    ServiceEligibilityRequest,
    ServiceEligibilityResponse,
    PrivateCharterRequest,
    PrivateCharterResponse
)
from app.services.flight_duration_resolver import FlightDurationResolver
from app.services.flight_service import FlightService
from app.services.flight_cache import FlightCache

from app.integrations.aerodatabox.service import AeroDataBoxService
from app.integrations.aerodatabox.schemas import FlightValidateRequest, FlightValidateResponse, FlightValidateResponseData
from app.integrations.aerodatabox.exceptions import AeroDataBoxException

router = APIRouter(prefix="/api/flight", tags=["Flight Intelligence & Airport Operations"])
flights_router = APIRouter(prefix="/api/flights", tags=["Flight Intelligence & Airport Operations"])

@flights_router.post("/validate", response_model=FlightValidateResponse)
@router.post("/validate-aerodatabox", response_model=FlightValidateResponse)
async def validate_aerodatabox_flight(payload: FlightValidateRequest):
    try:
        data = await AeroDataBoxService.validate_and_fetch_flight(payload.flightNumber, payload.date)
        return FlightValidateResponse(
            success=True,
            data=FlightValidateResponseData(**data)
        )
    except AeroDataBoxException as ae:
        raise HTTPException(status_code=ae.status_code, detail={"code": ae.code, "message": ae.message})
    except Exception as e:
        raise HTTPException(status_code=503, detail={"code": "FLIGHT_PROVIDER_UNAVAILABLE", "message": str(e)})

@router.post("/duration", response_model=FlightDurationResponse)
async def resolve_flight_duration(payload: FlightDurationRequest):
    result = await FlightDurationResolver.resolve(payload.model_dump())
    return FlightDurationResponse(
        success=True,
        data=FlightDurationData(
            duration=result["duration"],
            source=result["source"]
        )
    )

@router.post("/validate", response_model=FlightValidationResponse)
async def validate_flight_eligibility(payload: FlightValidationRequest):
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        dep_str = payload.departureTime.replace("Z", "+00:00")
        dep_date = datetime.fromisoformat(dep_str).replace(tzinfo=None)

        if dep_date < now:
            return FlightValidationResponse(
                success=True,
                data=FlightValidationData(
                    isBookable=False,
                    remainingTimeHours=0.0,
                    blockingMessage="This flight has already departed."
                )
            )

        diff_hours = (dep_date - now).total_seconds() / 3600.0

        if diff_hours < 6.0:
            return FlightValidationResponse(
                success=True,
                data=FlightValidationData(
                    isBookable=False,
                    remainingTimeHours=round(diff_hours, 1),
                    blockingMessage=f"Bookings require at least 6 hours advance notice. Departure is in {round(diff_hours, 1)} hours."
                )
            )

        return FlightValidationResponse(
            success=True,
            data=FlightValidationData(
                isBookable=True,
                remainingTimeHours=round(diff_hours, 1)
            )
        )
    except Exception as e:
        return FlightValidationResponse(
            success=False,
            error=f"Flight validation failed: {str(e)}"
        )

@router.get("/track/{flight_number}", response_model=LiveFlightStatusResponse)
async def track_live_flight(
    flight_number: str,
    db: Session = Depends(get_db)
):
    try:
        status_data = await FlightService.get_live_flight_status(flight_number, db=db)
        return LiveFlightStatusResponse(success=True, data=status_data)
    except Exception as e:
        return LiveFlightStatusResponse(success=False, error=f"Live flight tracking failed: {str(e)}")

@router.get("/airports/{airport_code}")
async def get_airport_operations(airport_code: str):
    code_up = airport_code.upper()
    return {
        "success": True,
        "data": {
            "airportCode": code_up,
            "name": f"{code_up} International Airport",
            "operatingHours": "24/7",
            "terminals": ["T1", "T2", "T3"],
            "supportedServices": ["MEET_AND_ASSIST", "VIP_LOUNGE", "FAST_TRACK", "BUGGY", "WHEELCHAIR", "PORTER"],
            "fboTerminal": "Shafsky Executive Terminal"
        }
    }

@router.post("/eligibility", response_model=ServiceEligibilityResponse)
async def check_service_eligibility(payload: ServiceEligibilityRequest):
    data = FlightService.determine_service_eligibility(payload)
    return ServiceEligibilityResponse(success=True, data=data)

@router.post("/charter/validate", response_model=PrivateCharterResponse)
async def validate_private_charter(payload: PrivateCharterRequest):
    data = FlightService.validate_private_charter(payload)
    return PrivateCharterResponse(success=True, data=data)

@router.post("/refresh-cache")
async def refresh_flight_cache():
    removed = FlightCache.clear_expired()
    return {"success": True, "message": f"Expired flight cache records cleared ({removed} keys evicted)."}
