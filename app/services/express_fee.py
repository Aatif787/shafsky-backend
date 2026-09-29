"""
Mumbai Airport Express Fee — effective 1 October 2026.

When a booking at Mumbai Airport (BOM) is created less than 24 hours before
the actual service start time, an Express Fee of 50% of the service fee applies.

This module is the single source of truth for the Express Fee rule.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

# Express Fee effective date: 1 October 2026 00:00:00 IST
_IST = timezone(timedelta(hours=5, minutes=30))
EXPRESS_FEE_EFFECTIVE_DATE = datetime(2026, 10, 1, 0, 0, 0, tzinfo=_IST)

_MUMBAI_IATA = "BOM"
_EXPRESS_FEE_RATE = Decimal("0.50")
_EXPRESS_THRESHOLD = timedelta(hours=24)
_TWOPLACES = Decimal("0.01")

# Service start offsets from scheduled flight time
_DEPARTURE_DOMESTIC_OFFSET = timedelta(hours=1, minutes=30)
_DEPARTURE_INTERNATIONAL_OFFSET = timedelta(hours=3)


def compute_service_start_at(
    *,
    journey_type: str,
    flight_type: str,
    departure_time: Optional[datetime],
    arrival_time: Optional[datetime],
) -> Optional[datetime]:
    """
    Derive the actual service start time from the scheduled flight datetime.

    Departure:
      - Domestic:       scheduled_departure - 1h30m
      - International:  scheduled_departure - 3h

    Arrival:
      - Domestic:       scheduled_arrival
      - International:  scheduled_arrival

    Returns None if the required flight datetime is missing.
    """
    jt = (journey_type or "").strip().upper()
    ft = (flight_type or "").strip().upper()

    if jt == "DEPARTURE":
        if departure_time is None:
            return None
        if ft == "INTERNATIONAL":
            return departure_time - _DEPARTURE_INTERNATIONAL_OFFSET
        # DOMESTIC (and any other) uses the domestic offset
        return departure_time - _DEPARTURE_DOMESTIC_OFFSET

    if jt in ("ARRIVAL", "TRANSIT"):
        if arrival_time is None:
            return None
        return arrival_time

    return None


def calculate_express_fee(
    *,
    airport_code: str,
    service_fee: float,
    booking_created_at: datetime,
    journey_type: str,
    flight_type: str,
    departure_time: Optional[datetime],
    arrival_time: Optional[datetime],
) -> float:
    """
    Calculate the Mumbai Airport Express Fee.

    Returns 0.0 when:
      - Airport is not BOM
      - Booking was created before the effective date (1 Oct 2026)
      - Advance time >= 24 hours
      - Required flight datetime is missing (caller handles validation)

    Returns round(service_fee * 0.50, 2) when:
      - Airport is BOM
      - Booking is on or after 1 Oct 2026
      - Advance time < 24 hours (strictly less than)
    """
    code = (airport_code or "").strip().upper()
    if code != _MUMBAI_IATA:
        return 0.0

    # Effective date gate — booking_created_at must be on or after 1 Oct 2026
    bca = booking_created_at
    if bca.tzinfo is None:
        bca = bca.replace(tzinfo=timezone.utc)
    if bca < EXPRESS_FEE_EFFECTIVE_DATE:
        return 0.0

    service_start_at = compute_service_start_at(
        journey_type=journey_type,
        flight_type=flight_type,
        departure_time=departure_time,
        arrival_time=arrival_time,
    )
    if service_start_at is None:
        # Missing flight datetime — do not invent a fee; return 0
        return 0.0

    # Ensure timezone awareness for comparison
    if service_start_at.tzinfo is None:
        service_start_at = service_start_at.replace(tzinfo=timezone.utc)

    advance_time = service_start_at - bca
    if advance_time < _EXPRESS_THRESHOLD:
        fee_dec = (Decimal(str(service_fee)) * _EXPRESS_FEE_RATE).quantize(
            _TWOPLACES, rounding=ROUND_HALF_UP
        )
        return float(fee_dec)

    return 0.0
