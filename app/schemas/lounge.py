import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, EmailStr, Field, field_validator
from app.models.lounge_models import LoungeEnquiryStatus


class LoungeEnquiryCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=150, description="Customer / passenger full name")
    mobile_number: str = Field(..., min_length=8, max_length=50, description="Customer phone / mobile number")
    email_address: EmailStr = Field(..., description="Customer email address")
    whatsapp_number: Optional[str] = Field(None, max_length=50, description="Optional WhatsApp number")
    airport: str = Field(..., min_length=2, max_length=255, description="Airport name or IATA code")
    lounge_category: Optional[str] = Field(default="Domestic", max_length=50, description="'Domestic' or 'International'")
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

    @field_validator("lounge_category")
    @classmethod
    def validate_category(cls, v: Optional[str]) -> str:
        trimmed = (v or "Domestic").strip().capitalize()
        if trimmed not in ("Domestic", "International"):
            return "Domestic"
        return trimmed

    @field_validator("travel_date")
    @classmethod
    def validate_travel_date_format(cls, v: str) -> str:
        trimmed = (v or "").strip()
        try:
            datetime.strptime(trimmed, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Travel date must be a valid calendar date in YYYY-MM-DD format.")
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
    lounge_category: Optional[str] = "Domestic"
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
    timezone: Optional[str] = "Asia/Kolkata"


class CutoffRuleItem(BaseModel):
    notice_hours: int
    grace_period_hours: int
    notice_text: str


class LoungeOptionsData(BaseModel):
    airports: List[AirportOptionItem]
    lounge_types: List[str]
    domestic_lounge_types: List[str]
    international_lounge_types: List[str]
    preferred_times: List[str]
    server_time: str
    cutoff_rules: Dict[str, CutoffRuleItem]


class LoungeOptionsApiResponse(BaseModel):
    success: bool = True
    data: LoungeOptionsData


class LoungeEligibilityCheckRequest(BaseModel):
    lounge_category: str = Field(default="Domestic", description="'Domestic' or 'International'")
    travel_date: str = Field(..., description="Travel date in YYYY-MM-DD format")
    preferred_time: str = Field(..., description="Preferred time slot or HH:MM")
    airport: Optional[str] = Field(None, description="Airport IATA code or name")

    @field_validator("lounge_category")
    @classmethod
    def validate_category(cls, v: Optional[str]) -> str:
        trimmed = (v or "Domestic").strip().capitalize()
        if trimmed not in ("Domestic", "International"):
            return "Domestic"
        return trimmed


class LoungeEligibilityData(BaseModel):
    eligible: bool = Field(..., description="Whether enquiry meets notice period requirements")
    is_grace_period: bool = Field(..., description="Whether enquiry falls within 1-hour grace period")
    lounge_category: str = Field(..., description="Selected category: 'Domestic' or 'International'")
    normal_cutoff_datetime: Optional[str] = Field(None, description="ISO 8601 normal cutoff datetime in airport local timezone")
    grace_period_end_datetime: Optional[str] = Field(None, description="ISO 8601 grace period end datetime in airport timezone")
    server_datetime: str = Field(..., description="ISO 8601 server current datetime in airport local timezone")
    travel_datetime: Optional[str] = Field(None, description="ISO 8601 selected travel datetime in airport local timezone")
    lead_time_hours: float = Field(..., description="Hours remaining between server time and travel datetime")
    required_notice_hours: int = Field(..., description="Advance notice hours required (12 for Domestic, 24 for International)")
    grace_period_hours: int = Field(default=1, description="Grace period duration in hours")
    min_required_hours: int = Field(..., description="Minimum notice hours with grace period applied (11 or 23)")
    airport_timezone: str = Field(..., description="IANA timezone identifier for the selected airport")
    message: str = Field(..., description="Human-readable explanation of eligibility and cutoff status")
    ineligible_reason: Optional[str] = Field(None, description="Specific reason why enquiry is ineligible, if applicable")
    server_time: Optional[str] = Field(None, description="Backward compatibility alias for server_datetime")
    scheduled_time: Optional[str] = Field(None, description="Backward compatibility alias for travel_datetime")


class LoungeEligibilityApiResponse(BaseModel):
    success: bool = True
    data: LoungeEligibilityData
