from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, field_validator
import re

class BookingCreate(BaseModel):
    passenger_name: str = Field(..., min_length=2, max_length=100)
    passenger_email: EmailStr
    passenger_phone: str = Field(..., min_length=7, max_length=25)
    flight_num: str = Field(..., min_length=2, max_length=20)
    origin_code: str = Field(..., min_length=3, max_length=3)
    dest_code: str = Field(..., min_length=3, max_length=3)
    departure_time: datetime
    arrival_time: datetime
    service_type: str = Field(..., min_length=2, max_length=50)
    selected_services: Dict[str, Any] = Field(default_factory=dict)
    total_amount: float = Field(..., gt=0)
    currency: Optional[str] = "INR"
    notes: Optional[str] = None

    @field_validator("origin_code", "dest_code")
    @classmethod
    def validate_iata(cls, v: str) -> str:
        v = v.strip().upper()
        if not re.match(r"^[A-Z]{3}$", v):
            raise ValueError("Airport code must be a valid 3-letter uppercase IATA code (e.g. DEL, BOM, LHR)")
        return v

    @field_validator("flight_num")
    @classmethod
    def validate_flight_num(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("Flight number cannot be empty")
        return v

class BookingResponseData(BaseModel):
    id: str
    bookingRef: str
    passengerName: str
    passengerEmail: str
    passengerPhone: str
    flightNum: str
    originCode: str
    destCode: str
    departureTime: str
    arrivalTime: str
    serviceType: str
    selectedServices: Dict[str, Any]
    totalAmount: float
    currency: str
    status: str
    notes: Optional[str] = None
    createdAt: str

class BookingApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None

class BookingStatusUpdate(BaseModel):
    status: str

class BookingAssign(BaseModel):
    assignedTo: str
