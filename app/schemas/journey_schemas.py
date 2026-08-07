"""
Pydantic Schemas for Journey Detection Engine — Phase 1.
"""

from typing import Optional, List
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


# ─── Airport Schemas ───

class SupportedAirportResponse(BaseModel):
    id: UUID
    airport_name: str
    iata_code: str
    icao_code: Optional[str] = None
    city: str
    country: str
    timezone: str
    is_supported: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SupportedAirportListResponse(BaseModel):
    success: bool = True
    total: int
    data: List[SupportedAirportResponse] = []


# ─── Service Schemas ───

class ServiceResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    description: Optional[str] = None
    icon: Optional[str] = "Sparkles"
    display_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ServiceListResponse(BaseModel):
    success: bool = True
    total: int
    data: List[ServiceResponse] = []


# ─── Airport-Service Mapping Schemas ───

class AirportServiceResponse(BaseModel):
    id: UUID
    airport_id: UUID
    service_id: UUID
    journey_type: str
    flight_type: Optional[str] = "DOMESTIC"
    terminal: Optional[str] = None
    short_description: Optional[str] = None
    features: Optional[List[str]] = []
    additional_benefits: Optional[List[str]] = []
    min_booking_notice_hours: int
    is_available: bool
    display_priority: int
    price: float = 2499.00
    currency: str = "INR"

    # Nested service info for the frontend
    service: Optional[ServiceResponse] = None

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AirportServiceListResponse(BaseModel):
    success: bool = True
    airport_iata: str
    airport_name: str
    journey_type: Optional[str] = None
    total: int
    data: List[AirportServiceResponse] = []


# ─── Urgent Assistance Schemas ───

class UrgentAssistanceInfo(BaseModel):
    """Returned when a booking falls inside the minimum notice window."""
    is_urgent: bool = True
    message: str = "Your flight departs too soon for online booking of this service."
    hours_remaining: Optional[float] = None
    min_notice_required_hours: Optional[int] = None
    contact_phone: str = "+91-XXXXXXXXXX"
    contact_whatsapp: str = "+91-XXXXXXXXXX"
    request_callback_available: bool = True


# ─── Journey Detection Schemas ───

class JourneyDetectionRequest(BaseModel):
    departure_code: Optional[str] = Field(None, min_length=3, max_length=3, description="IATA code of departure airport")
    arrival_code: Optional[str] = Field(None, min_length=3, max_length=3, description="IATA code of arrival airport")
    journey_type: str = Field("ARRIVAL", description="ARRIVAL, DEPARTURE, or TRANSIT")
    service_date: str = Field(..., description="Travel date in YYYY-MM-DD format")
    service_time: Optional[str] = Field(None, description="Travel time in HH:MM format (24h)")
    requested_service_slug: Optional[str] = Field(None, description="Optional specific requested service slug to validate")
    terminal: Optional[str] = Field(None, description="Optional terminal selector e.g. Terminal 1 & 2, Terminal 3")


class AvailableServiceItem(BaseModel):
    """A service available at the detected airport for the given journey type."""
    airport_service_id: UUID
    service_id: UUID
    name: str
    slug: str
    description: Optional[str] = None
    short_description: Optional[str] = None
    icon: Optional[str] = "Sparkles"
    journey_type: str
    flight_type: str = "DOMESTIC"
    terminal: Optional[str] = None
    features: List[str] = []
    additional_benefits: List[str] = []
    min_booking_notice_hours: int
    display_priority: int
    price: float = 2499.00
    currency: str = "INR"
    is_bookable_online: bool = True
    urgent_assistance: Optional[UrgentAssistanceInfo] = None


class DetectedAirportInfo(BaseModel):
    iata_code: Optional[str] = None
    airport_name: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    timezone: Optional[str] = None
    is_supported: bool = False


class JourneyDetectionResponse(BaseModel):
    success: bool = True
    departure_airport: Optional[DetectedAirportInfo] = None
    arrival_airport: Optional[DetectedAirportInfo] = None
    transit_airport: Optional[DetectedAirportInfo] = None
    journey_type: str = "ARRIVAL"
    primary_airport: Optional[DetectedAirportInfo] = None  # The airport where services will be rendered
    is_supported: bool = False
    available_terminals: List[str] = []
    selected_terminal: Optional[str] = None
    available_services: List[AvailableServiceItem] = []
    urgent_assistance: Optional[UrgentAssistanceInfo] = None  # Global urgency (all services inside window)
    requested_service_slug: Optional[str] = None
    is_requested_service_available: bool = True
    unavailable_message: Optional[str] = None


class BookingWindowCheckRequest(BaseModel):
    airport_iata: str = Field(..., min_length=3, max_length=3)
    service_slug: str = Field(..., description="Service slug to check")
    journey_type: str = Field("ARRIVAL", description="ARRIVAL, DEPARTURE, or TRANSIT")
    service_date: str = Field(..., description="Travel date YYYY-MM-DD")
    service_time: str = Field(..., description="Travel time HH:MM (24h)")


class BookingWindowCheckResponse(BaseModel):
    success: bool = True
    is_bookable_online: bool = True
    hours_remaining: Optional[float] = None
    min_notice_required_hours: Optional[int] = None
    urgent_assistance: Optional[UrgentAssistanceInfo] = None


# ─── Booking Validation & Pre-Payment Schemas ───

class ServicePriceItem(BaseModel):
    slug: str
    name: str
    unit_price: float
    quantity: int
    item_subtotal: float


class PriceBreakdown(BaseModel):
    items: List[ServicePriceItem] = []
    subtotal: float
    tax_percent: float = 18.0
    tax_amount: float
    total: float
    currency: str = "INR"


class BookingValidationRequest(BaseModel):
    airport_code: str = Field(..., min_length=3, max_length=3)
    journey_type: str = Field("ARRIVAL", description="ARRIVAL, DEPARTURE, or TRANSIT")
    service_date: str = Field(..., description="Travel date YYYY-MM-DD")
    service_time: Optional[str] = Field("12:00", description="Travel time HH:MM (24h)")
    selected_service_slugs: List[str] = Field(default_factory=list, description="Selected service slugs")
    guest_count: int = Field(1, ge=1, description="Number of passengers")


class BookingValidationResponse(BaseModel):
    success: bool = True
    is_valid: bool = True
    booking_reference: str
    airport_code: str
    journey_type: str
    is_airport_supported: bool = True
    validation_messages: List[str] = []
    price_breakdown: PriceBreakdown

