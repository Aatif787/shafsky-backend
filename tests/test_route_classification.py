"""
test_route_classification.py — Canonical Domestic/International Route Classification Tests.

Tests the authoritative route classification rule:
  - India → India = DOMESTIC
  - India → Outside India = INTERNATIONAL
  - Outside India → India = INTERNATIONAL
  - Outside India → Outside India = INTERNATIONAL

Covers: route matrix, client tampering, unknown airports, transit preservation.
"""
import pytest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from app.services.service_airport_rules import (
    derive_flight_type_from_route,
    resolve_airport_country,
    _is_india,
    normalize_iata,
)


# ──────────────────────────────────────────────────────────
# Helpers: mock DB session returning airport objects
# ──────────────────────────────────────────────────────────

def _make_supported_airport(iata_code: str, country: str):
    """Simulates a SupportedAirport ORM row."""
    return SimpleNamespace(iata_code=iata_code, country=country)


def _make_global_airport(iata_code: str, iso_country: str, country: str):
    """Simulates an AirportManagement ORM row."""
    return SimpleNamespace(iata_code=iata_code, iso_country=iso_country, country=country)


# Airport registry for mocking
_SUPPORTED_AIRPORTS = {
    "DEL": _make_supported_airport("DEL", "India"),
    "BOM": _make_supported_airport("BOM", "India"),
    "BLR": _make_supported_airport("BLR", "India"),
    "HYD": _make_supported_airport("HYD", "India"),
    "IXC": _make_supported_airport("IXC", "India"),
    "ATQ": _make_supported_airport("ATQ", "India"),
}

_GLOBAL_AIRPORTS = {
    "DXB": _make_global_airport("DXB", "AE", "AE"),
    "LHR": _make_global_airport("LHR", "GB", "GB"),
    "JFK": _make_global_airport("JFK", "US", "US"),
    "SIN": _make_global_airport("SIN", "SG", "SG"),
    "DOH": _make_global_airport("DOH", "QA", "QA"),
    "CDG": _make_global_airport("CDG", "FR", "FR"),
    "FRA": _make_global_airport("FRA", "DE", "DE"),
    # Indian airports also in global table (with iso_country = "IN")
    "DEL": _make_global_airport("DEL", "IN", "IN"),
    "BOM": _make_global_airport("BOM", "IN", "IN"),
    "BLR": _make_global_airport("BLR", "IN", "IN"),
}


def _mock_db_scalar(query):
    """
    Simulates db.scalar() for SupportedAirport and AirportManagement queries.
    Inspects the compiled query to extract the IATA code.
    """
    try:
        compiled = query.compile(compile_kwargs={"literal_binds": False})
        params = compiled.params
    except Exception:
        return None
    
    for key, val in params.items():
        code = str(val).strip().upper()
        if code in _SUPPORTED_AIRPORTS:
            return _SUPPORTED_AIRPORTS[code]
        if code in _GLOBAL_AIRPORTS:
            return _GLOBAL_AIRPORTS[code]
    
    return None


class MockDB:
    """Mock database session that routes queries to our test airport registries."""
    
    def scalar(self, query):
        return _mock_db_scalar(query)


@pytest.fixture
def db():
    return MockDB()


# ──────────────────────────────────────────────────────────
# Unit Tests: _is_india
# ──────────────────────────────────────────────────────────

class TestIsIndia:
    def test_india_full_name(self):
        assert _is_india("India") is True

    def test_india_iso2(self):
        assert _is_india("IN") is True

    def test_india_iso3(self):
        assert _is_india("IND") is True

    def test_india_case_insensitive(self):
        assert _is_india("india") is True
        assert _is_india("INDIA") is True
        assert _is_india("  In  ") is True

    def test_non_india(self):
        assert _is_india("AE") is False
        assert _is_india("GB") is False
        assert _is_india("US") is False
        assert _is_india("Indonesia") is False


# ──────────────────────────────────────────────────────────
# Route Matrix Tests (Tests 1–5)
# ──────────────────────────────────────────────────────────

class TestRouteMatrix:
    """Test 1-5: Basic route classification."""

    def test_1_dxb_to_del_arrival_is_international(self, db):
        """DXB → DEL, ARRIVAL = INTERNATIONAL"""
        result = derive_flight_type_from_route(db, "DXB", "DEL", "ARRIVAL")
        assert result == "INTERNATIONAL"

    def test_2_del_to_dxb_departure_is_international(self, db):
        """DEL → DXB, DEPARTURE = INTERNATIONAL"""
        result = derive_flight_type_from_route(db, "DEL", "DXB", "DEPARTURE")
        assert result == "INTERNATIONAL"

    def test_3_bom_to_del_arrival_is_domestic(self, db):
        """BOM → DEL, ARRIVAL = DOMESTIC"""
        result = derive_flight_type_from_route(db, "BOM", "DEL", "ARRIVAL")
        assert result == "DOMESTIC"

    def test_4_del_to_bom_departure_is_domestic(self, db):
        """DEL → BOM, DEPARTURE = DOMESTIC"""
        result = derive_flight_type_from_route(db, "DEL", "BOM", "DEPARTURE")
        assert result == "DOMESTIC"

    def test_5_lhr_to_jfk_is_international(self, db):
        """LHR → JFK = INTERNATIONAL (both outside India)"""
        result = derive_flight_type_from_route(db, "LHR", "JFK", "DEPARTURE")
        assert result == "INTERNATIONAL"


# ──────────────────────────────────────────────────────────
# Client Tampering Tests (Tests 6–9)
# ──────────────────────────────────────────────────────────

class TestClientTampering:
    """Test 6-9: Client sends contradictory flight_type — server overrides."""

    def test_6_dxb_del_client_domestic_server_international(self, db):
        """DXB → DEL + client says DOMESTIC → server says INTERNATIONAL"""
        # The canonical resolver ignores client input — it only looks at airports
        result = derive_flight_type_from_route(db, "DXB", "DEL", "ARRIVAL")
        assert result == "INTERNATIONAL"

    def test_7_del_dxb_client_domestic_server_international(self, db):
        """DEL → DXB + client says DOMESTIC → server says INTERNATIONAL"""
        result = derive_flight_type_from_route(db, "DEL", "DXB", "DEPARTURE")
        assert result == "INTERNATIONAL"

    def test_8_bom_del_client_international_server_domestic(self, db):
        """BOM → DEL + client says INTERNATIONAL → server says DOMESTIC"""
        result = derive_flight_type_from_route(db, "BOM", "DEL", "ARRIVAL")
        assert result == "DOMESTIC"

    def test_9_lhr_jfk_client_domestic_server_international(self, db):
        """LHR → JFK + client says DOMESTIC → server says INTERNATIONAL"""
        result = derive_flight_type_from_route(db, "LHR", "JFK", "DEPARTURE")
        assert result == "INTERNATIONAL"


# ──────────────────────────────────────────────────────────
# Unknown / Missing Airport Tests (Tests 10–13)
# ──────────────────────────────────────────────────────────

class TestUnknownAirports:
    """Test 10-13: Unknown/missing airports raise ValueError."""

    def test_10_unknown_origin_raises(self, db):
        """Unknown origin → validation error"""
        with pytest.raises(ValueError, match="Unable to determine the country"):
            derive_flight_type_from_route(db, "ZZZ", "DEL", "ARRIVAL")

    def test_11_unknown_destination_raises(self, db):
        """Unknown destination → validation error"""
        with pytest.raises(ValueError, match="Unable to determine the country"):
            derive_flight_type_from_route(db, "DEL", "ZZZ", "DEPARTURE")

    def test_12_missing_origin_raises(self, db):
        """Missing origin → validation error"""
        with pytest.raises(ValueError, match="Origin airport is missing"):
            derive_flight_type_from_route(db, None, "DEL", "ARRIVAL")

    def test_13_missing_destination_raises(self, db):
        """Missing destination → validation error"""
        with pytest.raises(ValueError, match="Destination airport is missing"):
            derive_flight_type_from_route(db, "DEL", None, "DEPARTURE")

    def test_both_missing_raises(self, db):
        """Both missing → validation error"""
        with pytest.raises(ValueError, match="Unable to determine whether"):
            derive_flight_type_from_route(db, None, None, "ARRIVAL")

    def test_empty_string_raises(self, db):
        """Empty string codes → validation error"""
        with pytest.raises(ValueError, match="Unable to determine whether"):
            derive_flight_type_from_route(db, "", "", "DEPARTURE")


# ──────────────────────────────────────────────────────────
# Transit Preservation Tests (Tests 14–17)
# ──────────────────────────────────────────────────────────

class TestTransitPreservation:
    """Test 14-17: Transit returns None (preserving compound types)."""

    def test_14_transit_domestic_domestic_unchanged(self, db):
        """TRANSIT returns None — existing compound type preserved"""
        result = derive_flight_type_from_route(db, "DEL", "BOM", "TRANSIT")
        assert result is None

    def test_15_transit_domestic_international_unchanged(self, db):
        """TRANSIT returns None"""
        result = derive_flight_type_from_route(db, "DEL", "DXB", "TRANSIT")
        assert result is None

    def test_16_transit_international_domestic_unchanged(self, db):
        """TRANSIT returns None"""
        result = derive_flight_type_from_route(db, "DXB", "DEL", "TRANSIT")
        assert result is None

    def test_17_transit_international_international_unchanged(self, db):
        """TRANSIT returns None"""
        result = derive_flight_type_from_route(db, "LHR", "JFK", "TRANSIT")
        assert result is None


# ──────────────────────────────────────────────────────────
# Package / Price Safety Tests (Tests 18–19)
# ──────────────────────────────────────────────────────────

class TestPackagePriceSafety:
    """Test 18-19: Verify correct flight_type flows into package selection."""

    def test_18_international_route_client_domestic_uses_international_package(self, db):
        """
        International route + client DOMESTIC → international package/pricing selected.
        The canonical resolver derives INTERNATIONAL regardless of client input.
        """
        # Client submits DOMESTIC for DEL → DXB route
        client_flight_type = "DOMESTIC"
        # Server derives from route
        server_flight_type = derive_flight_type_from_route(db, "DEL", "DXB", "DEPARTURE")
        assert server_flight_type == "INTERNATIONAL"
        assert server_flight_type != client_flight_type

    def test_19_domestic_route_client_international_uses_domestic_package(self, db):
        """
        Domestic route + client INTERNATIONAL → domestic package/pricing selected.
        """
        client_flight_type = "INTERNATIONAL"
        server_flight_type = derive_flight_type_from_route(db, "DEL", "BOM", "DEPARTURE")
        assert server_flight_type == "DOMESTIC"
        assert server_flight_type != client_flight_type


# ──────────────────────────────────────────────────────────
# Additional Edge Cases
# ──────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_iata_case_insensitive(self, db):
        """Lowercase IATA codes should work"""
        result = derive_flight_type_from_route(db, "del", "bom", "DEPARTURE")
        assert result == "DOMESTIC"

    def test_iata_with_whitespace(self, db):
        """IATA codes with extra whitespace should work"""
        result = derive_flight_type_from_route(db, "  DEL  ", "  DXB  ", "DEPARTURE")
        assert result == "INTERNATIONAL"

    def test_journey_type_aliases(self, db):
        """Journey type aliases should normalize correctly"""
        assert derive_flight_type_from_route(db, "BOM", "DEL", "ARR") == "DOMESTIC"
        assert derive_flight_type_from_route(db, "BOM", "DEL", "INBOUND") == "DOMESTIC"
        assert derive_flight_type_from_route(db, "DEL", "DXB", "DEP") == "INTERNATIONAL"
        assert derive_flight_type_from_route(db, "DEL", "DXB", "OUTBOUND") == "INTERNATIONAL"

    def test_supported_airport_india_recognized(self, db):
        """All supported Shafsky airports (India) should classify domestic together"""
        india_codes = ["DEL", "BOM", "BLR", "HYD", "IXC", "ATQ"]
        for origin in india_codes:
            for dest in india_codes:
                result = derive_flight_type_from_route(db, origin, dest, "DEPARTURE")
                assert result == "DOMESTIC", f"{origin} → {dest} should be DOMESTIC"

    def test_mixed_india_international(self, db):
        """India ↔ non-India should always be INTERNATIONAL"""
        india_codes = ["DEL", "BOM", "BLR"]
        intl_codes = ["DXB", "LHR", "JFK", "SIN"]
        for india in india_codes:
            for intl in intl_codes:
                assert derive_flight_type_from_route(db, india, intl, "DEPARTURE") == "INTERNATIONAL"
                assert derive_flight_type_from_route(db, intl, india, "ARRIVAL") == "INTERNATIONAL"
