"""
Unit Test Suite for Flight Duration Calculation Logic.

Audits and verifies:
- Provider official duration field parsing (flightTime, elapsedTime, blockTime, formatted strings)
- Same-day domestic flights (e.g. 07:00 -> 09:15 = 135m -> '2h 15m')
- International flights across timezones (UTC timestamps, ISO8601)
- Midnight crossing flights (e.g. 23:30 -> 02:15 = 165m -> '2h 45m')
- Cross-month and cross-year midnight crossings
- Delayed flights (utilizing actual/estimated timestamps over scheduled)
- Cancelled flights (fallback to scheduled timestamps or provider duration)
- Provider official duration vs timestamp disparity logging warning
"""

import pytest
from datetime import datetime, timezone, timedelta
import logging

from app.flight.duration import (
    parse_provider_duration_value,
    extract_provider_duration,
    calculate_timestamp_duration,
    format_duration_text,
    compute_flight_duration,
)
from app.flight.schemas import FlightStatusData, FlightCarrier, FlightAirport
from app.flight.providers.aviation_edge_provider import AviationEdgeProvider


def test_parse_provider_duration_value():
    """Verify various provider duration representations parse into integer minutes."""
    assert parse_provider_duration_value(135) == 135
    assert parse_provider_duration_value(135.0) == 135
    assert parse_provider_duration_value("135") == 135
    assert parse_provider_duration_value("2h 15m") == 135
    assert parse_provider_duration_value("2h15m") == 135
    assert parse_provider_duration_value("2 hr 15 min") == 135
    assert parse_provider_duration_value("02:15") == 135
    assert parse_provider_duration_value("02:15:00") == 135
    assert parse_provider_duration_value("45m") == 45
    assert parse_provider_duration_value(None) is None
    assert parse_provider_duration_value("") is None
    assert parse_provider_duration_value(-10) is None


def test_extract_provider_duration_from_various_payload_keys():
    """Audit provider response keys (flightTime, elapsedTime, blockTime, duration, etc.)."""
    payload1 = {"flightTime": "135"}
    assert extract_provider_duration(payload1) == 135

    payload2 = {"flight": {"elapsedTime": 210}}
    assert extract_provider_duration(payload2) == 210

    payload3 = {"timetable": {"blockTime": "3h 30m"}}
    assert extract_provider_duration(payload3) == 210

    payload4 = {"departure": {"duration": "01:45"}}
    assert extract_provider_duration(payload4) == 105

    payload5 = {"flight_time": 90}
    assert extract_provider_duration(payload5) == 90


def test_same_day_flight_duration():
    """Verify same-day flight calculation (07:00 -> 09:15 = 135 mins = '2h 15m')."""
    dep = datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)
    arr = datetime(2026, 8, 4, 9, 15, tzinfo=timezone.utc)

    mins = calculate_timestamp_duration(dep, arr)
    assert mins == 135

    text = format_duration_text(mins)
    assert text == "2h 15m"

    mins_computed, text_computed = compute_flight_duration(None, dep, arr, "AI302")
    assert mins_computed == 135
    assert text_computed == "2h 15m"


def test_international_cross_timezone_flight():
    """Verify international long-haul flight across timezones in UTC ISO8601 (LHR -> JFK)."""
    # 10:00 UTC to 18:00 UTC (8 hours)
    dep = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
    arr = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)

    mins, text = compute_flight_duration(None, dep, arr, "BA177")
    assert mins == 480
    assert text == "8h 0m"


def test_midnight_crossing_flight():
    """Verify cross-day midnight crossing (e.g. 23:30 to 02:15 next day = 165 mins = '2h 45m')."""
    dep = datetime(2026, 8, 4, 23, 30, tzinfo=timezone.utc)
    arr = datetime(2026, 8, 5, 2, 15, tzinfo=timezone.utc)

    mins, text = compute_flight_duration(None, dep, arr, "6E211")
    assert mins == 165
    assert text == "2h 45m"


def test_cross_month_and_year_flight():
    """Verify cross-month and cross-year midnight crossings."""
    # Cross-month: Oct 31 23:00 to Nov 1 03:30 = 4h 30m = 270 mins
    dep_oct = datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)
    arr_nov = datetime(2026, 11, 1, 3, 30, tzinfo=timezone.utc)
    mins_m, text_m = compute_flight_duration(None, dep_oct, arr_nov, "EK504")
    assert mins_m == 270
    assert text_m == "4h 30m"

    # Cross-year: Dec 31 22:00 to Jan 1 06:15 = 8h 15m = 495 mins
    dep_dec = datetime(2026, 12, 31, 22, 0, tzinfo=timezone.utc)
    arr_jan = datetime(2027, 1, 1, 6, 15, tzinfo=timezone.utc)
    mins_y, text_y = compute_flight_duration(None, dep_dec, arr_jan, "QR570")
    assert mins_y == 495
    assert text_y == "8h 15m"


def test_delayed_flight_duration():
    """Verify delayed flight uses actual/estimated departure and arrival times."""
    raw_delayed = {
        "flight": {"iataNumber": "AI302"},
        "status": "active",
        "departure": {
            "iataCode": "DEL",
            "scheduledTime": "2026-08-04T07:00:00.000",
            "estimatedTime": "2026-08-04T07:30:00.000",
            "actualTime": "2026-08-04T07:35:00.000"
        },
        "arrival": {
            "iataCode": "BOM",
            "scheduledTime": "2026-08-04T09:15:00.000",
            "estimatedTime": "2026-08-04T09:45:00.000",
            "actualTime": "2026-08-04T09:50:00.000"
        }
    }
    provider = AviationEdgeProvider()
    status_data = provider._normalize_flight_data(raw_delayed)

    # Actual dep 07:35 to actual arr 09:50 = 135 mins = '2h 15m'
    assert status_data.duration_minutes == 135
    assert status_data.duration_text == "2h 15m"
    assert status_data.duration.formatted == "2h 15m"
    assert status_data.durationMinutes == 135


def test_cancelled_flight_duration():
    """Verify cancelled flight safely computes duration using scheduled timestamps."""
    raw_cancelled = {
        "flight": {"iataNumber": "AI302"},
        "status": "cancelled",
        "departure": {
            "iataCode": "DEL",
            "scheduledTime": "2026-08-04T07:00:00.000"
        },
        "arrival": {
            "iataCode": "BOM",
            "scheduledTime": "2026-08-04T09:15:00.000"
        }
    }
    provider = AviationEdgeProvider()
    status_data = provider._normalize_flight_data(raw_cancelled)

    assert status_data.status == "Cancelled"
    assert status_data.duration_minutes == 135
    assert status_data.duration_text == "2h 15m"


def test_provider_official_duration_priority_and_mismatch_warning(caplog):
    """Verify provider official duration takes priority over calculated timestamp, and logs warning if > 15m disparity."""
    raw_disparity = {
        "flight": {"iataNumber": "AI302", "flightTime": "160"}, # Provider reports 160 mins
        "departure": {"scheduledTime": "2026-08-04T07:00:00.000"},
        "arrival": {"scheduledTime": "2026-08-04T09:15:00.000"} # Timestamp diff is 135 mins (diff = 25m > 15m)
    }

    dep = datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)
    arr = datetime(2026, 8, 4, 9, 15, tzinfo=timezone.utc)

    with caplog.at_level(logging.WARNING):
        mins, text = compute_flight_duration(raw_disparity, dep, arr, "AI302")

    # Preferred provider official duration (160 mins -> 2h 40m)
    assert mins == 160
    assert text == "2h 40m"
    assert "DURATION MISMATCH" in caplog.text
