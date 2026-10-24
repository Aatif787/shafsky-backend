import re
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, EmailStr, Field, field_validator
from app.models.lounge_models import LoungeEnquiryStatus


class LoungeEnquiryCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=150, description="Customer / passenger full name")
    mobile_number: str = Field(..., min_length=8, max_length=50, description="Customer phone / mobile number")
    email_address: EmailStr = Field(..., description="Customer email address")
    whatsapp_number: Optional[str] = Field(None, max_length=50, description="Optional WhatsApp number")
    airport: str = Field(..., min_length=2, max_length=255, description="Airport name or IATA code")
    lounge_type: str = Field(..., min_length=2, max_length=100, description="Requested lounge category")
    travel_date: str = Field(..., min_length=8, max_length=50, description="Travel date in YYYY-MM-DD format")
    preferred_time: str = Field(..., min_length=2, max_length=100, description="Preferred arrival / departure time window")
    number_of_passengers: int = Field(default=1, ge=1, le=100, description="Number of passengers")
    flight_number: Optional[str] = Field(None, max_length=50, description="Optional flight number")
    additional_requirements: Optional[str] = Field(None, max_length=2000, description="Special requests or requirements")

    @field_validator("full_name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        trimmed = (v or "").strip()
        if not trimmed or len(trimmed) < 2:
            raise ValueError("Full name must be at least 2 characters.")
        if not re.match(r"^[A-Za-z\s]+$", trimmed):
            raise ValueError("Full name must contain letters only.")
        return trimmed

    @field_validator("mobile_number")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        trimmed = (v or "").strip()
        if not re.match(r"^[+\d\s-]{8,20}$", trimmed):
            raise ValueError("Mobile number must be a valid phone number with at least 8 digits.")
        return trimmed

    @field_validator("airport", "lounge_type", "preferred_time")
    @classmethod
    def validate_required_strings(cls, v: str) -> str:
        trimmed = (v or "").strip()
        if not trimmed:
            raise ValueError("Field cannot be empty or whitespace only.")
        return trimmed

    @field_validator("flight_number")
    @classmethod
    def format_flight_number(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return v.strip().upper()


class LoungeEnquiryResponseData(BaseModel):
    id: str
    enquiry_reference: str
    airport: str
    lounge_type: str
    travel_date: str
    preferred_time: str
    number_of_passengers: int
    flight_number: Optional[str] = None
    full_name: str
    mobile_number: str
    email_address: str
    whatsapp_number: Optional[str] = None
    additional_requirements: Optional[str] = None
    status: str
    assigned_staff_id: Optional[str] = None
    assigned_staff_name: Optional[str] = None
    internal_notes: Optional[str] = None
    created_at: str
    updated_at: str


class LoungeEnquiryApiResponse(BaseModel):
    success: bool = True
    message: str = "Lounge enquiry received successfully. Our concierge desk will contact you shortly."
    data: Optional[LoungeEnquiryResponseData] = None


class LoungeEnquiryListApiResponse(BaseModel):
    success: bool = True
    data: Dict[str, Any]


class LoungeEnquiryAdminUpdate(BaseModel):
    status: Optional[LoungeEnquiryStatus] = None
    assigned_staff_id: Optional[str] = None
    assigned_staff_name: Optional[str] = None
    internal_notes: Optional[str] = None


class AirportOptionItem(BaseModel):
    code: str
    name: str
    city: str
    country: str


class LoungeOptionsData(BaseModel):
    airports: List[AirportOptionItem]
    lounge_types: List[str]
    preferred_times: List[str]


class LoungeOptionsApiResponse(BaseModel):
    success: bool = True
    data: LoungeOptionsData
