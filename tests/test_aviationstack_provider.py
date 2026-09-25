"""
Unit and integration tests for AviationStackProvider and website flight fetch flow.
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.flight.exceptions import (
    FlightNotFoundException,
    FlightRateLimitExceededException,
    InvalidFlightDateException,
    InvalidFlightNumberException,
)
from app.flight.providers.aviationstack_provider import AviationStackProvider
from app.flight.schemas import FlightStatusData

client = TestClient(app)

SAMPLE_AVIATIONSTACK_RESPONSE = {
    "flight_date": "2026-09-25",
    "flight_status": "scheduled",
    "departure": {
        "airport": "Indira Gandhi International",
        "timezone": "Asia/Kolkata",
        "iata": "DEL",
        "icao": "VIDP",
        "terminal": "3",
        "gate": "42",
        "delay": None,
        "scheduled": "2026-09-25T11:20:00+00:00",
        "estimated": None,
        "actual": None,
    },
    "arrival": {
        "airport": "Dubai International",
        "timezone": "Asia/Dubai",
        "iata": "DXB",
        "icao": "OMDB",
        "terminal": "3",
        "gate": "B12",
        "delay": None,
        "scheduled": "2026-09-25T13:40:00+00:00",
        "estimated": None,
        "actual": None,
    },
    "airline": {
        "name": "Air India",
        "iata": "AI",
        "icao": "AIC",
    },
    "flight": {
        "number": "995",
        "iata": "AI995",
        "icao": "AIC995",
        "codeshared": None,
    },
    "aircraft": {
        "registration": "VT-EXQ",
        "iata": "A320",
        "icao": "A20N",
    },
    "live": {
        "latitude": 28.55,
        "longitude": 77.10,
        "altitude": 10000.0,
        "direction": 270.0,
        "speed_horizontal": 450.0,
    },
}


def test_aviationstack_provider_init():
    provider = AviationStackProvider(api_key="test_key", base_url="https://api.aviationstack.com/v1")
    assert provider.api_key == "test_key"
    assert provider.base_url == "https://api.aviationstack.com/v1"


@patch.object(AviationStackProvider, "_make_request")
def test_aviationstack_validate_flight_success(mock_request):
    mock_request.return_value = [SAMPLE_AVIATIONSTACK_RESPONSE]
    provider = AviationStackProvider(api_key="test_key")

    result = provider.validate_flight(
        flight_num="AI995",
        date="2026-10-15",
        origin_code="DEL",
        destination_code="DXB",
    )

    assert isinstance(result, FlightStatusData)
    assert result.airline.name == "Air India"
    assert result.airline.iata == "AI"
    assert result.flight.iata == "AI995"
    assert result.departure.airport == "DEL"
    assert result.departure.terminal == "3"
    assert result.arrival.airport == "DXB"
    assert result.arrival.terminal == "3"
    # Verify future date retargeting preserved the time and set the requested date
    assert "2026-10-15" in (result.departure.scheduled or "")
    assert result.status == "Scheduled"


def test_aviationstack_validate_invalid_flight_number():
    provider = AviationStackProvider(api_key="test_key")
    with pytest.raises(InvalidFlightNumberException):
        provider.validate_flight(flight_num="???", date="2026-09-25")


def test_aviationstack_validate_invalid_date():
    provider = AviationStackProvider(api_key="test_key")
    with pytest.raises(InvalidFlightDateException):
        provider.validate_flight(flight_num="AI995", date="not-a-date")


@patch.object(AviationStackProvider, "_make_request")
def test_aviationstack_validate_flight_not_found(mock_request):
    mock_request.return_value = []
    provider = AviationStackProvider(api_key="test_key")

    with pytest.raises(FlightNotFoundException):
        provider.validate_flight(flight_num="AI995", date="2026-09-25")


@patch.object(AviationStackProvider, "_make_request")
def test_aviationstack_get_flight_status(mock_request):
    mock_request.return_value = [SAMPLE_AVIATIONSTACK_RESPONSE]
    provider = AviationStackProvider(api_key="test_key")

    result = provider.get_flight_status(flight_num="AI995")
    assert isinstance(result, FlightStatusData)
    assert result.flight.iata == "AI995"
    assert result.departure.airport == "DEL"


@patch.object(AviationStackProvider, "_make_request")
def test_aviationstack_search_flights(mock_request):
    mock_request.return_value = [SAMPLE_AVIATIONSTACK_RESPONSE]
    provider = AviationStackProvider(api_key="test_key")

    results = provider.search_flights(query="AI995")
    assert len(results) == 1
    assert results[0].flight.iata == "AI995"


@patch.object(AviationStackProvider, "_make_request")
def test_aviationstack_get_live_telemetry(mock_request):
    mock_request.return_value = [SAMPLE_AVIATIONSTACK_RESPONSE]
    provider = AviationStackProvider(api_key="test_key")

    telemetry = provider.get_live_telemetry(flight_num="AI995")
    assert telemetry.latitude == 28.55
    assert telemetry.longitude == 77.10
    assert telemetry.altitude == 10000.0


@patch.object(AviationStackProvider, "validate_flight")
def test_website_endpoint_validate_flight_with_aviationstack(mock_validate, monkeypatch):
    """Verify website POST /api/flights/validate endpoint seamlessly uses AviationStackProvider."""
    monkeypatch.setattr("app.config.settings.FLIGHT_PROVIDER", "aviationstack")
    provider = AviationStackProvider(api_key="test_key")
    mock_data = provider._normalize_flight_data(SAMPLE_AVIATIONSTACK_RESPONSE, requested_flight="AI995")
    mock_validate.return_value = mock_data

    payload = {
        "flightNumber": "AI995",
        "date": "2026-09-25",
        "originCode": "DEL",
        "destinationCode": "DXB",
    }
    response = client.post("/api/flights/validate", json=payload)
    assert response.status_code == 200
    res_json = response.json()
    assert res_json["success"] is True
    assert res_json["data"]["valid"] is True
    assert res_json["data"]["flightData"]["flight"]["iata"] == "AI995"
    assert res_json["data"]["flightData"]["departure"]["airport"] == "DEL"
    assert res_json["data"]["flightData"]["arrival"]["airport"] == "DXB"
