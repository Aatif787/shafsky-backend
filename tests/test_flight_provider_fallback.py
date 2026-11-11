from unittest.mock import MagicMock
import pytest
from app.flight.service import FlightIntelligenceService
from app.flight.exceptions import (
    FlightNotFoundException,
    FlightProviderTimeoutException,
    FlightRateLimitExceededException,
    FlightProviderUnavailableException,
)
from app.flight.schemas import FlightStatusData, AirlineDetails, FlightInfo, LocationEndpointDetails, DurationDetails, AircraftDetails


def _sample_flight_status(flight_num: str = "XY320") -> FlightStatusData:
    return FlightStatusData(
        airline=AirlineDetails(name="flynas", iata="XY"),
        flight=FlightInfo(number="320", iata=flight_num),
        departure=LocationEndpointDetails(airport="LKO", scheduled="2026-11-13T17:15:00"),
        arrival=LocationEndpointDetails(airport="DMM", scheduled="2026-11-13T20:30:00"),
        duration=DurationDetails(minutes=255, formatted="4h 15m"),
        aircraft=AircraftDetails(),
        status="Scheduled",
    )


def test_primary_provider_success_does_not_call_fallback():
    mock_primary = MagicMock()
    mock_fallback = MagicMock()
    expected = _sample_flight_status("XY320")
    mock_primary.validate_flight.return_value = expected

    service = FlightIntelligenceService(provider=mock_primary, fallback_provider=mock_fallback)
    result = service.validate_flight("XY320", "2026-11-13")

    assert result == expected
    mock_primary.validate_flight.assert_called_once()
    mock_fallback.validate_flight.assert_not_called()


def test_primary_rate_limited_falls_back_to_secondary():
    mock_primary = MagicMock()
    mock_fallback = MagicMock()
    mock_primary.validate_flight.side_effect = FlightRateLimitExceededException()
    expected = _sample_flight_status("XY320")
    mock_fallback.validate_flight.return_value = expected

    service = FlightIntelligenceService(provider=mock_primary, fallback_provider=mock_fallback)
    result = service.validate_flight("XY320", "2026-11-13")

    assert result == expected
    mock_primary.validate_flight.assert_called_once()
    mock_fallback.validate_flight.assert_called_once()


def test_primary_not_found_falls_back_to_secondary():
    mock_primary = MagicMock()
    mock_fallback = MagicMock()
    mock_primary.validate_flight.side_effect = FlightNotFoundException("XY320", "2026-11-13")
    expected = _sample_flight_status("XY320")
    mock_fallback.validate_flight.return_value = expected

    service = FlightIntelligenceService(provider=mock_primary, fallback_provider=mock_fallback)
    result = service.validate_flight("XY320", "2026-11-13")

    assert result == expected
    mock_primary.validate_flight.assert_called_once()
    mock_fallback.validate_flight.assert_called_once()


def test_primary_unavailable_falls_back_to_secondary():
    mock_primary = MagicMock()
    mock_fallback = MagicMock()
    mock_primary.validate_flight.side_effect = FlightProviderUnavailableException("503 Service Unavailable")
    expected = _sample_flight_status("XY320")
    mock_fallback.validate_flight.return_value = expected

    service = FlightIntelligenceService(provider=mock_primary, fallback_provider=mock_fallback)
    result = service.validate_flight("XY320", "2026-11-13")

    assert result == expected
    mock_primary.validate_flight.assert_called_once()
    mock_fallback.validate_flight.assert_called_once()


def test_primary_timeout_falls_back_to_secondary():
    mock_primary = MagicMock()
    mock_fallback = MagicMock()
    mock_primary.validate_flight.side_effect = FlightProviderTimeoutException()
    expected = _sample_flight_status("XY320")
    mock_fallback.validate_flight.return_value = expected

    service = FlightIntelligenceService(provider=mock_primary, fallback_provider=mock_fallback)
    result = service.validate_flight("XY320", "2026-11-13")

    assert result == expected
    mock_fallback.validate_flight.assert_called_once()


def test_both_providers_fail_raises_exception():
    mock_primary = MagicMock()
    mock_fallback = MagicMock()
    mock_primary.validate_flight.side_effect = FlightNotFoundException("XY999", "2026-11-13")
    mock_fallback.validate_flight.side_effect = FlightNotFoundException("XY999", "2026-11-13")

    service = FlightIntelligenceService(provider=mock_primary, fallback_provider=mock_fallback)
    with pytest.raises(FlightNotFoundException):
        service.validate_flight("XY999", "2026-11-13")

