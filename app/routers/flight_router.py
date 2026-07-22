from fastapi import APIRouter
from datetime import datetime
from app.schemas.flight import (
    FlightDurationRequest,
    FlightDurationResponse,
    FlightDurationData,
    FlightValidationRequest,
    FlightValidationResponse,
    FlightValidationData
)
from app.services.flight_duration_resolver import FlightDurationResolver

router = APIRouter(prefix="/api/flight", tags=["Flight Services"])

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
        now = datetime.utcnow()
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
