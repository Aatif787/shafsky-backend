from abc import ABC, abstractmethod
from typing import List, Optional
from app.flight.schemas import FlightStatusData, FlightTelemetry

class FlightProvider(ABC):
    """Abstract Base Class defining the interface for all external flight intelligence providers."""

    @abstractmethod
    def validate_flight(
        self,
        flight_num: str,
        date: str,
        direction: Optional[str] = None,
        origin_code: Optional[str] = None,
        destination_code: Optional[str] = None
    ) -> FlightStatusData:
        """Validate flight status and retrieve metadata."""
        pass

    @abstractmethod
    def get_flight_status(self, flight_num: str) -> FlightStatusData:
        """Retrieve real-time flight status."""
        pass

    @abstractmethod
    def search_flights(self, query: str) -> List[FlightStatusData]:
        """Query multiple flights matching search criteria."""
        pass

    @abstractmethod
    def get_live_telemetry(self, flight_num: str) -> FlightTelemetry:
        """Fetch real-time location and attitude coordinates."""
        pass
