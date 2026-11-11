import logging
from typing import List, Optional
from app.flight.provider import FlightProvider
from app.flight.schemas import FlightStatusData, FlightTelemetry
from app.flight.exceptions import (
    FlightProviderNotConfiguredException,
    FlightNotFoundException,
    FlightRateLimitExceededException,
    FlightProviderUnavailableException,
)

logger = logging.getLogger("shafsky.flight.service")


class FlightIntelligenceService:
    """Enterprise domain service orchestrating all flight intelligence operations with automatic provider fallback."""

    def __init__(
        self,
        provider: Optional[FlightProvider] = None,
        fallback_provider: Optional[FlightProvider] = None,
    ):
        self._provider = provider
        self._fallback_provider = fallback_provider

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
        destination_code: Optional[str] = None,
        airport_code: Optional[str] = None,
    ) -> FlightStatusData:
        """Validate flight existence and return parsed metadata with automatic fallback."""
        primary = self._get_provider()
        try:
            return primary.validate_flight(
                flight_num,
                date,
                direction,
                origin_code,
                destination_code,
                airport_code=airport_code,
            )
        except (FlightRateLimitExceededException, FlightNotFoundException, FlightProviderUnavailableException) as exc:
            if self._fallback_provider is not None:
                logger.info(
                    "[FLIGHT PROVIDER FALLBACK] Primary provider %s failed for flight %s (%s). Attempting fallback provider %s.",
                    type(primary).__name__,
                    flight_num,
                    type(exc).__name__,
                    type(self._fallback_provider).__name__,
                )
                try:
                    return self._fallback_provider.validate_flight(
                        flight_num,
                        date,
                        direction,
                        origin_code,
                        destination_code,
                        airport_code=airport_code,
                    )
                except Exception as fb_exc:
                    logger.warning(
                        "[FLIGHT PROVIDER FALLBACK FAILED] Fallback provider %s also failed for flight %s (%s). Re-raising primary exception.",
                        type(self._fallback_provider).__name__,
                        flight_num,
                        type(fb_exc).__name__,
                    )
            raise

    def get_flight_status(self, flight_num: str) -> FlightStatusData:
        """Retrieve live flight status metrics with automatic fallback."""
        primary = self._get_provider()
        try:
            return primary.get_flight_status(flight_num)
        except (FlightRateLimitExceededException, FlightNotFoundException, FlightProviderUnavailableException) as exc:
            if self._fallback_provider is not None:
                logger.info(
                    "[FLIGHT PROVIDER FALLBACK] get_flight_status primary %s failed for %s. Attempting fallback %s.",
                    type(primary).__name__,
                    flight_num,
                    type(self._fallback_provider).__name__,
                )
                try:
                    return self._fallback_provider.get_flight_status(flight_num)
                except Exception:
                    pass
            raise

    def search_flights(self, query: str) -> List[FlightStatusData]:
        """Search flight routes or operational metrics with automatic fallback."""
        primary = self._get_provider()
        try:
            res = primary.search_flights(query)
            if res:
                return res
        except Exception:
            pass

        if self._fallback_provider is not None:
            try:
                return self._fallback_provider.search_flights(query)
            except Exception:
                pass
        return []

    def get_live_telemetry(self, flight_num: str) -> FlightTelemetry:
        """Fetch real-time flight positional coordinates."""
        primary = self._get_provider()
        try:
            return primary.get_live_telemetry(flight_num)
        except Exception as exc:
            if self._fallback_provider is not None:
                try:
                    return self._fallback_provider.get_live_telemetry(flight_num)
                except Exception:
                    pass
            raise
