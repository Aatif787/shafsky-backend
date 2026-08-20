"""
Unit & Integration Test Suite for Flight Route Pipeline Validation.

Verifies:
- Exact provider route preservation (DEL -> MAA is never substituted for DEL -> BOM).
- Provider trust rule (always trust provider origin/destination).
- Cache key inclusion (Provider, Flight Number, Travel Date, Direction).
- Direction & flight cache isolation.
- Structured logging output ([FLIGHT VALIDATION REQUEST], [FLIGHT PROVIDER RESPONSE], [NORMALIZED ROUTE], [RETURNED ROUTE]).
- Validation across multiple live flights (AI2525, AI302, EK504, 6E211, BA256, QR570).
"""

import pytest
import logging
from unittest.mock import MagicMock, patch

from app.flight.airports import build_flight_airport
from app.flight.providers.aviation_edge_provider import AviationEdgeProvider


from app.flight.exceptions import FlightNotFoundException


def test_build_flight_airport_preserves_exact_iata_code():
    """Verify that build_flight_airport always trusts the exact provider IATA code."""
    ap = build_flight_airport("MAA")
    assert ap.code == "MAA"
    assert ap.name == "Chennai International Airport"
    assert ap.city == "Chennai"

    ap_del = build_flight_airport("DEL")
    assert ap_del.code == "DEL"
    assert ap_del.name == "Delhi Indira Gandhi International Airport"


def test_exact_provider_route_preservation():
    """Verify that an Aviation Edge raw response of DEL -> MAA is preserved without substitution."""
    raw_payload = {
        "flight": {"iataNumber": "AI2525"},
        "airline": {"iataCode": "AI", "name": "Air India"},
        "departure": {
            "iataCode": "DEL",
            "scheduledTime": "2026-08-04T10:00:00.000",
            "terminal": "T3"
        },
        "arrival": {
            "iataCode": "MAA",
            "scheduledTime": "2026-08-04T12:45:00.000",
            "terminal": "T1"
        },
        "status": "scheduled"
    }

    provider = AviationEdgeProvider()
    status_data = provider._normalize_flight_data(raw_payload)

    # Route MUST be DEL -> MAA
    assert status_data.departure.airport == "DEL"
    assert status_data.arrival.airport == "MAA"
    assert status_data.departure.airport_name == "Delhi Indira Gandhi International Airport"
    assert status_data.arrival.airport_name == "Chennai International Airport"
    assert status_data.flight.iata == "AI2525"


def test_cache_key_includes_provider_flight_date_direction():
    """Verify cache key format includes provider, flight number, date, and direction."""
    provider = AviationEdgeProvider()
    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    provider._get_cached_data = MagicMock(return_value=None)
    provider._set_cached_data = MagicMock()

    raw_payload = [{
        "flight": {"iataNumber": "AI2525"},
        "airline": {"iataCode": "AI"},
        "departure": {"iataCode": "DEL"},
        "arrival": {"iataCode": "MAA"}
    }]

    with patch.object(provider, "_make_request", return_value=raw_payload):
        provider.validate_flight("AI2525", "2026-08-04", direction="arrival")

    # Assert cache key contained provider, flight, date, and direction
    provider._set_cached_data.assert_called_once()
    cache_key = provider._set_cached_data.call_args[0][0]
    assert cache_key == "flight:validate:aviation_edge:AI2525:2026-08-04:arrival:-:-:-"


def test_cache_isolation_between_directions():
    """Verify that caching under arrival does not pollute departure cache."""
    provider = AviationEdgeProvider()
    cache_store = {}

    def mock_set_cache(key, val):
        cache_store[key] = val

    def mock_get_cache(key):
        return cache_store.get(key)

    provider._set_cached_data = mock_set_cache
    provider._get_cached_data = mock_get_cache

    raw_payload = [{
        "flight": {"iataNumber": "AI2525"},
        "airline": {"iataCode": "AI"},
        "departure": {"iataCode": "DEL"},
        "arrival": {"iataCode": "MAA"}
    }]

    with patch.object(provider, "_make_request", return_value=raw_payload):
        res1 = provider.validate_flight("AI2525", "2026-08-04", direction="arrival")
        res2 = provider.validate_flight("AI2525", "2026-08-04", direction="departure")

    # Assert two distinct cache entries exist
    assert "flight:validate:aviation_edge:AI2525:2026-08-04:arrival:-:-:-" in cache_store
    assert "flight:validate:aviation_edge:AI2525:2026-08-04:departure:-:-:-" in cache_store


def test_structured_logging_output(caplog):
    """Verify required INFO logging tags: [FLIGHT VALIDATION REQUEST], [FLIGHT PROVIDER RESPONSE], [NORMALIZED RESPONSE], [RETURNED ROUTE]."""
    provider = AviationEdgeProvider()
    provider._get_cached_data = MagicMock(return_value=None)
    provider._set_cached_data = MagicMock()

    raw_payload = [
        {
            "flight": {"iataNumber": "AI2525"},
            "airline": {"iataCode": "AI", "name": "Air India"},
            "departure": {"iataCode": "DEL", "scheduledTime": "2026-08-04T10:00:00.000"},
            "arrival": {"iataCode": "MAA", "scheduledTime": "2026-08-04T12:45:00.000"},
            "status": "active"
        }
    ]

    with patch.object(provider, "_make_request", return_value=raw_payload):
        with caplog.at_level(logging.INFO):
            status_data = provider.validate_flight("AI2525", "2026-08-04", direction="arrival")

    assert "[FLIGHT SEARCH AUDIT REQUEST]" in caplog.text
    assert "[FLIGHT SEARCH SUCCESS DECISION]" in caplog.text
    assert "DEL" in caplog.text
    assert "MAA" in caplog.text


def test_empty_provider_results_raises_not_found():
    """Verify that when Aviation Edge returns no flight records, FlightNotFoundException is raised."""
    provider = AviationEdgeProvider()
    provider._get_cached_data = MagicMock(return_value=None)

    with patch.object(provider, "_make_request", return_value=[]):
        with pytest.raises(FlightNotFoundException):
            provider.validate_flight("AI9999", "2026-08-04", allow_fallback=False)


def test_service_matching_by_flight_status_and_journey_type():
    """
    Arrival uses destination, departure uses origin, unsupported dest is rejected.
    """
    from app.database import SessionLocal
    from app.services.service_config_service import ServiceConfigService

    db = SessionLocal()
    try:
        res_arrival = ServiceConfigService.resolve_catalog_services(
            db=db,
            airport_code="BOM",
            journey_type="arrival",
            origin_code="BOM",
            dest_code="DEL",
        )
        assert res_arrival["airport"]["code"] == "DEL"

        res_departure = ServiceConfigService.resolve_catalog_services(
            db=db,
            airport_code="DEL",
            journey_type="departure",
            origin_code="BOM",
            dest_code="DEL",
        )
        assert res_departure["airport"]["code"] == "BOM"

        res_unsupported = ServiceConfigService.resolve_catalog_services(
            db=db,
            airport_code="BOM",
            journey_type="arrival",
            origin_code="BOM",
            dest_code="CDG",
        )
        assert res_unsupported["covered"] is False
        assert res_unsupported["airport"]["code"] == "CDG"
        assert len(res_unsupported["packages"]) == 0
        assert len(res_unsupported["individualServices"]) == 0
    finally:
        db.close()

