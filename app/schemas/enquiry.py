"""Public service quotation / enquiry schemas (non-airport, no payment)."""

from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator, model_validator
import re

EnquiryCategory = Literal[
    "Ground Transport",
    "Travel Support",
    "Medical Assistance",
    "Cargo & Logistics",
    "Private Charter",
]


class ServiceEnquiryCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    passenger_name: str = Field(..., min_length=2, max_length=120, alias="passengerName")
    passenger_email: EmailStr = Field(..., alias="passengerEmail")
    passenger_phone: str = Field(..., min_length=7, max_length=40, alias="passengerPhone")

    service_category: EnquiryCategory = Field(..., alias="serviceCategory")
    service_type: str = Field(..., min_length=2, max_length=120, alias="serviceType")

    origin: Optional[str] = Field(None, max_length=200)
    destination: Optional[str] = Field(None, max_length=200)
    service_date: Optional[str] = Field(None, max_length=40, alias="serviceDate")
    notes: Optional[str] = Field(None, max_length=4000)
    details: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_transport_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Normalize pickup/origin
            if not data.get("origin"):
                pickup = data.get("pickup_location") or data.get("pickupLocation") or data.get("pickup")
                if pickup:
                    data["origin"] = pickup
            # Normalize dropoff/destination
            if not data.get("destination"):
                drop = data.get("dropoff_location") or data.get("dropoffLocation") or data.get("dropoff")
                if drop:
                    data["destination"] = drop
            # Normalize service date
            if not data.get("service_date") and not data.get("serviceDate"):
                dt = data.get("travel_date") or data.get("travelDate") or data.get("date")
                if dt:
                    data["service_date"] = dt
            # Ensure details dictionary preserves root-level transport extras if not already nested
            details = data.get("details")
            if details is None or not isinstance(details, dict):
                details = {}
                data["details"] = details
            for k in (
                "vehicle_id", "vehicleId",
                "vehicle_name", "vehicleName",
                "vehicle_category", "vehicleCategory",
                "provider", "selected_provider",
                "passenger_count", "passengerCount",
                "reference_price", "referencePrice",
                "additional_requirements",
            ):
                if k in data and k not in details:
                    details[k] = data[k]
        return data

    @field_validator("passenger_phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        clean = re.sub(r"[\s\-()]", "", v or "")
        digits = re.sub(r"[^\d+]", "", clean)
        if len(re.sub(r"\D", "", digits)) < 7:
            raise ValueError("Please provide a valid phone number.")
        return clean

    @field_validator("service_type")
    @classmethod
    def strip_service_type(cls, v: str) -> str:
        return (v or "").strip()


class ServiceEnquiryResponseData(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    booking_ref: str = Field(..., alias="bookingRef")
    passenger_name: str = Field(..., alias="passengerName")
    service_category: str = Field(..., alias="serviceCategory")
    service_type: str = Field(..., alias="serviceType")
    status: str
    created_at: Optional[str] = Field(None, alias="createdAt")


class ServiceEnquiryApiResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    success: bool = True
    data: Optional[ServiceEnquiryResponseData] = None
    message: Optional[str] = None
    error: Optional[str] = None
