"""
Authoritative booking cutoff: 12h domestic / 24h international.

Uses the flight API scheduled datetime at the service airport, in the
airport timezone. Transit classification is not altered here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.services.service_airport_rules import (
    derive_flight_type_from_route,
    normalize_flight_type,
    normalize_iata,
    normalize_journey_type,
)

DOMESTIC_MIN_HOURS = 12
INTERNATIONAL_MIN_HOURS = 24
IST = timezone(timedelta(hours=5, minutes=30))


def airport_tzinfo(tz_name: Optional[str]):
    """Timezone for the service airport. Never uses the customer device clock."""
    name = (tz_name or "").strip()
    if not name or name in ("Asia/Kolkata", "Asia/Calcutta", "IST"):
        return IST
    if name.upper() in ("UTC", "GMT"):
        return timezone.utc
    if name in ("Asia/Dubai",) or name.upper() == "GST":
        return timezone(timedelta(hours=4))
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return IST


def lookup_airport_timezone(db, iata: Optional[str]) -> str:
    from sqlalchemy import select
    from app.models.journey_models import SupportedAirport

    code = normalize_iata(iata)
    if not code:
        return "Asia/Kolkata"
    row = db.scalar(select(SupportedAirport).where(SupportedAirport.iata_code == code))
    if row and getattr(row, "timezone", None):
        return str(row.timezone)
    return "Asia/Kolkata"


def parse_scheduled_datetime(value: Any, airport_tz) -> Optional[datetime]:
    """Parse API/booking scheduled values. Naive times are airport-local."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=airport_tz)
    return dt


def service_leg_scheduled_datetime(
    journey_type: Optional[str],
    departure_scheduled: Any,
    arrival_scheduled: Any,
    airport_tz,
) -> Optional[datetime]:
    """
    ARRIVAL → scheduled arrival at the service airport.
    DEPARTURE → scheduled departure at the service airport.
    TRANSIT → None (existing transit notice rules stay unchanged).
    """
    jt = normalize_journey_type(journey_type)
    if jt == "ARRIVAL":
        return parse_scheduled_datetime(arrival_scheduled, airport_tz)
    if jt == "DEPARTURE":
        return parse_scheduled_datetime(departure_scheduled, airport_tz)
    return None


def required_notice_hours(flight_type: Optional[str]) -> Optional[int]:
    ft = normalize_flight_type(flight_type)
    if ft == "DOMESTIC":
        return DOMESTIC_MIN_HOURS
    if ft == "INTERNATIONAL":
        return INTERNATIONAL_MIN_HOURS
    return None


def timezone_label(tz) -> str:
    try:
        probe = datetime.now(timezone.utc).astimezone(tz)
        if probe.utcoffset() == timedelta(hours=5, minutes=30):
            return "IST"
        if probe.utcoffset() == timedelta(hours=4):
            return "GST"
        if probe.utcoffset() == timedelta(0):
            return "UTC"
    except Exception:
        pass
    name = getattr(tz, "key", None) or getattr(tz, "tzname", lambda *_: None)(None)
    return str(name or "local")


def format_date_local(dt: datetime, airport_tz) -> str:
    local = dt.astimezone(airport_tz)
    return local.strftime("%d %b %Y")


def format_time_local(dt: datetime, airport_tz) -> str:
    local = dt.astimezone(airport_tz)
    return f"{local.strftime('%H:%M')} {timezone_label(airport_tz)}"


@dataclass
class CutoffResult:
    allowed: bool
    remaining: Optional[timedelta]
    required_hours: Optional[int]
    flight_type: Optional[str]
    scheduled: Optional[datetime]
    customer_message: str
    reason: str


def customer_cutoff_message(flight_type: str, remaining: timedelta, required_hours: int) -> str:
    kind = "domestic" if flight_type == "DOMESTIC" else "international"
    if remaining.total_seconds() <= 0:
        hours_txt = "0"
    else:
        hours_txt = str(max(0, int(remaining.total_seconds() // 3600)))
    return (
        "❌ Booking cannot be completed\n\n"
        f"This {kind} flight is scheduled in approximately {hours_txt} hours.\n"
        f"Shafsky requires at least {required_hours} hours advance booking "
        f"for {kind} services.\n\n"
        "Please choose another eligible flight/date or contact the Shafsky team."
    )


def evaluate_booking_cutoff(
    *,
    scheduled_dt: Any,
    now_utc: datetime,
    airport_tz_name: Optional[str],
    flight_type: Optional[str],
) -> CutoffResult:
    ft = normalize_flight_type(flight_type)
    required = required_notice_hours(ft)
    if required is None:
        return CutoffResult(
            allowed=True,
            remaining=None,
            required_hours=None,
            flight_type=ft,
            scheduled=None,
            customer_message="",
            reason="not_applicable",
        )

    tz = airport_tzinfo(airport_tz_name)
    scheduled = parse_scheduled_datetime(scheduled_dt, tz)
    if scheduled is None:
        return CutoffResult(
            allowed=False,
            remaining=None,
            required_hours=required,
            flight_type=ft,
            scheduled=None,
            customer_message=(
                "❌ Booking cannot be completed\n\n"
                "We could not determine the scheduled flight time required to complete this booking.\n\n"
                "Please choose another eligible flight/date or contact the Shafsky team."
            ),
            reason="missing_scheduled_datetime",
        )

    now = now_utc
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_local = now.astimezone(tz)
    sched_local = scheduled.astimezone(tz)
    remaining = sched_local - now_local
    if remaining >= timedelta(hours=required):
        return CutoffResult(
            allowed=True,
            remaining=remaining,
            required_hours=required,
            flight_type=ft,
            scheduled=scheduled,
            customer_message="",
            reason="ok",
        )
    return CutoffResult(
        allowed=False,
        remaining=remaining,
        required_hours=required,
        flight_type=ft,
        scheduled=scheduled,
        customer_message=customer_cutoff_message(ft, remaining, required),
        reason="cutoff_violation",
    )


def evaluate_cutoff_for_booking(db, booking, now_utc: Optional[datetime] = None) -> CutoffResult:
    """Final server-side cutoff using booking + verified flight metadata."""
    now = now_utc or datetime.now(timezone.utc)
    meta = getattr(booking, "metadata_json", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    journey = normalize_journey_type(
        meta.get("journey_type") or meta.get("direction") or getattr(booking, "service_category", None)
    )
    if journey == "TRANSIT":
        return CutoffResult(
            allowed=True,
            remaining=None,
            required_hours=None,
            flight_type=None,
            scheduled=None,
            customer_message="",
            reason="transit_unchanged",
        )

    origin = meta.get("origin_iata") or getattr(booking, "origin_code", None)
    dest = meta.get("destination_iata") or getattr(booking, "dest_code", None)
    service_airport = (
        meta.get("service_airport")
        or meta.get("airport_code")
        or (origin if journey == "DEPARTURE" else dest)
    )
    tz_name = lookup_airport_timezone(db, service_airport)
    tz = airport_tzinfo(tz_name)

    try:
        flight_type = derive_flight_type_from_route(db, origin, dest, journey)
    except (ValueError, Exception):
        flight_type = None

    if flight_type not in ("DOMESTIC", "INTERNATIONAL"):
        return CutoffResult(
            allowed=True,
            remaining=None,
            required_hours=None,
            flight_type=flight_type,
            scheduled=None,
            customer_message="",
            reason="not_applicable",
        )

    scheduled = service_leg_scheduled_datetime(
        journey,
        getattr(booking, "departure_time", None) or meta.get("departure_scheduled"),
        getattr(booking, "arrival_time", None) or meta.get("arrival_scheduled"),
        tz,
    )
    return evaluate_booking_cutoff(
        scheduled_dt=scheduled,
        now_utc=now,
        airport_tz_name=tz_name,
        flight_type=flight_type,
    )
