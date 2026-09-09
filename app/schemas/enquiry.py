"""Public service quotation / enquiry schemas (non-airport, no payment)."""

from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
import re

EnquiryCategory = Literal[
    "Ground Transport",
    "Travel Support",
    "Medical Assistance",
    "Cargo & Logistics",
    "Private Charter",
]


class ServiceEnquiryCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

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
