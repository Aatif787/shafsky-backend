"""
Pydantic Schemas for Air Ticketing Domain Foundation.
"""

import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr
from app.models.ticketing import AirTicketStatus, AirTicketPassengerType


class AirTicketPassengerCreate(BaseModel):
    passenger_type: AirTicketPassengerType = AirTicketPassengerType.ADULT
    title: str = Field("MR", max_length=10)
    first_name: str = Field(..., min_length=1, max_length=80)
    last_name: str = Field(..., min_length=1, max_length=80)
    dob: Optional[str] = None
    gender: Optional[str] = None
    nationality: Optional[str] = None
    passport_number: Optional[str] = None
    e_ticket_number: Optional[str] = None
    seat_number: Optional[str] = None


class AirTicketPassengerResponse(AirTicketPassengerCreate):
    id: str
    ticket_booking_id: str
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class AirTicketBookingCreateRequest(BaseModel):
    contact_name: str = Field(..., min_length=2, max_length=120)
    contact_email: EmailStr
    contact_phone: str = Field(..., min_length=6, max_length=40)

    airline_name: str = Field(..., min_length=1, max_length=100)
    flight_number: str = Field(..., min_length=1, max_length=20)
    cabin_class: str = Field("ECONOMY", max_length=50)
    origin_iata: str = Field(..., min_length=2, max_length=10)
    destination_iata: str = Field(..., min_length=2, max_length=10)

    departure_time: datetime
    arrival_time: Optional[datetime] = None

    base_fare: float = Field(0.0, ge=0.0)
    taxes_amount: float = Field(0.0, ge=0.0)
    currency: str = Field("INR", max_length=10)
    notes: Optional[str] = None

    passengers: Optional[List[AirTicketPassengerCreate]] = []


class AirTicketBookingResponse(BaseModel):
    id: str
    booking_ref: str
    pnr_code: Optional[str] = None
    customer_id: Optional[str] = None
    contact_name: str
    contact_email: str
    contact_phone: str
    airline_name: str
    flight_number: str
    cabin_class: str
    origin_iata: str
    destination_iata: str
    departure_time: str
    arrival_time: Optional[str] = None
    passenger_count: int
    base_fare: float
    taxes_amount: float
    total_fare: float
    currency: str
    status: AirTicketStatus
    notes: Optional[str] = None
    created_at: str
    updated_at: str
    passengers: List[AirTicketPassengerResponse] = []

    class Config:
        from_attributes = True


class AirTicketTransitionRequest(BaseModel):
    target_state: AirTicketStatus
    reason: Optional[str] = None
    pnr_code: Optional[str] = None


class AirTicketApiResponse(BaseModel):
    success: bool
    data: Optional[dict | list] = None
    error: Optional[str] = None
