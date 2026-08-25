from typing import Dict, Any, Optional
from datetime import datetime, timezone
from fastapi import HTTPException
import re

class ServiceValidator:
    """
    Dynamic validation engine for multi-category airport services.
    Validates required fields specific to each category while reusing common validators.
    """

    CATEGORIES = {
        "Airport Assistance": [
            "Meet & Greet",
            "Fast Track",
            "Lounge Access",
            "Baggage Assistance",
            "Airport Assistance"
        ],
        "Ground Transport": [
            "Airport Transfer",
            "Luxury Sedan",
            "SUV",
            "Executive Van",
            "Ground Transport"
        ],
        "Private Charter": [
            "Light Jet",
            "Midsize Jet",
            "Heavy Jet",
            "Turboprop",
            "Helicopter",
            "Private Charter"
        ],
        "Cargo & Logistics": [
            "Express Air Freight",
            "Dangerous Goods",
            "Temperature Controlled",
            "Charter Cargo",
            "Cargo & Logistics"
        ],
        "Medical Assistance": [
            "Air Ambulance",
            "Medical Escort",
            "Stretcher Transport",
            "Wheelchair Support",
            "Medical Assistance"
        ],
        "Travel Support": [
            "Visa Assistance",
            "Travel Insurance",
            "Hotel Booking",
            "VIP Escort",
            "Travel Support"
        ]
    }

    @classmethod
    def normalize_category(cls, category: Optional[str], service_type: str) -> str:
        """Infer or normalize service category from input."""
        if category and category.strip():
            normalized = category.strip()
            # Case insensitive matching
            for cat_name in cls.CATEGORIES:
                if cat_name.lower() == normalized.lower():
                    return cat_name
            return normalized

        # Infer category from service_type if category is not explicitly provided
        st_lower = service_type.lower()
        for cat_name, child_services in cls.CATEGORIES.items():
            for child in child_services:
                if child.lower() == st_lower:
                    return cat_name

        if any(term in st_lower for term in ["sedan", "suv", "van", "transfer", "driver", "ground"]):
            return "Ground Transport"
        if any(term in st_lower for term in ["jet", "charter", "helicopter", "turboprop"]):
            return "Private Charter"
        if any(term in st_lower for term in ["cargo", "freight", "logistics"]):
            return "Cargo & Logistics"
        if any(term in st_lower for term in ["medical", "ambulance", "stretcher", "wheelchair"]):
            return "Medical Assistance"
        if any(term in st_lower for term in ["visa", "insurance", "hotel", "escort"]):
            return "Travel Support"

        # Default fallback for backward compatibility
        return "Airport Assistance"

    @classmethod
    def validate_common(cls, payload: Any) -> None:
        """Validate passenger name, email, phone."""
        if not payload.passenger_name or len(payload.passenger_name.strip()) < 2:
            raise HTTPException(status_code=400, detail="Passenger name must be at least 2 characters.")
        if not payload.passenger_email or "@" not in payload.passenger_email:
            raise HTTPException(status_code=400, detail="A valid passenger email is required.")
        if not payload.passenger_phone or len(payload.passenger_phone.strip()) < 7:
            raise HTTPException(status_code=400, detail="A valid passenger phone number is required.")

    @classmethod
    def validate_booking(cls, payload: Any) -> str:
        """
        Main entrypoint for dynamic booking validation.
        Returns the resolved category name.
        """
        cls.validate_common(payload)
        category = cls.normalize_category(getattr(payload, "service_category", None), payload.service_type)
        options = getattr(payload, "service_options", None) or getattr(payload, "selected_services", None) or {}

        if category == "Airport Assistance":
            cls._validate_airport_assistance(payload, options)
        elif category == "Ground Transport":
            cls._validate_ground_transport(payload, options)
        elif category == "Private Charter":
            cls._validate_private_charter(payload, options)
        elif category == "Cargo & Logistics":
            cls._validate_cargo_logistics(payload, options)
        elif category == "Medical Assistance":
            cls._validate_medical_assistance(payload, options)
        elif category == "Travel Support":
            cls._validate_travel_support(payload, options)

        return category

    @classmethod
    def _validate_airport_assistance(cls, payload: Any, options: Dict[str, Any]) -> None:
        if not getattr(payload, "flight_num", None):
            raise HTTPException(status_code=400, detail="Airport Assistance requires a flight number.")
        if not getattr(payload, "origin_code", None) or not getattr(payload, "dest_code", None):
            raise HTTPException(status_code=400, detail="Airport Assistance requires origin and destination IATA codes.")

        metadata = getattr(payload, "metadata_json", None) or getattr(payload, "metadata", None) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        if not isinstance(options, dict):
            options = {}

        raw_jt = (
            getattr(payload, "journey_type", None)
            or getattr(payload, "journeyType", None)
            or metadata.get("journey_type")
            or metadata.get("journeyType")
            or metadata.get("direction")
            or options.get("journey_type")
            or options.get("journeyType")
            or options.get("direction")
        )

        dep_time = getattr(payload, "departure_time", None)
        arr_time = getattr(payload, "arrival_time", None)

        if raw_jt:
            from app.services.service_airport_rules import normalize_journey_type
            jt = normalize_journey_type(str(raw_jt))
        else:
            # Infer from timestamps if journey_type was not explicitly provided
            if dep_time and not arr_time:
                jt = "DEPARTURE"
            elif arr_time and not dep_time:
                jt = "ARRIVAL"
            elif dep_time and arr_time:
                jt = "TRANSIT"
            else:
                jt = "DEPARTURE"

        if jt == "DEPARTURE":
            if not dep_time and not arr_time:
                raise HTTPException(status_code=400, detail="Departure airport assistance requires a departure time.")
        elif jt == "ARRIVAL":
            if not arr_time and not dep_time:
                raise HTTPException(status_code=400, detail="Arrival airport assistance requires an arrival time.")
        elif jt == "TRANSIT":
            if not dep_time and not arr_time:
                raise HTTPException(status_code=400, detail="Transit airport assistance requires a service date and time.")

    @classmethod
    def _validate_ground_transport(cls, payload: Any, options: Dict[str, Any]) -> None:
        pickup = options.get("pickup_location") or options.get("pickup") or getattr(payload, "origin_code", None)
        dropoff = options.get("dropoff_location") or options.get("dropoff") or getattr(payload, "dest_code", None)
        if not pickup:
            raise HTTPException(status_code=400, detail="Ground Transport requires a pickup location.")
        if not dropoff:
            raise HTTPException(status_code=400, detail="Ground Transport requires a dropoff location.")

    @classmethod
    def _validate_private_charter(cls, payload: Any, options: Dict[str, Any]) -> None:
        origin = options.get("origin") or getattr(payload, "origin_code", None)
        destination = options.get("destination") or getattr(payload, "dest_code", None)
        if not origin or not destination:
            raise HTTPException(status_code=400, detail="Private Charter requires origin and destination locations.")

    @classmethod
    def _validate_cargo_logistics(cls, payload: Any, options: Dict[str, Any]) -> None:
        origin = options.get("origin") or getattr(payload, "origin_code", None)
        destination = options.get("destination") or getattr(payload, "dest_code", None)
        if not origin or not destination:
            raise HTTPException(status_code=400, detail="Cargo & Logistics requires origin and destination points.")

    @classmethod
    def _validate_medical_assistance(cls, payload: Any, options: Dict[str, Any]) -> None:
        condition = options.get("patient_condition") or options.get("medical_notes") or getattr(payload, "notes", None)
        if not condition:
            raise HTTPException(status_code=400, detail="Medical Assistance requires medical notes or patient condition description.")

    @classmethod
    def _validate_travel_support(cls, payload: Any, options: Dict[str, Any]) -> None:
        # Travel support validates basic common fields and options
        pass
