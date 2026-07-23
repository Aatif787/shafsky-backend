import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from app.integrations.aerodatabox.provider import FlightProvider, AeroDataBoxProvider
from app.integrations.aerodatabox.schemas import (
    FlightStatusData,
    AirlineDetails,
    AirportDetails,
    AircraftDetails,
    LiveTrackingTelemetry
)
from app.monitoring.metrics import PrometheusMetricsCollector

logger = logging.getLogger("shafsky.integrations.aerodatabox.service")

class FlightIntelligenceService:
    """
    High-Level Domain Service orchestrating flight operations.
    Depends ONLY on the FlightProvider interface.
    """
    _provider: FlightProvider = AeroDataBoxProvider()
    _events_log: List[Dict[str, Any]] = []

    @classmethod
    def get_provider(cls) -> FlightProvider:
        return cls._provider

    @classmethod
    def get_cache_key(cls, flight_number: str, date: str) -> str:
        from app.integrations.aerodatabox.cache import FlightCacheManager
        return FlightCacheManager.format_flight_key(flight_number, date)

    @classmethod
    def get_from_cache(cls, key: str) -> Optional[Any]:
        from app.integrations.aerodatabox.cache import FlightCacheManager
        return FlightCacheManager.get(key)

    @classmethod
    def set_cache(cls, key: str, data: Any, ttl_seconds: int = 600):
        from app.integrations.aerodatabox.cache import FlightCacheManager
        FlightCacheManager.set(key, data, ttl_seconds=ttl_seconds)

    @classmethod
    def get_last_successful_request(cls) -> Optional[str]:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    async def validate_flight(cls, flight_number: str, date: str) -> Dict[str, Any]:
        return await cls._provider.validate_flight(flight_number, date)

    @classmethod
    async def validate_and_fetch_flight(cls, flight_number: str, date: str) -> Dict[str, Any]:
        return await cls.validate_flight(flight_number, date)

    @classmethod
    async def get_flight_status(cls, flight_number: str, date: Optional[str] = None) -> FlightStatusData:
        return await cls._provider.get_flight_status(flight_number, date)

    @classmethod
    async def get_airline_details(cls, iata: str) -> AirlineDetails:
        return await cls._provider.get_airline_details(iata)

    @classmethod
    async def get_airport_details(cls, iata: str) -> AirportDetails:
        return await cls._provider.get_airport_details(iata)

    @classmethod
    async def get_aircraft_details(cls, registration: str) -> AircraftDetails:
        return await cls._provider.get_aircraft_details(registration)

    @classmethod
    async def search_flights(cls, query: str) -> List[Dict[str, Any]]:
        return await cls._provider.search_flights(query)

    @classmethod
    async def get_live_tracking(cls, flight_number: str) -> LiveTrackingTelemetry:
        return await cls._provider.get_live_tracking(flight_number)

    @classmethod
    async def autofill_flight_data(cls, flight_number: str, date: str) -> Dict[str, Any]:
        status = await cls.get_flight_status(flight_number, date)
        return {
            "flightNumber": status.flightNumber,
            "origin": status.origin.iata,
            "originName": status.origin.name,
            "destination": status.destination.iata,
            "destinationName": status.destination.name,
            "departureTime": status.timings.scheduledDeparture,
            "arrivalTime": status.timings.scheduledArrival,
            "terminal": status.terminals.departureTerminal,
            "gate": status.terminals.departureGate,
            "airline": status.airline.name,
            "airlineCode": status.airline.iata,
            "aircraft": status.aircraft.type,
            "aircraftReg": status.aircraft.registration,
            "status": status.status.value
        }

    @classmethod
    def emit_notification_event(cls, event_type: str, flight_number: str, details: Dict[str, Any]):
        event = {
            "eventType": event_type,
            "flightNumber": flight_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details
        }
        cls._events_log.append(event)
        logger.info(f"INTERNAL FLIGHT EVENT GENERATED [{event_type}] for Flight {flight_number}: {details}")

    @classmethod
    async def refresh_active_booking_flights(cls, active_flight_numbers: List[str]):
        """
        Background status refresh loop for active bookings (Every 10 minutes).
        Evaluates gate changes, delays, cancellations, terminal updates, and emits events.
        """
        today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for fn in active_flight_numbers:
            try:
                status = await cls.get_flight_status(fn, today_date)
                if status.delays.departureDelay > 15:
                    cls.emit_notification_event("DELAY", fn, {"delayMinutes": status.delays.departureDelay, "reason": status.delays.reason})
                if status.status.value == "Cancelled":
                    cls.emit_notification_event("CANCELLATION", fn, {"status": "Cancelled"})
                if status.status.value == "Boarding":
                    cls.emit_notification_event("BOARDING", fn, {"gate": status.terminals.departureGate})
            except Exception as e:
                logger.warning(f"Background refresh skipped for {fn}: {str(e)}")

# Backward compatibility alias
AeroDataBoxService = FlightIntelligenceService
