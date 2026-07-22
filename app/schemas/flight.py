from typing import Optional
from pydantic import BaseModel

class FlightDurationRequest(BaseModel):
    duration: Optional[str] = None
    scheduledDuration: Optional[str] = None
    estimatedDuration: Optional[str] = None
    blockTime: Optional[str] = None
    flightTime: Optional[str] = None
    depTimeIso: Optional[str] = None
    arrTimeIso: Optional[str] = None
    flightNum: Optional[str] = None
    departDate: Optional[str] = None
    originCode: Optional[str] = None
    destCode: Optional[str] = None

class FlightDurationData(BaseModel):
    duration: str
    source: str

class FlightDurationResponse(BaseModel):
    success: bool
    data: Optional[FlightDurationData] = None
    error: Optional[str] = None

class FlightValidationRequest(BaseModel):
    departureTime: str
    arrivalTime: str

class FlightValidationData(BaseModel):
    isBookable: bool
    remainingTimeHours: float
    blockingMessage: Optional[str] = None

class FlightValidationResponse(BaseModel):
    success: bool
    data: Optional[FlightValidationData] = None
    error: Optional[str] = None
