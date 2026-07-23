from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from app.integrations.aerodatabox.constants import FlightStatusEnum

class FlightTimings(BaseModel):
    scheduledDeparture: Optional[str] = None
    estimatedDeparture: Optional[str] = None
    actualDeparture: Optional[str] = None
    scheduledArrival: Optional[str] = None
    estimatedArrival: Optional[str] = None
    actualArrival: Optional[str] = None
    boardingTime: Optional[str] = None
    gateClosingTime: Optional[str] = None

class DelayAnalysis(BaseModel):
    delayMinutes: int = 0
    departureDelay: int = 0
    arrivalDelay: int = 0
    reason: Optional[str] = None

class TerminalInformation(BaseModel):
    departureTerminal: Optional[str] = "3"
    arrivalTerminal: Optional[str] = "2"
    departureGate: Optional[str] = "25"
    arrivalGate: Optional[str] = "12"
    checkInCounter: Optional[str] = "Zone B"
    baggageBelt: Optional[str] = "Belt 4"

class AirportDetails(BaseModel):
    name: str
    icao: str
    iata: str
    city: str
    country: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timezone: Optional[str] = "UTC"
    elevation: Optional[int] = 0

class AirlineDetails(BaseModel):
    name: str
    icao: str
    iata: str
    country: str
    logoUrl: Optional[str] = None
    website: Optional[str] = None

class AircraftDetails(BaseModel):
    type: str
    icaoType: str
    registration: str
    manufacturer: str
    model: str
    wakeCategory: Optional[str] = "Heavy"

class CodeshareDetails(BaseModel):
    isCodeshare: bool = False
    operatingCarrier: Optional[str] = None
    marketingCarrier: Optional[str] = None

class LiveTrackingTelemetry(BaseModel):
    latitude: float
    longitude: float
    altitude: float
    heading: float
    groundSpeed: float
    verticalSpeed: Optional[float] = 0.0
    lastPositionUpdate: str

class FlightStatusData(BaseModel):
    flightNumber: str
    icao: str
    iata: str
    callsign: Optional[str] = None
    status: FlightStatusEnum = FlightStatusEnum.SCHEDULED
    airline: AirlineDetails
    aircraft: AircraftDetails
    origin: AirportDetails
    destination: AirportDetails
    terminals: TerminalInformation
    timings: FlightTimings
    delays: DelayAnalysis
    codeshare: CodeshareDetails
    liveTracking: Optional[LiveTrackingTelemetry] = None
    isCached: bool = False

class FlightValidateRequest(BaseModel):
    flightNumber: str = Field(..., example="AI101")
    date: str = Field(..., example="2026-07-24")

class FlightValidateResponseData(BaseModel):
    valid: bool
    flightNumber: str
    airline: str
    origin: str
    destination: str
    departureTime: str
    arrivalTime: str
    status: str
    terminal: str
    gate: str
    aircraft: str
    isCached: bool = False

class FlightValidateResponse(BaseModel):
    success: bool
    data: Optional[FlightValidateResponseData] = None
    error: Optional[Any] = None

class GenericFlightApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[Any] = None
