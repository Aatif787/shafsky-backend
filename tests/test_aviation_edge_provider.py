"""
Unit and Integration Test Suite for Aviation Edge Flight Intelligence Provider.

Audits and verifies provider initialization, normalization, flight number normalization (AI302, AI 302, EK504, 6E211, BA256, QR570),
date validation, delay/cancellation parsing, structured error code handling (INVALID_FLIGHT_NUMBER, FLIGHT_NOT_FOUND,
PROVIDER_TIMEOUT, PROVIDER_UNAVAILABLE, INVALID_DATE, RATE_LIMIT_EXCEEDED), Redis caching, and FastAPI endpoints.
"""

import json
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
import httpx

from app.main import app
from app.flight.providers.aviation_edge_provider import (
    AviationEdgeProvider,
    normalize_flight_number,
    validate_date_string,
    canonical_flight_iata,
    _IN_MEMORY_CACHE,
)
from app.flight.schemas import FlightStatusData, FlightTelemetry, FlightValidateResponse
from app.flight.exceptions import (
    InvalidFlightNumberException,
    InvalidFlightDateException,
    FlightNotFoundException,
    FlightRateLimitExceededException,
    FlightProviderTimeoutException,
    FlightProviderUnavailableException
)

client = TestClient(app)


# Sample Aviation Edge API raw payloads
SAMPLE_AVIATION_EDGE_FLIGHT = {
    "flight": {
        "iataNumber": "AI101",
        "icaoNumber": "AIC101",
        "number": "101"
    },
    "departure": {
        "iataCode": "DEL",
        "icaoCode": "VIDP",
        "terminal": "3",
        "gate": "14",
        "scheduledTime": "2026-08-03T10:00:00.000",
        "estimatedTime": "2026-08-03T10:10:00.000",
        "actualTime": "2026-08-03T10:12:00.000"
    },
    "arrival": {
        "iataCode": "JFK",
        "icaoCode": "KJFK",
        "terminal": "4",
        "gate": "B22",
        "scheduledTime": "2026-08-03T14:30:00.000",
        "estimatedTime": "2026-08-03T14:35:00.000",
        "actualTime": None
    },
    "airline": {
        "name": "Air India",
        "iataCode": "AI",
        "icaoCode": "AIC"
    },
    "status": "active",
    "geography": {
        "latitude": 28.5562,
        "longitude": 77.1000,
        "altitude": 10000.0,
        "direction": 270.0
    },
    "speed": {
        "horizontal": 800.0
    }
}


def test_provider_initialization():
    """Verify provider initializes with settings without exposing secrets."""
    provider = AviationEdgeProvider(api_key="test_secret_key", base_url="https://aviation-edge.com/v2/public")
    assert provider.api_key == "test_secret_key"
    assert provider.base_url == "https://aviation-edge.com/v2/public"


def test_flight_number_normalization():
    """Verify flight numbers are correctly normalized or raise InvalidFlightNumberException."""
    assert normalize_flight_number("AI302") == "AI302"
    assert normalize_flight_number("AI 302") == "AI302"
    assert normalize_flight_number("ai-302") == "AI302"
    assert normalize_flight_number("EK504") == "EK504"
    assert normalize_flight_number("EK 504") == "EK504"
    assert normalize_flight_number("6E211") == "6E211"
    assert normalize_flight_number("6e 211") == "6E211"
    assert normalize_flight_number("BA256") == "BA256"
    assert normalize_flight_number("BA 256") == "BA256"
    assert normalize_flight_number("QR570") == "QR570"

    with pytest.raises(InvalidFlightNumberException) as exc_info:
        normalize_flight_number("INVALID1234567")
    assert exc_info.value.code == "INVALID_FLIGHT_NUMBER"

    with pytest.raises(InvalidFlightNumberException):
        normalize_flight_number("1234")


def test_date_string_validation():
    """Verify date string format validation."""
    assert validate_date_string("2026-08-03") == "2026-08-03"
    assert validate_date_string("2026-08-03T12:00:00") == "2026-08-03"

    with pytest.raises(InvalidFlightDateException) as exc_info:
        validate_date_string("invalid-date")
    assert exc_info.value.code == "INVALID_DATE"


def test_data_normalization():
    """Verify raw Aviation Edge JSON is correctly normalized into FlightStatusData."""
    provider = AviationEdgeProvider(api_key="test_key")
    normalized = provider._normalize_flight_data(SAMPLE_AVIATION_EDGE_FLIGHT)

    assert isinstance(normalized, FlightStatusData)
    assert normalized.airline.iata == "AI"
    assert normalized.airline.name == "Air India"
    assert normalized.airline.logo == "https://images.aviation-edge.com/airline-logos/AI.png"
    assert normalized.flight.iata == "AI101"
    assert normalized.departure.airport == "DEL"
    assert normalized.departure.terminal == "3"
    assert normalized.departure.gate == "14"
    assert normalized.arrival.airport == "JFK"
    assert normalized.arrival.terminal == "4"
    assert normalized.arrival.gate == "B22"
    assert normalized.status == "Active"


def test_delayed_and_cancelled_flight_normalization():
    """Verify delayed and cancelled flight states are correctly classified."""
    provider = AviationEdgeProvider(api_key="test_key")

    # Delayed flight payload (45 min departure delay)
    delayed_payload = dict(SAMPLE_AVIATION_EDGE_FLIGHT)
    delayed_payload["departure"] = {
        "iataCode": "DEL",
        "scheduledTime": "2026-08-03T10:00:00.000",
        "estimatedTime": "2026-08-03T10:45:00.000",
        "delay": "45"
    }
    delayed_payload["status"] = "scheduled"
    normalized_delayed = provider._normalize_flight_data(delayed_payload)
    assert normalized_delayed.departure.delay == 45

    # Cancelled flight payload
    cancelled_payload = dict(SAMPLE_AVIATION_EDGE_FLIGHT)
    cancelled_payload["status"] = "cancelled"
    normalized_cancelled = provider._normalize_flight_data(cancelled_payload)
    assert normalized_cancelled.status == "Cancelled"


@patch("app.flight.providers.aviation_edge_provider.httpx.Client")
def test_rate_limit_exceeded_exception(mock_client_cls):
    """Test HTTP 429 triggers RateLimitExceededException with RATE_LIMIT_EXCEEDED code."""
    mock_response = MagicMock()
    mock_response.status_code = 429

    mock_client = MagicMock()
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_client

    provider = AviationEdgeProvider(api_key="dummy_key")
    with pytest.raises(FlightRateLimitExceededException) as exc_info:
        provider.get_flight_status("AI302")
    assert exc_info.value.code == "RATE_LIMIT_EXCEEDED"


@patch("app.flight.providers.aviation_edge_provider.httpx.Client")
def test_provider_timeout_exception(mock_client_cls):
    """Test request timeout triggers FlightProviderTimeoutException with PROVIDER_TIMEOUT code."""
    mock_client = MagicMock()
    mock_client.get.side_effect = httpx.TimeoutException("Timeout")
    mock_client_cls.return_value.__enter__.return_value = mock_client

    provider = AviationEdgeProvider(api_key="dummy_key")
    with pytest.raises(FlightProviderTimeoutException) as exc_info:
        provider.get_flight_status("AI302")
    assert exc_info.value.code == "PROVIDER_TIMEOUT"


@patch("app.flight.providers.aviation_edge_provider.httpx.Client")
def test_request_with_retry_success(mock_client_cls):
    """Test successful request after transient retry."""
    mock_response_fail = MagicMock()
    mock_response_fail.status_code = 503

    mock_response_success = MagicMock()
    mock_response_success.status_code = 200
    mock_response_success.json.return_value = [SAMPLE_AVIATION_EDGE_FLIGHT]

    mock_client = MagicMock()
    mock_client.get.side_effect = [mock_response_fail, mock_response_success]
    mock_client_cls.return_value.__enter__.return_value = mock_client

    provider = AviationEdgeProvider(api_key="dummy_key")
    result = provider.get_flight_status("AI 101")

    assert result.flight_num == "AI101"
    assert mock_client.get.call_count == 2


@patch("app.flight.providers.aviation_edge_provider.httpx.Client")
def test_request_failure_downtime_resilience(mock_client_cls):
    """Test provider handles server errors and downtime gracefully by raising FlightProviderUnavailableException."""
    mock_response = MagicMock()
    mock_response.status_code = 500

    mock_client = MagicMock()
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_client

    provider = AviationEdgeProvider(api_key="dummy_key")
    _IN_MEMORY_CACHE.clear()
    with pytest.raises(FlightProviderUnavailableException) as exc_info:
        provider.get_flight_status("AI101")
    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


@patch("app.flight.providers.aviation_edge_provider.AviationEdgeProvider._get_redis")
@patch("app.flight.providers.aviation_edge_provider.httpx.Client")
def test_redis_caching_hit(mock_client_cls, mock_get_redis):
    """Test Redis cache hit bypasses external API request."""
    provider = AviationEdgeProvider(api_key="dummy_key")
    normalized_dict = provider._normalize_flight_data(SAMPLE_AVIATION_EDGE_FLIGHT).model_dump(mode="json")

    mock_redis = MagicMock()
    mock_redis.get.return_value = json.dumps(normalized_dict)
    mock_get_redis.return_value = mock_redis

    result = provider.get_flight_status("EK 504")
    assert result.flight_num == "AI101"
    mock_client_cls.assert_not_called()


@patch("app.flight.providers.aviation_edge_provider.AviationEdgeProvider.get_flight_status")
def test_endpoint_get_flight_by_number(mock_get_status):
    """Test GET /api/flights/{flightNumber} endpoint with space normalization."""
    provider = AviationEdgeProvider()
    mock_status = provider._normalize_flight_data(SAMPLE_AVIATION_EDGE_FLIGHT)
    mock_get_status.return_value = mock_status

    response = client.get("/api/flights/AI%20302")
    assert response.status_code == 200
    data = response.json()
    assert data["flight_num"] == "AI101"


@patch("app.flight.providers.aviation_edge_provider.AviationEdgeProvider.validate_flight")
def test_endpoint_validate_flight_success(mock_validate):
    """Test POST /api/flights/validate endpoint with valid payload."""
    provider = AviationEdgeProvider()
    mock_status = provider._normalize_flight_data(SAMPLE_AVIATION_EDGE_FLIGHT)
    mock_validate.return_value = mock_status

    payload = {
        "flightNumber": "6E 211",
        "date": "2026-08-03"
    }
    response = client.post("/api/flights/validate", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["success"] is True
    assert res_data["data"]["valid"] is True


def test_endpoint_validate_flight_invalid_flight_number():
    """Test POST /api/flights/validate returning INVALID_FLIGHT_NUMBER error code."""
    payload = {
        "flightNumber": "INVALID12345",
        "date": "2026-08-03"
    }
    response = client.post("/api/flights/validate", json=payload)
    assert response.status_code == 400
    res_data = response.json()
    assert res_data["success"] is False
    assert res_data["code"] == "INVALID_FLIGHT_NUMBER"
    assert "invalid" in res_data["error"].lower()


def test_endpoint_validate_flight_invalid_date():
    """Test POST /api/flights/validate returning INVALID_DATE error code."""
    payload = {
        "flightNumber": "AI302",
        "date": "invalid-date-format"
    }
    response = client.post("/api/flights/validate", json=payload)
    assert response.status_code == 400
    res_data = response.json()
    assert res_data["success"] is False
    assert res_data["code"] == "INVALID_DATE"


def test_canonical_flight_iata_strips_padding_and_spaces():
    assert canonical_flight_iata("AI2615") == "AI2615"
    assert canonical_flight_iata("ai 02615") == "AI2615"
    assert canonical_flight_iata("AI1745") == canonical_flight_iata("ai1745")


def test_validate_uses_airport_scoped_timetable_not_hardcoded_hubs():
    provider = AviationEdgeProvider(api_key="test_key")
    provider._get_cached_data = MagicMock(return_value=None)
    provider._set_cached_data = MagicMock()
    seen = []

    payload = [{
        "flight": {"iataNumber": "AI1744", "number": "1744"},
        "airline": {"iataCode": "AI", "name": "Air India"},
        "departure": {"iataCode": "DEL", "scheduledTime": "2026-08-21T10:00:00.000"},
        "arrival": {"iataCode": "BBI", "scheduledTime": "2026-08-21T12:00:00.000"},
        "status": "scheduled",
    }]

    def capture(endpoint, params):
        seen.append((endpoint, dict(params)))
        if endpoint == "timetable" and params.get("flight_iata") == "AI1744" and params.get("iataCode") == "DEL":
            return payload
        return []

    with patch.object(provider, "_make_request", side_effect=capture):
        result = provider.validate_flight(
            "AI1744",
            "2026-08-21",
            direction="departure",
            origin_code="DEL",
            destination_code="BBI",
        )

    assert result.flight.iata == "AI1744"
    timetable_airports = {p.get("iataCode") for ep, p in seen if ep == "timetable"}
    assert "DEL" in timetable_airports
    assert "BBI" in timetable_airports
    assert not any(ep == "timetable" and p.get("iataCode") == "BLR" for ep, p in seen)
    assert any(
        ep == "timetable" and p.get("iataCode") == "DEL" and p.get("flight_iata") == "AI1744"
        for ep, p in seen
    )


def test_validate_rejects_unrelated_live_airline_dump():
    provider = AviationEdgeProvider(api_key="test_key")
    provider._get_cached_data = MagicMock(return_value=None)
    provider._set_cached_data = MagicMock()
    dump = [{
        "flight": {"iataNumber": "AI191"},
        "airline": {"iataCode": "AI"},
        "departure": {"iataCode": "FCO"},
        "arrival": {"iataCode": "EWR"},
    }]

    with patch.object(provider, "_make_request", return_value=dump):
        with pytest.raises(FlightNotFoundException):
            provider.validate_flight("AI2615", "2026-08-21", origin_code="DEL", destination_code="BOM")


def test_codeshare_token_matches_requested_flight():
    provider = AviationEdgeProvider(api_key="test_key")
    candidate = {
        "airline": {"iataCode": "6E"},
        "flight": {"iataNumber": "6E123"},
        "codeshared": {"flight": {"iataNumber": "AI1745"}},
        "departure": {"iataCode": "DEL"},
        "arrival": {"iataCode": "BOM"},
    }
    assert provider._validate_candidate_airline(candidate, "AI", "AIC", expected_flight_iata="AI1745")


def test_same_flight_number_keeps_requested_sector_not_continuation():
    """SG476 operates DEL-BOM then BOM-BLR; arrival at BOM must not return Bangalore."""
    provider = AviationEdgeProvider(api_key="test_key")
    provider._get_cached_data = MagicMock(return_value=None)
    provider._set_cached_data = MagicMock()

    del_bom = {
        "flight": {"iataNumber": "SG476", "number": "476"},
        "airline": {"iataCode": "SG", "name": "SpiceJet"},
        "departure": {"iataCode": "DEL", "scheduledTime": "2026-08-20T18:35:00.000"},
        "arrival": {"iataCode": "BOM", "scheduledTime": "2026-08-20T20:45:00.000"},
        "status": "scheduled",
    }
    bom_blr = {
        "flight": {"iataNumber": "SG476", "number": "476"},
        "airline": {"iataCode": "SG", "name": "SpiceJet"},
        "departure": {"iataCode": "BOM", "scheduledTime": "2026-08-20T21:40:00.000"},
        "arrival": {"iataCode": "BLR", "scheduledTime": "2026-08-20T23:40:00.000"},
        "status": "scheduled",
    }

    def capture(endpoint, params):
        if endpoint == "timetable" and params.get("iataCode") == "DEL" and params.get("type") == "departure":
            return [del_bom]
        if endpoint == "timetable" and params.get("iataCode") == "BOM" and params.get("type") == "arrival":
            return [del_bom]
        if endpoint == "timetable" and params.get("iataCode") == "BOM" and params.get("type") == "departure":
            return [bom_blr]
        return []

    with patch.object(provider, "_make_request", side_effect=capture):
        arrival_mumbai = provider.validate_flight(
            "SG476",
            "2026-08-20",
            direction="arrival",
            origin_code="DEL",
            destination_code="BOM",
            airport_code="BOM",
        )
        departure_mumbai = provider.validate_flight(
            "SG476",
            "2026-08-20",
            direction="departure",
            origin_code="BOM",
            destination_code="BLR",
            airport_code="BOM",
        )

    assert arrival_mumbai.departure.airport == "DEL"
    assert arrival_mumbai.arrival.airport == "BOM"
    assert departure_mumbai.departure.airport == "BOM"
    assert departure_mumbai.arrival.airport == "BLR"

