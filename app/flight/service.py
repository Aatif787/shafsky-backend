import logging
import time
from typing import List, Optional
from app.flight.provider import FlightProvider
from app.flight.schemas import FlightStatusData, FlightTelemetry
from app.flight.exceptions import (
    FlightDomainException,
    FlightProviderNotConfiguredException,
    FlightNotFoundException,
    FlightProviderTimeoutException,
    FlightRateLimitExceededException,
    FlightProviderUnavailableException,
    FlightScheduleUnavailableException,
)

logger = logging.getLogger("shafsky.flight.service")

LOOKUP_BUDGET_SECONDS = 22.0
_RECOVERABLE = (
    FlightRateLimitExceededException,
    FlightNotFoundException,
    FlightProviderUnavailableException,
    FlightProviderTimeoutException,
    FlightScheduleUnavailableException,
)


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

    @staticmethod
    def _limit_provider(provider: FlightProvider, budget_seconds: float) -> None:
        """Keep one provider inside the time the booking page will wait."""
        if budget_seconds < 3:
            return
        retries = getattr(provider, "max_retries", None)
        if isinstance(retries, int) and not isinstance(retries, bool):
            provider.max_retries = 1
        current_timeout = getattr(provider, "timeout", None)
        if isinstance(current_timeout, (int, float)) and not isinstance(current_timeout, bool):
            provider.timeout = min(float(current_timeout), max(3.0, budget_seconds / 2))

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
        started = time.monotonic()
        primary = self._get_provider()
        self._limit_provider(primary, LOOKUP_BUDGET_SECONDS * 0.45)
        try:
            return primary.validate_flight(
                flight_num,
                date,
                direction,
                origin_code,
                destination_code,
                airport_code=airport_code,
            )
        except _RECOVERABLE as exc:
            if self._fallback_provider is None:
                raise
            remaining = LOOKUP_BUDGET_SECONDS - (time.monotonic() - started)
            if remaining < 3:
                logger.warning(
                    "[FLIGHT PROVIDER FALLBACK SKIPPED] Budget exhausted for %s after %s.",
                    flight_num,
                    type(exc).__name__,
                )
                raise
            logger.info(
                "[FLIGHT PROVIDER FALLBACK] Primary provider %s failed for flight %s (%s). Attempting fallback provider %s.",
                type(primary).__name__,
                flight_num,
                type(exc).__name__,
                type(self._fallback_provider).__name__,
            )
            self._limit_provider(self._fallback_provider, remaining)
            try:
                return self._fallback_provider.validate_flight(
                    flight_num,
                    date,
                    direction,
                    origin_code,
                    destination_code,
                    airport_code=airport_code,
                )
            except FlightDomainException as fb_exc:
                logger.warning(
                    "[FLIGHT PROVIDER FALLBACK FAILED] Fallback provider %s failed for flight %s (%s).",
                    type(self._fallback_provider).__name__,
                    flight_num,
                    type(fb_exc).__name__,
                )
                if isinstance(exc, (FlightProviderTimeoutException, FlightProviderUnavailableException, FlightRateLimitExceededException)):
                    raise fb_exc
                if isinstance(fb_exc, FlightScheduleUnavailableException) and isinstance(exc, FlightNotFoundException):
                    raise fb_exc
                raise exc
            except Exception as fb_exc:
                logger.warning(
                    "[FLIGHT PROVIDER FALLBACK FAILED] Fallback provider %s also failed for flight %s (%s). Re-raising primary exception.",
                    type(self._fallback_provider).__name__,
                    flight_num,
                    type(fb_exc).__name__,
                )
                raise exc

    def get_flight_status(self, flight_num: str) -> FlightStatusData:
        """Retrieve live flight status metrics with automatic fallback."""
        primary = self._get_provider()
        try:
            return primary.get_flight_status(flight_num)
        except _RECOVERABLE as exc:
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
