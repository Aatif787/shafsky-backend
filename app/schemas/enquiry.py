"""Public service quotation / enquiry schemas (non-airport, no payment)."""

import re
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator, model_validator

EnquiryCategory = Literal[
    "Ground Transport",
    "Transport",
    "Travel Support",
    "Medical Assistance",
    "Cargo & Logistics",
    "Private Charter",
    "Round Trip",
    "Ticketing",
    "Lounge",
    "Airport Assistance",
    "Hotel",
    "Hotels",
]


class ServiceEnquiryCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    passenger_name: str = Field(..., max_length=120, alias="passengerName")
    first_name: Optional[str] = Field(None, max_length=60, alias="firstName")
    last_name: Optional[str] = Field(None, max_length=60, alias="lastName")
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
            # Synthesize passenger_name from first_name / last_name if omitted
            if not data.get("passenger_name") and not data.get("passengerName"):
                fn = str(data.get("first_name") or data.get("firstName") or "").strip()
                ln = str(data.get("last_name") or data.get("lastName") or "").strip()
                if fn or ln:
                    data["passenger_name"] = f"{fn} {ln}".strip()

            # Normalize service_category (case-insensitive and alias mapping)
            raw_cat = data.get("service_category") or data.get("serviceCategory")
            if isinstance(raw_cat, str):
                cat_lower = raw_cat.strip().lower()
                if cat_lower in ("hotel", "hotels", "hotel booking", "hotel reservation"):
                    data["service_category"] = "Hotel"
                    if "serviceCategory" in data:
                        data["serviceCategory"] = "Hotel"
                elif cat_lower in ("ticketing", "air ticketing", "flight ticket"):
                    data["service_category"] = "Ticketing"
                    if "serviceCategory" in data:
                        data["serviceCategory"] = "Ticketing"
                elif cat_lower in ("round trip", "round-trip", "roundtrip"):
                    data["service_category"] = "Round Trip"
                    if "serviceCategory" in data:
                        data["serviceCategory"] = "Round Trip"
                elif cat_lower in ("transport", "ground transport"):
                    norm_trans = "Ground Transport" if "ground" in cat_lower else "Transport"
                    data["service_category"] = norm_trans
                    if "serviceCategory" in data:
                        data["serviceCategory"] = norm_trans
                elif cat_lower in ("lounge", "airport lounge"):
                    data["service_category"] = "Lounge"
                    if "serviceCategory" in data:
                        data["serviceCategory"] = "Lounge"
                elif cat_lower in ("cargo", "logistics", "cargo & logistics"):
                    data["service_category"] = "Cargo & Logistics"
                    if "serviceCategory" in data:
                        data["serviceCategory"] = "Cargo & Logistics"
                elif cat_lower in ("medical", "medical assistance", "ambulance"):
                    data["service_category"] = "Medical Assistance"
                    if "serviceCategory" in data:
                        data["serviceCategory"] = "Medical Assistance"
                elif cat_lower in ("travel support", "visa", "visa assistance"):
                    data["service_category"] = "Travel Support"
                    if "serviceCategory" in data:
                        data["serviceCategory"] = "Travel Support" 

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
                "vehicle_count", "vehicleCount",
                "reference_price", "referencePrice",
                "additional_requirements",
            ):
                if k in data and k not in details:
                    details[k] = data[k]
        return data

    @field_validator("passenger_name", mode="before")
    @classmethod
    def validate_passenger_name(cls, v: Any) -> str:
        v_str = (str(v) if v is not None else "").strip()
        if not v_str or len(v_str) < 2 or not re.match(r"^[A-Za-z\s]+$", v_str):
            raise ValueError("Please enter a valid passenger name using letters only.")
        return v_str

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
