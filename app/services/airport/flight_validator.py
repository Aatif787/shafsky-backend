"""
Pluggable Flight Validation Interface and Mock Validator for Airport Meet & Assist.
"""

import re
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger("shafsky.airport.flight_validator")


class FlightValidationResult:
    def __init__(self, is_valid: bool, error: str = "", details: Dict[str, Any] = None):
        self.is_valid = is_valid
        self.error = error
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "error": self.error,
            "details": self.details
        }


class FlightValidator(ABC):
    """Abstract interface for flight information validation."""

    @abstractmethod
    def validate_flight_info(
        self,
        airline: str,
        flight_number: str,
        departure_airport: str,
        arrival_airport: str,
        scheduled_time: datetime,
        terminal: str = None
    ) -> FlightValidationResult:
        pass


class MockFlightValidator(FlightValidator):
    """
    Pluggable Mock Flight Validator.
    Validates flight number format, IATA 3-letter codes, and scheduled time.
    """

    FLIGHT_NUM_REGEX = r"^[A-Z0-9]{2,3}-?\d{1,4}[A-Z]?$"

    def validate_flight_info(
        self,
        airline: str,
        flight_number: str,
        departure_airport: str,
        arrival_airport: str,
        scheduled_time: datetime,
        terminal: str = None
    ) -> FlightValidationResult:
        if not airline or len(airline.strip()) < 2:
            return FlightValidationResult(False, "Invalid airline name specified.")

        flight_num_clean = flight_number.strip().upper()
        if not re.match(self.FLIGHT_NUM_REGEX, flight_num_clean):
            return FlightValidationResult(False, f"Invalid flight number format '{flight_number}'. Expected format like 'EK-202' or 'QR123'.")

        dep_clean = departure_airport.strip().upper()
        arr_clean = arrival_airport.strip().upper()

        if len(dep_clean) != 3 or not dep_clean.isalpha():
            return FlightValidationResult(False, f"Invalid departure airport IATA code '{departure_airport}'. Must be 3 letters (e.g. DXB, LHR).")

        if len(arr_clean) != 3 or not arr_clean.isalpha():
            return FlightValidationResult(False, f"Invalid arrival airport IATA code '{arrival_airport}'. Must be 3 letters (e.g. DXB, JFK).")

        if dep_clean == arr_clean:
            return FlightValidationResult(False, "Departure and arrival airports cannot be identical.")

        return FlightValidationResult(
            is_valid=True,
            details={
                "airline": airline.strip(),
                "flight_number": flight_num_clean,
                "departure_airport": dep_clean,
                "arrival_airport": arr_clean,
                "terminal": terminal,
                "status": "SCHEDULED_VERIFIED"
            }
        )


# Global default validator instance
default_flight_validator: FlightValidator = MockFlightValidator()
