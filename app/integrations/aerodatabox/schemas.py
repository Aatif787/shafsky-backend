from typing import Optional, Any
from pydantic import BaseModel, Field

class FlightValidateRequest(BaseModel):
    flightNumber: str = Field(..., example="AI101")
    date: str = Field(..., example="2026-07-24")

class FlightValidateResponseData(BaseModel):
    valid: bool
    flightNumber: str
    airline: Optional[str] = "Air India"
    origin: Optional[str] = "DEL"
    destination: Optional[str] = "BOM"
    departureTime: Optional[str] = None
    arrivalTime: Optional[str] = None
    status: Optional[str] = "Scheduled"
    terminal: Optional[str] = "3"
    gate: Optional[str] = "25"
    aircraft: Optional[str] = "Boeing 787-9"
    isCached: Optional[bool] = False

class FlightValidateResponse(BaseModel):
    success: bool
    data: Optional[FlightValidateResponseData] = None
    error: Optional[Any] = None
