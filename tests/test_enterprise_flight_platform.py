import sys
import os
import asyncio
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app
from app.integrations.aerodatabox.provider import FlightProvider, AeroDataBoxProvider
from app.integrations.aerodatabox.service import FlightIntelligenceService
from app.integrations.aerodatabox.cache import FlightCacheManager
from app.integrations.aerodatabox.client import AeroDataBoxClient

client = TestClient(app)

# 1. Test Provider Interface Polymorphism
def test_01_provider_interface_polymorphism():
    provider = FlightIntelligenceService.get_provider()
    assert isinstance(provider, FlightProvider)
    assert isinstance(provider, AeroDataBoxProvider)
    print("\nPASSED: 1. FlightProvider Abstract Base Interface & AeroDataBoxProvider Polymorphism")

# 2. Test All 7 Enterprise API Endpoints
def test_02_enterprise_api_endpoints():
    mock_raw = [
        {
            "airline": {"name": "Air India", "iata": "AI"},
            "departure": {"airport": {"iata": "DEL"}, "terminal": "3", "gate": "25"},
            "arrival": {"airport": {"iata": "BOM"}, "terminal": "2", "gate": "12"},
            "status": "Scheduled"
        }
    ]

    with patch.object(AeroDataBoxClient, "fetch_flight_status", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_raw

        # 1. POST /api/flights/validate
        r1 = client.post("/api/flights/validate", json={"flightNumber": "AI101", "date": "2026-07-24"})
        assert r1.status_code == 200
        assert r1.json()["data"]["valid"] is True

        # 2. GET /api/flights/status/{flightNumber}
        r2 = client.get("/api/flights/status/AI101")
        assert r2.status_code == 200
        assert r2.json()["data"]["flightNumber"] == "AI101"

        # 3. GET /api/flights/airline/{iata}
        r3 = client.get("/api/flights/airline/AI")
        assert r3.status_code == 200
        assert r3.json()["data"]["name"] == "Air India"

        # 4. GET /api/flights/airport/{iata}
        r4 = client.get("/api/flights/airport/DEL")
        assert r4.status_code == 200
        assert r4.json()["data"]["iata"] == "DEL"

        # 5. GET /api/flights/aircraft/{registration}
        r5 = client.get("/api/flights/aircraft/VT-ANX")
        assert r5.status_code == 200
        assert r5.json()["data"]["registration"] == "VT-ANX"

        # 6. GET /api/flights/search
        r6 = client.get("/api/flights/search?query=AI101")
        assert r6.status_code == 200
        assert len(r6.json()["data"]) > 0

        # 7. GET /api/flights/live/{flightNumber}
        r7 = client.get("/api/flights/live/AI101")
        assert r7.status_code == 200
        assert "latitude" in r7.json()["data"]

    print("PASSED: 2. All 7 Enterprise Flight Intelligence API Endpoints Verified")

# 3. Test Redis Cache Keys
def test_03_cache_keys():
    k1 = FlightCacheManager.format_flight_key("AI101", "2026-07-24")
    k2 = FlightCacheManager.format_airport_key("DEL")
    k3 = FlightCacheManager.format_airline_key("AI")
    k4 = FlightCacheManager.format_aircraft_key("VT-ANX")

    assert k1 == "flight:AI101:2026-07-24"
    assert k2 == "airport:DEL"
    assert k3 == "airline:AI"
    assert k4 == "aircraft:VT-ANX"
    print("PASSED: 3. Redis Cache Keys Format Verification")

# 4. Test Notification Event Generation
def test_04_notification_events():
    FlightIntelligenceService.emit_notification_event("GATE_CHANGE", "AI101", {"oldGate": "25", "newGate": "28"})
    assert len(FlightIntelligenceService._events_log) > 0
    assert FlightIntelligenceService._events_log[-1]["eventType"] == "GATE_CHANGE"
    print("PASSED: 4. Notification Event Dispatching Verified")

if __name__ == "__main__":
    test_01_provider_interface_polymorphism()
    test_02_enterprise_api_endpoints()
    test_03_cache_keys()
    test_04_notification_events()
    print("\n==================================================")
    print(" ALL ENTERPRISE FLIGHT PLATFORM TESTS PASSED 100%!")
    print("==================================================")
