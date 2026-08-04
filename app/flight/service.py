from typing import List, Optional
from app.flight.provider import FlightProvider
from app.flight.schemas import FlightStatusData, FlightTelemetry
from app.flight.exceptions import FlightProviderNotConfiguredException

class FlightIntelligenceService:
    """Enterprise domain service orchestrating all flight intelligence operations."""

    def __init__(self, provider: Optional[FlightProvider] = None):
        self._provider = provider

    def _get_provider(self) -> FlightProvider:
        if not self._provider:
            raise FlightProviderNotConfiguredException()
        return self._provider

    def validate_flight(
        self,
        flight_num: str,
        date: str,
        direction: Optional[str] = None,
        origin_code: Optional[str] = None,
        destination_code: Optional[str] = None
    ) -> FlightStatusData:
        """Validate flight existence and return parsed metadata."""
        return self._get_provider().validate_flight(
            flight_num, date, direction, origin_code, destination_code
        )

    def get_flight_status(self, flight_num: str) -> FlightStatusData:
        """Retrieve live flight status metrics."""
        return self._get_provider().get_flight_status(flight_num)

    def search_flights(self, query: str) -> List[FlightStatusData]:
        """Search flight routes or operational metrics."""
        return self._get_provider().search_flights(query)

    def get_live_telemetry(self, flight_num: str) -> FlightTelemetry:
        """Fetch real-time flight positional coordinates."""
        return self._get_provider().get_live_telemetry(flight_num)
