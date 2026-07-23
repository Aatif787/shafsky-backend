from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

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

# New Flight Intelligence DTOs
class LiveFlightStatusData(BaseModel):
    flightNumber: str
    airlineCode: str
    originCode: str
    destCode: str
    status: str  # SCHEDULED, BOARDING, TAXIING, DEPARTED, EN_ROUTE, DELAYED, LANDED, CANCELLED, DIVERTED
    departureGate: Optional[str] = "TBA"
    arrivalGate: Optional[str] = "TBA"
    departureTerminal: Optional[str] = "TBA"
    arrivalTerminal: Optional[str] = "TBA"
    baggageBelt: Optional[str] = "TBA"
    checkinCounter: Optional[str] = "TBA"
    scheduledDeparture: str
    estimatedDeparture: Optional[str] = None
    actualDeparture: Optional[str] = None
    scheduledArrival: Optional[str] = None
    estimatedArrival: Optional[str] = None
    actualArrival: Optional[str] = None
    source: str = "LIVE"

class LiveFlightStatusResponse(BaseModel):
    success: bool
    data: Optional[LiveFlightStatusData] = None
    error: Optional[str] = None

class ServiceEligibilityRequest(BaseModel):
    flightNumber: str
    originCode: str
    destCode: str
    departureTime: str
    passengerCount: int = 1
    hasSpecialAssistance: bool = False
    baggageCount: int = 1

class ServiceEligibilityData(BaseModel):
    isEligible: bool
    eligibleServices: List[str]
    airportCode: str
    serviceRules: Dict[str, Any]

class ServiceEligibilityResponse(BaseModel):
    success: bool
    data: Optional[ServiceEligibilityData] = None
    error: Optional[str] = None

class PrivateCharterRequest(BaseModel):
    charterFlightNumber: str
    operatorName: str
    tailNumber: str
    aircraftModel: str
    originCode: str
    destCode: str
    departureTime: str
    passengerCount: int = 1
    fboTerminal: Optional[str] = "Executive Terminal"

class PrivateCharterData(BaseModel):
    isValid: bool
    charterReference: str
    operator: str
    tailNumber: str
    fboTerminal: str
    estimatedDeparture: str
    conciergeAssigned: bool

class PrivateCharterResponse(BaseModel):
    success: bool
    data: Optional[PrivateCharterData] = None
    error: Optional[str] = None
