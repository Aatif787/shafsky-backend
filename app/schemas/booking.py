from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict
import re

class BookingCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    passenger_name: str = Field(..., min_length=2, max_length=100, alias="passengerName")
    passenger_email: EmailStr = Field(..., alias="passengerEmail")
    passenger_phone: str = Field(..., min_length=7, max_length=25, alias="passengerPhone")
    
    service_category: Optional[str] = Field(default=None, alias="serviceCategory")
    service_type: str = Field(..., min_length=2, max_length=100, alias="serviceType")
    
    # Flight details (optional for non-flight services, required for Airport Assistance)
    flight_num: Optional[str] = Field(default=None, alias="flightNum")
    origin_code: Optional[str] = Field(default=None, alias="originCode")
    dest_code: Optional[str] = Field(default=None, alias="destCode")
    departure_time: Optional[datetime] = Field(default=None, alias="departureTime")
    arrival_time: Optional[datetime] = Field(default=None, alias="arrivalTime")
    
    # Service options / selected services
    selected_services: Dict[str, Any] = Field(default_factory=dict, alias="selectedServices")
    service_options: Dict[str, Any] = Field(default_factory=dict, alias="serviceOptions")
    options: Optional[Dict[str, Any]] = Field(default=None)
    
    metadata_json: Dict[str, Any] = Field(default_factory=dict, alias="metadataJson")
    metadata: Optional[Dict[str, Any]] = Field(default=None)
    
    total_amount: float = Field(..., gt=0, alias="totalAmount")
    currency: Optional[str] = Field(default="INR")
    notes: Optional[str] = Field(default=None)

    @field_validator("origin_code", "dest_code")
    @classmethod
    def validate_iata(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().upper()
        if v and not re.match(r"^[A-Z]{3}$", v):
            raise ValueError("Airport code must be a valid 3-letter uppercase IATA code (e.g. DEL, BOM, LHR)")
        return v

    @field_validator("flight_num")
    @classmethod
    def validate_flight_num(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().upper()
        if not v:
            raise ValueError("Flight number cannot be empty")
        return v

class BookingResponseData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    bookingRef: str
    passengerName: str
    passengerEmail: str
    passengerPhone: str
    serviceCategory: Optional[str] = "Airport Assistance"
    serviceType: str
    flightNum: Optional[str] = None
    originCode: Optional[str] = None
    destCode: Optional[str] = None
    departureTime: Optional[str] = None
    arrivalTime: Optional[str] = None
    selectedServices: Dict[str, Any] = Field(default_factory=dict)
    serviceOptions: Dict[str, Any] = Field(default_factory=dict)
    metadataJson: Dict[str, Any] = Field(default_factory=dict)
    totalAmount: float
    currency: str
    status: str
    version: Optional[int] = 1
    notes: Optional[str] = None
    createdAt: str

class BookingApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None

class BookingStatusUpdate(BaseModel):
    status: str
    version: Optional[int] = None

class BookingAssign(BaseModel):
    assignedTo: str
