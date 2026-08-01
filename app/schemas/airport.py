"""
Pydantic Schemas for Airport Meet & Assist Module — Phase C.1.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


class PassengerCreate(BaseModel):
    full_name: str = Field(..., min_length=1, description="Full passenger name", example="John Doe")
    gender: Optional[str] = Field(None, example="MALE")
    dob: Optional[str] = Field(None, example="1985-06-15")
    nationality: Optional[str] = Field(None, example="United States")
    passport_number: Optional[str] = Field(None, example="A12345678")
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    is_primary: bool = Field(default=False, description="Is primary contact passenger")


class PassengerResponse(BaseModel):
    id: UUID
    booking_id: UUID
    full_name: str
    gender: Optional[str] = None
    dob: Optional[str] = None
    nationality: Optional[str] = None
    passport_number: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    is_primary: bool
    created_at: datetime

    class Config:
        from_attributes = True


class FlightDetailCreate(BaseModel):
    airline: str = Field(..., example="Emirates")
    flight_number: str = Field(..., example="EK-202")
    departure_airport: str = Field(..., min_length=3, max_length=3, example="DXB")
    arrival_airport: str = Field(..., min_length=3, max_length=3, example="JFK")
    terminal: Optional[str] = Field(None, example="Terminal 3")
    scheduled_time: datetime = Field(..., description="Scheduled departure/arrival UTC timestamp")
    flight_type: str = Field(default="ARRIVAL", description="ARRIVAL, DEPARTURE, or TRANSIT")


class FlightDetailResponse(BaseModel):
    id: UUID
    booking_id: UUID
    airline: str
    flight_number: str
    departure_airport: str
    arrival_airport: str
    terminal: Optional[str] = None
    scheduled_time: datetime
    flight_type: str
    created_at: datetime

    class Config:
        from_attributes = True


class ServiceAddonCreate(BaseModel):
    service_code: str = Field(..., description="MEET_GREET, FAST_TRACK, BUGGY, LOUNGE, PORTER, VIP_ASSIST", example="FAST_TRACK")
    quantity: int = Field(default=1, ge=1)


class ServiceAddonResponse(BaseModel):
    id: UUID
    booking_id: UUID
    service_code: str
    quantity: int
    unit_price: float
    total_price: float
    created_at: datetime

    class Config:
        from_attributes = True


class AirportBookingCreate(BaseModel):
    service_package: str = Field(default="STANDARD_MEET_GREET", description="Service package name")
    special_instructions: Optional[str] = None
    passengers: List[PassengerCreate] = Field(..., min_items=1, description="List of passengers")
    flight_detail: FlightDetailCreate = Field(..., description="Flight information")
    addons: List[ServiceAddonCreate] = Field(default_factory=list, description="Addon service selections")


class AirportBookingUpdate(BaseModel):
    service_package: Optional[str] = None
    special_instructions: Optional[str] = None


class AirportBookingResponse(BaseModel):
    id: UUID
    booking_reference: str
    customer_id: str
    service_package: str
    status: str
    total_price: float
    currency: str
    special_instructions: Optional[str] = None
    workflow_instance_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

    passengers: List[PassengerResponse] = []
    flight_details: List[FlightDetailResponse] = []
    addons: List[ServiceAddonResponse] = []

    # Shared domain & workflow aggregations (populated on detail lookup)
    workflow_state: Optional[str] = None
    assignments: List[Dict[str, Any]] = []
    attachments: List[Dict[str, Any]] = []

    class Config:
        from_attributes = True


class PaginatedAirportBookingResponse(BaseModel):
    success: bool = True
    total: int
    limit: int
    offset: int
    data: List[AirportBookingResponse] = []


class AirportTransitionRequest(BaseModel):
    action: str = Field(..., description="Workflow action name (e.g. CONFIRM, ASSIGN, IN_PROGRESS, COMPLETE, CANCEL)")
    payload: Optional[Dict[str, Any]] = Field(default_factory=dict)


class AssignStaffRequest(BaseModel):
    staff_id: UUID
    role_type: str = Field(default="GREETER", description="Staff role (GREETER, DRIVER, DISPATCHER, etc.)")
    notes: Optional[str] = None


class RegisterAttachmentRequest(BaseModel):
    filename: str
    storage_path: str
    category: str = Field(default="PASSPORT", description="PASSPORT, VISA, TICKET, SUPPORTING")
    access_level: str = Field(default="STAFF", description="PUBLIC, CUSTOMER, STAFF, ADMIN")
