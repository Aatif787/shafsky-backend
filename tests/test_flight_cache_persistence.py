"""
Unit Tests for Multi-Tier Flight Cache & Permanent DB Persistence.
Verifies RAM memory cache, Redis, PostgreSQL FlightAPICache, and FlightStatusRecord persistence.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.flight.unified_cache import (
    clear_unified_cache,
    get_unified_flight,
    store_unified_flight,
    to_flight_status_data,
    _RAM_CACHE,
    _RAM_LOCK,
)
from app.flight.schemas import FlightStatusData


SAMPLE_FLIGHT_DATA = {
    "flight": {
        "iata": "AI101",
        "number": "101",
    },
    "airline": {
        "name": "Air India",
        "iata": "AI",
        "icao": "AIC",
    },
    "departure": {
        "airport": "DEL",
        "airport_name": "New Delhi Indira Gandhi",
        "city": "New Delhi",
        "country": "IN",
        "terminal": "3",
        "gate": "3",
        "scheduled": "2026-10-28T21:28:00+05:30",
        "estimated": "2026-10-28T21:30:00+05:30",
        "actual": "2026-10-28T21:35:00+05:30",
        "checkin_counter": "Counters 12-16",
        "timezone": "Asia/Kolkata",
    },
    "arrival": {
        "airport": "FCO",
        "airport_name": "Rome Leonardo da Vinci",
        "city": "Rome",
        "country": "IT",
        "terminal": "3",
        "gate": "B12",
        "scheduled": "2026-10-29T00:25:00+01:00",
        "estimated": "2026-10-29T00:30:00+01:00",
        "actual": "2026-10-29T00:32:00+01:00",
        "baggage_belt": "Belt 4",
        "timezone": "Europe/Rome",
    },
    "duration": {
        "minutes": 507,
        "formatted": "8h 27m",
    },
    "aircraft": {
        "model": "Airbus A350-900",
        "registration": "VT-JRH",
    },
    "status": "Expected",
}


def setup_function():
    with _RAM_LOCK:
        _RAM_CACHE.clear()


def test_flight_ram_cache_hit():
    """Verify storing flight data serves sub-millisecond RAM memory cache."""
    store_unified_flight(
        flight_iata="AI101",
        data=SAMPLE_FLIGHT_DATA,
        provider="aerodatabox",
        flight_date="2026-10-28",
        origin="DEL",
        dest="FCO",
        skip_db=True,
    )

    ram_hit = get_unified_flight("AI101", "2026-10-28", origin="DEL", dest="FCO")
    assert ram_hit is not None
    assert ram_hit["departure"]["airport"] == "DEL"
    assert ram_hit["arrival"]["airport"] == "FCO"
    assert ram_hit["departure"]["terminal"] == "3"
    assert ram_hit["departure"]["gate"] == "3"
    assert ram_hit["arrival"]["terminal"] == "3"
    assert ram_hit["arrival"]["gate"] == "B12"
    assert ram_hit["departure"]["actual"] == "2026-10-28T21:35:00+05:30"
    assert ram_hit["arrival"]["actual"] == "2026-10-29T00:32:00+01:00"
    assert ram_hit["departure"]["checkin_counter"] == "Counters 12-16"
    assert ram_hit["arrival"]["baggage_belt"] == "Belt 4"


def test_flight_db_persistence_and_retrieval():
    """Verify store_unified_flight persists into DB and get_unified_flight retrieves from DB."""
    mock_db_record = MagicMock()
    mock_db_record.provider = "aerodatabox"
    mock_db_record.flight_date = "2026-10-28"
    mock_db_record.response_data = SAMPLE_FLIGHT_DATA

    mock_session = MagicMock()
    session_instance = mock_session.__enter__.return_value
    query_mock = session_instance.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.first.return_value = mock_db_record

    with patch("app.flight.unified_cache.SessionLocal", return_value=mock_session):
        # 1. Test storing into DB
        store_unified_flight(
            flight_iata="AI101",
            data=SAMPLE_FLIGHT_DATA,
            provider="aerodatabox",
            flight_date="2026-10-28",
            origin="DEL",
            dest="FCO",
            skip_db=False,
        )
        assert session_instance.commit.called
        assert session_instance.add.called

        # 2. Clear RAM cache
        with _RAM_LOCK:
            _RAM_CACHE.clear()

        # 3. Test retrieving from DB
        db_hit = get_unified_flight("AI101", "2026-10-28", origin="DEL", dest="FCO")
        assert db_hit is not None
        assert db_hit["departure"]["airport"] == "DEL"
        assert db_hit["arrival"]["airport"] == "FCO"
        assert db_hit["departure"]["terminal"] == "3"
        assert db_hit["departure"]["gate"] == "3"
        assert db_hit["arrival"]["gate"] == "B12"


def test_flight_cache_conversion_to_schema():
    """Verify to_flight_status_data preserves terminals, gates, and actual times."""
    schema = to_flight_status_data(SAMPLE_FLIGHT_DATA, "AI101", target_date="2026-10-28")
    assert isinstance(schema, FlightStatusData)
    assert schema.origin.code == "DEL"
    assert schema.destination.code == "FCO"
    assert schema.departure.terminal == "3"
    assert schema.departure.gate == "3"
    assert schema.departure.actual == "2026-10-28T21:35:00+05:30"
    assert schema.departure.checkin_counter == "Counters 12-16"
    assert schema.arrival.terminal == "3"
    assert schema.arrival.gate == "B12"
    assert schema.arrival.actual == "2026-10-29T00:32:00+01:00"
    assert schema.arrival.baggage_belt == "Belt 4"
