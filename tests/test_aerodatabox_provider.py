import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.flight.providers.aerodatabox_provider import AeroDataBoxProvider
from app.flight.exceptions import (
    InvalidFlightNumberException,
    FlightNotFoundException,
    FlightRateLimitExceededException,
)
from app.flight.schemas import FlightStatusData

client = TestClient(app)

SAMPLE_AERODATABOX_PAYLOAD = [
    {
        "departure": {
            "airport": {
                "icao": "VIDP",
                "iata": "DEL",
                "name": "New Delhi Indira Gandhi",
                "municipalityName": "New Delhi",
                "countryCode": "IN",
                "timeZone": "Asia/Kolkata",
            },
            "scheduledTime": {
                "utc": "2026-10-28 16:05Z",
                "local": "2026-10-28 21:35+05:30",
            },
            "terminal": "3",
        },
        "arrival": {
            "airport": {
                "icao": "LIRF",
                "iata": "FCO",
                "name": "Rome Leonardo da Vinci",
                "municipalityName": "Rome",
                "countryCode": "IT",
                "timeZone": "Europe/Rome",
            },
            "scheduledTime": {
                "utc": "2026-10-29 02:20Z",
                "local": "2026-10-29 03:20+01:00",
            },
            "terminal": "3",
        },
        "number": "AI 101",
        "status": "Expected",
        "aircraft": {"model": "Airbus A350-900"},
        "airline": {"name": "Air India", "iata": "AI", "icao": "AIC"},
    }
]


def test_aerodatabox_rejects_malformed_flight_number():
    provider = AeroDataBoxProvider()
    with pytest.raises(InvalidFlightNumberException):
        provider.validate_flight("0JHHSDSKDSIJD", "2026-10-28")

    with pytest.raises(InvalidFlightNumberException):
        provider.validate_flight("RANDOMTEXT", "2026-10-28")


@patch.object(AeroDataBoxProvider, "_make_request")
def test_aerodatabox_validate_flight_success(mock_request):
    mock_request.return_value = SAMPLE_AERODATABOX_PAYLOAD
    provider = AeroDataBoxProvider()

    res = provider.validate_flight("AI101", "2026-10-28")

    assert isinstance(res, FlightStatusData)
    assert res.airline.name == "Air India"
    assert res.airline.iata == "AI"
    assert res.flight.iata == "AI101"
    assert res.departure.airport == "DEL"
    assert res.departure.terminal == "3"
    assert "21:35" in (res.departure.scheduled or "")
    assert res.arrival.airport == "FCO"
    assert "03:20" in (res.arrival.scheduled or "")
    assert res.status == "Expected"


@patch.object(AeroDataBoxProvider, "_make_request")
def test_aerodatabox_validate_flight_not_found(mock_request):
    mock_request.return_value = []
    provider = AeroDataBoxProvider()

    with pytest.raises(FlightNotFoundException):
        provider.validate_flight("AI9999", "2026-10-28")


def test_endpoint_validate_flight_rejects_gibberish():
    """Verify endpoint rejects random gibberish input with 400 Bad Request."""
    payload = {
        "flightNumber": "0JHHSDSKDSIJD",
        "departDate": "2026-10-28",
        "originCode": "DEL",
        "destinationCode": "BOM",
    }
    response = client.post("/api/flight/validate", json=payload)
    assert response.status_code == 400
    res_json = response.json()
    assert res_json["success"] is False
    assert res_json["code"] == "INVALID_FLIGHT_NUMBER"
    assert res_json["data"]["valid"] is False


@patch.object(AeroDataBoxProvider, "validate_flight")
def test_endpoint_validate_flight_success(mock_validate, monkeypatch):
    """Verify endpoint returns real schedule data without hardcoded times."""
    monkeypatch.setattr("app.config.settings.FLIGHT_PROVIDER", "aerodatabox")
    provider = AeroDataBoxProvider()
    mock_status = provider._normalize_candidate(
        SAMPLE_AERODATABOX_PAYLOAD[0], requested_flight="AI101", target_date="2026-10-28"
    )
    mock_validate.return_value = mock_status

    payload = {
        "flightNumber": "AI101",
        "departDate": "2026-10-28",
        "originCode": "DEL",
        "destinationCode": "FCO",
    }
    response = client.post("/api/flight/validate", json=payload)
    assert response.status_code == 200
    res_json = response.json()
    assert res_json["success"] is True
    assert res_json["data"]["valid"] is True
    flight_data = res_json["data"]["flightData"]
    assert flight_data["airline"]["name"] == "Air India"
    assert flight_data["flight"]["iata"] == "AI101"
    assert flight_data["departure"]["airport"] == "DEL"
    assert "21:35" in flight_data["departure"]["scheduled"]
    assert flight_data["arrival"]["airport"] == "FCO"
    assert "03:20" in flight_data["arrival"]["scheduled"]
