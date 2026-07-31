from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field

class FlightAirport(BaseModel):
    code: str = Field(..., description="3-letter IATA code")
    name: Optional[str] = None
    city: Optional[str] = None
    timezone: Optional[str] = None

class FlightCarrier(BaseModel):
    iata: str = Field(..., description="2-letter airline code")
    name: Optional[str] = None

class FlightStatusData(BaseModel):
    flight_num: str
    carrier: FlightCarrier
    origin: FlightAirport
    destination: FlightAirport
    scheduled_departure: datetime
    scheduled_arrival: datetime
    terminal: Optional[str] = None
    gate: Optional[str] = None
    status: str

class FlightTelemetry(BaseModel):
    latitude: float
    longitude: float
    altitude: float
    heading: float
    speed: float

class FlightValidateRequest(BaseModel):
    flight_num: str = Field(..., alias="flightNumber")
    date: str
    is_manual: Optional[bool] = Field(default=False, alias="isManual")
    depart_time: Optional[str] = Field(default=None, alias="departTime")
    origin_code: Optional[str] = Field(default=None, alias="originCode")
    destination_code: Optional[str] = Field(default=None, alias="destinationCode")
    airline_name: Optional[str] = Field(default=None, alias="airlineName")
    arrival_date: Optional[str] = Field(default=None, alias="arrivalDate")
    arrival_time: Optional[str] = Field(default=None, alias="arrivalTime")

    class Config:
        populate_by_name = True

class FlightValidateResponseData(BaseModel):
    valid: bool
    flight_data: Optional[FlightStatusData] = Field(default=None, alias="flightData")

    class Config:
        populate_by_name = True

class FlightValidateResponse(BaseModel):
    success: bool
    data: Optional[FlightValidateResponseData] = None
    error: Optional[str] = None
