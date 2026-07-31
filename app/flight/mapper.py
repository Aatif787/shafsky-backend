from abc import ABC, abstractmethod
from typing import Any
from app.flight.schemas import FlightStatusData, FlightAirport, FlightCarrier, FlightTelemetry

class FlightDataMapper(ABC):
    """Abstract mapping class to convert third-party API payloads into domain models."""

    @abstractmethod
    def to_flight_status(self, raw_data: Any) -> FlightStatusData:
        """Map raw provider data to FlightStatusData schema."""
        pass

    @abstractmethod
    def to_airport(self, raw_data: Any) -> FlightAirport:
        """Map raw provider data to FlightAirport schema."""
        pass

    @abstractmethod
    def to_carrier(self, raw_data: Any) -> FlightCarrier:
        """Map raw provider data to FlightCarrier schema."""
        pass

    @abstractmethod
    def to_telemetry(self, raw_data: Any) -> FlightTelemetry:
        """Map raw provider data to FlightTelemetry schema."""
        pass
