import re
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from pydantic import BaseModel, Field, EmailStr, field_validator, model_validator, ConfigDict
from app.models.charter_models import CharterRequestStatus


def _validate_date_str(v: Optional[str], label: str = "Date", allow_past: bool = False) -> Optional[str]:
    if not v:
        return v
    v = v.strip()
    try:
        parsed_d = datetime.strptime(v, "%Y-%m-%d").date()
        if not allow_past and parsed_d < date.today():
            raise ValueError(f"{label} cannot be in the past.")
    except ValueError as e:
        if "cannot be in the past" in str(e):
            raise e
        raise ValueError(f"{label} must be in YYYY-MM-DD format.")
    return v


class CharterLegSchema(BaseModel):
    origin: str = Field(..., min_length=2, max_length=150, description="Origin airport or city")
    destination: str = Field(..., min_length=2, max_length=150, description="Destination airport or city")
    departure_date: str = Field(..., description="Departure date in YYYY-MM-DD format")
    departure_time: Optional[str] = Field(None, description="Preferred departure time window")

    @field_validator("departure_date")
    @classmethod
    def validate_departure_date(cls, v: str) -> str:
        res = _validate_date_str(v, label="Departure date", allow_past=False)
        assert res is not None
        return res


class PassengerCountSchema(BaseModel):
    adults: int = Field(1, ge=1, le=150, description="Number of adult passengers (min 1)")
    children: int = Field(0, ge=0, le=100, description="Number of children (2-12 yrs)")
    infants: int = Field(0, ge=0, le=50, description="Number of infants (under 2 yrs)")
    total: Optional[int] = Field(None, description="Total passenger count")

    @model_validator(mode="after")
    def compute_total(self) -> "PassengerCountSchema":
        self.total = self.adults + self.children + self.infants
        return self


class PrivateCharterRequestCreate(BaseModel):
    # Customer Details
    customer_name: str = Field(..., min_length=2, max_length=120, description="Full Name")
    country_code: str = Field("+91", min_length=2, max_length=6, description="Country Calling Code")
    phone: str = Field(..., min_length=6, max_length=20, description="Phone number")
    email: EmailStr = Field(..., description="Valid contact email address")
    company: Optional[str] = Field(None, max_length=120, description="Company / Organization Name")
    preferred_contact_method: str = Field(
        "PHONE_WHATSAPP",
        description="Preferred contact method: PHONE, WHATSAPP, EMAIL, PHONE_WHATSAPP, EMAIL_WHATSAPP",
    )

    # Journey Details
    trip_type: str = Field("ONE_WAY", description="Trip type: ONE_WAY, ROUND_TRIP, MULTI_CITY")
    origin: Optional[str] = Field(None, description="Primary origin airport/city (auto-inferred from first leg)")
    destination: Optional[str] = Field(None, description="Primary destination airport/city")
    departure_date: Optional[str] = Field(None, description="Primary departure date (YYYY-MM-DD)")
    departure_time: Optional[str] = Field(None, description="Preferred departure time window")
    return_date: Optional[str] = Field(None, description="Return date for round trip (YYYY-MM-DD)")
    return_time: Optional[str] = Field(None, description="Preferred return time window")
    itinerary: List[CharterLegSchema] = Field(default_factory=list, description="List of flight legs")

    # Passengers & Aircraft Preference
    passengers: PassengerCountSchema = Field(default_factory=lambda: PassengerCountSchema(adults=1))
    aircraft_preference: str = Field("NO_PREFERENCE", description="Aircraft category preference")

    # Travel Requirements
    travel_requirements: List[str] = Field(default_factory=list, description="Selected travel requirements")
    special_requests: Optional[str] = Field(None, max_length=2000, description="Special requests / custom notes")

    @field_validator("departure_date")
    @classmethod
    def validate_top_departure_date(cls, v: Optional[str]) -> Optional[str]:
        return _validate_date_str(v, label="Departure date", allow_past=False)

    @field_validator("return_date")
    @classmethod
    def validate_top_return_date(cls, v: Optional[str]) -> Optional[str]:
        return _validate_date_str(v, label="Return date", allow_past=False)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        clean_phone = re.sub(r"[\s\-\(\)]", "", v)
        if not re.match(r"^\+?[0-9]{7,16}$", clean_phone):
            raise ValueError("Please provide a valid phone number with 7-15 digits.")
        return clean_phone

    @model_validator(mode="after")
    def validate_itinerary_and_dates(self) -> "PrivateCharterRequestCreate":
        # Ensure itinerary exists
        if not self.itinerary:
            if not self.origin or not self.destination or not self.departure_date:
                raise ValueError("Charter request requires at least one flight leg or origin/destination/departure_date.")
            try:
                self.itinerary = [
                    CharterLegSchema(
                        origin=self.origin,
                        destination=self.destination,
                        departure_date=self.departure_date,
                        departure_time=self.departure_time,
                    )
                ]
            except Exception as leg_err:
                raise ValueError(str(leg_err))

        # Sync top-level origin, destination, and departure_date from first leg if omitted
        first_leg = self.itinerary[0]
        if not self.origin:
            self.origin = first_leg.origin
        if not self.destination:
            self.destination = first_leg.destination
        if not self.departure_date:
            self.departure_date = first_leg.departure_date
        if not self.departure_time:
            self.departure_time = first_leg.departure_time

        # Validate Round Trip
        if self.trip_type == "ROUND_TRIP":
            if not self.return_date:
                raise ValueError("Return date is required for Round Trip charters.")
            try:
                dep_d = datetime.strptime(self.departure_date, "%Y-%m-%d").date()
                ret_d = datetime.strptime(self.return_date, "%Y-%m-%d").date()
                if ret_d < dep_d:
                    raise ValueError("Return date cannot be earlier than departure date.")
            except ValueError as e:
                if "cannot be earlier" in str(e):
                    raise e
                raise ValueError("Invalid return date format (expected YYYY-MM-DD).")

        # Validate Multi-City Leg chronology
        if self.trip_type == "MULTI_CITY" and len(self.itinerary) > 1:
            for i in range(1, len(self.itinerary)):
                prev_d = datetime.strptime(self.itinerary[i - 1].departure_date, "%Y-%m-%d").date()
                curr_d = datetime.strptime(self.itinerary[i].departure_date, "%Y-%m-%d").date()
                if curr_d < prev_d:
                    raise ValueError(f"Leg {i + 1} departure date cannot be earlier than Leg {i} departure date.")

        return self


class PrivateCharterRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    success: bool = True
    request_reference: str
    status: CharterRequestStatus
    customer_name: str
    trip_type: str
    origin: str
    destination: str
    departure_date: str
    departure_time: Optional[str] = None
    return_date: Optional[str] = None
    passengers: Dict[str, Any]
    aircraft_preference: str
    travel_requirements: List[str]
    created_at: datetime


class PrivateCharterAdminUpdate(BaseModel):
    status: Optional[CharterRequestStatus] = None
    assigned_staff_id: Optional[str] = None
    assigned_staff_name: Optional[str] = None
    internal_notes: Optional[str] = None


class PrivateCharterAdminListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    request_reference: str
    customer_name: str
    country_code: str
    phone: str
    email: str
    company: Optional[str] = None
    preferred_contact_method: str
    trip_type: str
    origin: str
    destination: str
    departure_date: str
    departure_time: Optional[str] = None
    return_date: Optional[str] = None
    return_time: Optional[str] = None
    itinerary: List[Dict[str, Any]]
    passengers: Dict[str, Any]
    aircraft_preference: str
    travel_requirements: List[str]
    special_requests: Optional[str] = None
    status: CharterRequestStatus
    assigned_staff_id: Optional[str] = None
    assigned_staff_name: Optional[str] = None
    internal_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
