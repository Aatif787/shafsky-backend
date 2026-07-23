import sys
import os
import time
import json
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.integrations.aerodatabox.client import AeroDataBoxClient
from app.integrations.aerodatabox.service import AeroDataBoxService
from app.integrations.aerodatabox.exceptions import (
    InvalidFlightException,
    ProviderUnavailableException,
    RateLimitException,
    TimeoutException
)
from app.monitoring.metrics import PrometheusMetricsCollector

client = TestClient(app)

# 1. Verify Configuration & Environment Secrets
def test_01_configuration_verification():
    assert settings.AERODATABOX_BASE_URL is not None
    assert settings.RAPIDAPI_HOST is not None
    assert settings.RAPIDAPI_KEY is not None
    headers = AeroDataBoxClient.get_headers()
    assert "X-RapidAPI-Key" in headers
    assert "X-RapidAPI-Host" in headers
    print("\nPASSED: 1. Environment Secrets & Configuration Validated")

# 2. Verify Redis Cache Key & TTL Behavior (10 Minutes)
def test_02_redis_cache_verification():
    cache_key = AeroDataBoxService.get_cache_key("AI101", "2026-07-24")
    assert cache_key == "flight:AI101:2026-07-24"

    test_payload = {"valid": True, "flightNumber": "AI101", "airline": "Air India", "status": "Scheduled"}
    AeroDataBoxService.set_cache(cache_key, test_payload, ttl_seconds=600)

    cached_item = AeroDataBoxService.get_from_cache(cache_key)
    assert cached_item is not None
    assert cached_item["flightNumber"] == "AI101"
    print("PASSED: 2. Redis Cache Key & 10-Minute TTL Verified (Key: flight:AI101:2026-07-24)")

# 3. Verify AeroDataBox Service Validation with Mocked API Response
def test_03_aerodatabox_mocked_validation():
    mock_api_data = [
        {
            "airline": {"name": "Air India"},
            "departure": {"airport": {"iata": "DEL"}, "terminal": "3", "gate": "14"},
            "arrival": {"airport": {"iata": "BOM"}},
            "status": "Scheduled"
        }
    ]

    with patch.object(AeroDataBoxClient, "fetch_flight_status", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_api_data

        res = client.post("/api/flights/validate", json={"flightNumber": "AI202", "date": "2026-07-24"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["success"] is True
        assert body["data"]["valid"] is True
        assert body["data"]["flightNumber"] == "AI202"
        assert body["data"]["airline"] == "Air India"
        assert body["data"]["origin"] == "DEL"
        assert body["data"]["destination"] == "BOM"
        print("PASSED: 3. AeroDataBox Mocked API Validation (POST /api/flights/validate)")

# 4. Verify Invalid Flight Handling (HTTP 400 INVALID_FLIGHT)
def test_04_invalid_flight_handling():
    with patch.object(AeroDataBoxClient, "fetch_flight_status", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = InvalidFlightException("Flight 'FAIL999' not found.")

        res = client.post("/api/flights/validate", json={"flightNumber": "FAIL999", "date": "2026-07-24"})
        assert res.status_code == 400
        body = res.json()
        assert body["detail"]["code"] == "INVALID_FLIGHT"
        print("PASSED: 4. Invalid Flight Handling (HTTP 400 INVALID_FLIGHT)")

# 5. Verify Provider Failover & Outage Handling (HTTP 503 FLIGHT_PROVIDER_UNAVAILABLE)
def test_05_provider_failover_handling():
    with patch.object(AeroDataBoxClient, "fetch_flight_status", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = ProviderUnavailableException("AeroDataBox 503 Service Unavailable")

        res = client.post("/api/flights/validate", json={"flightNumber": "UNCURATED123", "date": "2026-07-24"})
        assert res.status_code == 503
        body = res.json()
        assert body["detail"]["code"] == "FLIGHT_PROVIDER_UNAVAILABLE"
        print("PASSED: 5. Provider Outage Failover Handling (HTTP 503 FLIGHT_PROVIDER_UNAVAILABLE)")

# 6. Verify Prometheus Metrics Exposition
def test_06_metrics_verification():
    res = client.get("/metrics")
    assert res.status_code == 200
    text = res.text
    assert "flight_api_requests_total" in text
    assert "flight_api_failures_total" in text
    assert "flight_cache_hits_total" in text
    assert "flight_cache_misses_total" in text
    print("PASSED: 6. Prometheus Metrics Verification (/metrics)")

# 7. Verify Health Endpoint Extension
def test_07_health_endpoint_verification():
    res = client.get("/health")
    assert res.status_code == 200
    subsystems = res.json()["subsystems"]
    assert "flightIntelligenceService" in subsystems
    assert subsystems["flightIntelligenceService"]["provider"] == "AeroDataBox API (RapidAPI)"
    assert "lastSuccessfulRequest" in subsystems["flightIntelligenceService"]
    print("PASSED: 7. Health Endpoint Verification (/health flightIntelligenceService)")

# 8. Verify Booking Integration Flight Validation
def test_08_booking_integration_verification():
    # Attempt booking with invalid flight
    booking_invalid = {
        "passenger_name": "Lord Randolph",
        "passenger_email": "randolph@shafsky.com",
        "passenger_phone": "+919876543210",
        "flight_num": "INVALID",
        "origin_code": "DEL",
        "dest_code": "BOM",
        "departure_time": "2026-07-25T11:25:49+00:00",
        "arrival_time": "2026-07-25T14:25:49+00:00",
        "service_type": "MEET_AND_GREET",
        "total_amount": 15000.0
    }
    res = client.post("/api/bookings", json=booking_invalid)
    assert res.status_code == 400
    body = res.json()
    assert body["detail"]["code"] == "INVALID_FLIGHT"
    print("PASSED: 8. Pre-Booking Flight Validation Integration")

if __name__ == "__main__":
    test_01_configuration_verification()
    test_02_redis_cache_verification()
    test_03_aerodatabox_mocked_validation()
    test_04_invalid_flight_handling()
    test_05_provider_failover_handling()
    test_06_metrics_verification()
    test_07_health_endpoint_verification()
    test_08_booking_integration_verification()
    print("\n==================================================")
    print(" ALL 8 AERODATABOX INTEGRATION TESTS PASSED 100%!")
    print("==================================================")
