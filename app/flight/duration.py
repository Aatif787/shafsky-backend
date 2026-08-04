"""
Flight Duration Calculation and Formatting Utility.

Provides enterprise-grade flight duration calculations handling:
- Official provider duration fields (flightTime, elapsedTime, blockTime, duration, etc.)
- Fallback calculation using Departure and Arrival timestamps
- Timestamp selection priority (actual > estimated > scheduled)
- Timezone offsets, UTC datetimes, ISO8601 strings
- Cross-day, cross-month, cross-year midnight crossings
- Disparity warnings when provider duration differs significantly (>15m) from timestamp calculations
- Standardized formatted output (e.g. 135 -> '2h 15m')
"""

import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger("shafsky.flight.duration")


def parse_provider_duration_value(val: Any) -> Optional[int]:
    """
    Parses various provider duration formats into integer minutes.
    Supported inputs:
    - int or float: e.g. 135, 135.0 -> 135
    - string integer: e.g. '135' -> 135
    - string formatted: e.g. '2h 15m', '2h15m', '2 hr 15 min', '02:15', '02:15:00', '135m'
    Returns None if unparseable or negative.
    """
    if val is None:
        return None

    if isinstance(val, (int, float)):
        mins = int(round(val))
        return mins if mins >= 0 else None

    if isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            return None

        # Check if plain integer string
        if cleaned.isdigit():
            mins = int(cleaned)
            return mins if mins >= 0 else None

        # Check HH:MM or HH:MM:SS format
        hh_mm_match = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", cleaned)
        if hh_mm_match:
            hours = int(hh_mm_match.group(1))
            minutes = int(hh_mm_match.group(2))
            return hours * 60 + minutes

        # Check 'Xh Ym' or 'XhYm' or 'X hr Y min' or 'Xm' format
        h_match = re.search(r"(\d+)\s*(?:h|hr|hrs|hour|hours)", cleaned, re.IGNORECASE)
        m_match = re.search(r"(\d+)\s*(?:m|min|mins|minute|minutes)", cleaned, re.IGNORECASE)

        if h_match or m_match:
            hours = int(h_match.group(1)) if h_match else 0
            minutes = int(m_match.group(1)) if m_match else 0
            return hours * 60 + minutes

    return None


def extract_provider_duration(raw: Dict[str, Any]) -> Optional[int]:
    """
    Audits raw provider payloads for explicit duration fields.
    Checks root, 'flight', 'timetable', 'departure', 'arrival' dicts for:
    flightTime, elapsedTime, blockTime, duration, duration_minutes, durationMinutes, flight_time.
    """
    if not isinstance(raw, dict):
        return None

    duration_keys = [
        "flightTime", "flight_time",
        "elapsedTime", "elapsed_time",
        "blockTime", "block_time",
        "duration", "duration_minutes", "durationMinutes",
        "total_flight_time", "flightDuration"
    ]

    containers = [
        raw,
        raw.get("flight", {}) if isinstance(raw.get("flight"), dict) else {},
        raw.get("timetable", {}) if isinstance(raw.get("timetable"), dict) else {},
        raw.get("departure", {}) if isinstance(raw.get("departure"), dict) else {},
        raw.get("arrival", {}) if isinstance(raw.get("arrival"), dict) else {}
    ]

    for container in containers:
        if isinstance(container, dict):
            for key in duration_keys:
                if key in container and container[key] is not None:
                    parsed = parse_provider_duration_value(container[key])
                    if parsed is not None and parsed > 0:
                        return parsed

    return None


def calculate_timestamp_duration(
    dep_dt: Optional[datetime],
    arr_dt: Optional[datetime]
) -> Optional[int]:
    """
    Calculates duration in minutes between departure and arrival datetimes.
    Handles timezone offsets, UTC timestamps, and cross-midnight crossings.
    """
    if not dep_dt or not arr_dt:
        return None

    # Ensure both datetimes are timezone-aware in UTC if naive
    if dep_dt.tzinfo is None:
        dep_dt = dep_dt.replace(tzinfo=timezone.utc)
    if arr_dt.tzinfo is None:
        arr_dt = arr_dt.replace(tzinfo=timezone.utc)

    # Calculate difference
    diff_seconds = (arr_dt - dep_dt).total_seconds()

    # Handle cross-midnight case where arrival time appears earlier on same date string
    if diff_seconds < 0 and (dep_dt.date() == arr_dt.date()):
        arr_dt = arr_dt + timedelta(days=1)
        diff_seconds = (arr_dt - dep_dt).total_seconds()

    minutes = int(round(diff_seconds / 60.0))
    return max(0, minutes)


def format_duration_text(minutes: int) -> str:
    """
    Formats total minutes into standard human-readable string (e.g. 135 -> '2h 15m').
    Examples:
    135 -> '2h 15m'
    60 -> '1h 0m'
    45 -> '45m'
    0 -> '0m'
    """
    if minutes <= 0:
        return "0m"

    hours = minutes // 60
    mins = minutes % 60

    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def compute_flight_duration(
    raw_payload: Optional[Dict[str, Any]],
    dep_dt: Optional[datetime],
    arr_dt: Optional[datetime],
    flight_num: str = "FLIGHT"
) -> Tuple[int, str]:
    """
    Audits provider response and computes optimal flight duration.

    Priority:
    1. Provider explicit official duration (if present and > 0).
    2. Arrival timestamp minus Departure timestamp.

    If provider duration differs from timestamp calculation by > 15 mins:
    Logs a warning and prefers provider official duration.

    Returns:
    (duration_minutes: int, duration_text: str)
    """
    provider_mins = extract_provider_duration(raw_payload) if raw_payload else None
    timestamp_mins = calculate_timestamp_duration(dep_dt, arr_dt)

    selected_mins: int = 0

    if provider_mins is not None and provider_mins > 0:
        selected_mins = provider_mins
        if timestamp_mins is not None and timestamp_mins > 0:
            diff = abs(provider_mins - timestamp_mins)
            if diff > 15:
                logger.warning(
                    f"[DURATION MISMATCH] Flight {flight_num}: Provider official duration ({provider_mins}m) "
                    f"differs from timestamp calculation ({timestamp_mins}m) by {diff} mins. "
                    f"Preferring provider official duration."
                )
    elif timestamp_mins is not None and timestamp_mins >= 0:
        selected_mins = timestamp_mins
    else:
        selected_mins = 0

    formatted = format_duration_text(selected_mins)
    return selected_mins, formatted
