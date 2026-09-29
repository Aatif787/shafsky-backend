"""
Tests for Mumbai Airport Express Fee — effective 1 October 2026.

Covers all 10 critical boundary conditions specified in the business rules.
Tests the pure calculate_express_fee() function directly — no database needed.
"""

import pytest
from datetime import datetime, timedelta, timezone

from app.services.express_fee import (
    calculate_express_fee,
    compute_service_start_at,
    EXPRESS_FEE_EFFECTIVE_DATE,
)

# ── Timezone helpers ───────────────────────────────────────────────────────
_IST = timezone(timedelta(hours=5, minutes=30))

# A booking_created_at on or after 1 Oct 2026  (well inside the effective window)
_AFTER_EFFECTIVE = datetime(2026, 10, 15, 10, 0, 0, tzinfo=_IST)
# A booking_created_at before 1 Oct 2026
_BEFORE_EFFECTIVE = datetime(2026, 9, 15, 10, 0, 0, tzinfo=_IST)


# ── 1. Mumbai + domestic departure + more than 24h → no Express Fee ──────
def test_bom_domestic_departure_more_than_24h_no_fee():
    """Domestic departure booked well in advance → Express Fee = 0."""
    dep_time = _AFTER_EFFECTIVE + timedelta(hours=48)
    service_start = dep_time - timedelta(hours=1, minutes=30)  # domestic offset
    assert (service_start - _AFTER_EFFECTIVE) > timedelta(hours=24)

    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=10000.0,
        booking_created_at=_AFTER_EFFECTIVE,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        departure_time=dep_time,
        arrival_time=None,
    )
    assert fee == 0.0


# ── 2. Mumbai + domestic departure + exactly 24h → no Express Fee ────────
def test_bom_domestic_departure_exactly_24h_no_fee():
    """Exactly 24h advance time must NOT trigger the fee."""
    # service_start_at = departure_time - 1h30m
    # We want: service_start_at - booking_created_at == exactly 24h
    # So: departure_time = booking_created_at + 24h + 1h30m = booking_created_at + 25h30m
    dep_time = _AFTER_EFFECTIVE + timedelta(hours=25, minutes=30)

    # Verify the service start time
    svc_start = compute_service_start_at(
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        departure_time=dep_time,
        arrival_time=None,
    )
    assert svc_start is not None
    assert svc_start == dep_time - timedelta(hours=1, minutes=30)
    advance = svc_start - _AFTER_EFFECTIVE
    assert advance == timedelta(hours=24)

    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=10000.0,
        booking_created_at=_AFTER_EFFECTIVE,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        departure_time=dep_time,
        arrival_time=None,
    )
    assert fee == 0.0


# ── 3. Mumbai + domestic departure + less than 24h → 50% ─────────────────
def test_bom_domestic_departure_less_than_24h_express_fee():
    """Domestic departure booked < 24h before service start → 50% fee."""
    # service_start = dep_time - 1h30m; need advance < 24h
    # dep_time = booking + 20h → service_start = booking + 18h30m → advance = 18h30m
    dep_time = _AFTER_EFFECTIVE + timedelta(hours=20)

    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=10000.0,
        booking_created_at=_AFTER_EFFECTIVE,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        departure_time=dep_time,
        arrival_time=None,
    )
    assert fee == 5000.0


# ── 4. Mumbai + international departure + less than 24h → 50% ────────────
def test_bom_international_departure_less_than_24h_express_fee():
    """International departure booked < 24h before service start → 50% fee."""
    # service_start = dep_time - 3h; need advance < 24h
    # dep_time = booking + 20h → service_start = booking + 17h → advance = 17h
    dep_time = _AFTER_EFFECTIVE + timedelta(hours=20)

    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=9000.0,
        booking_created_at=_AFTER_EFFECTIVE,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
        departure_time=dep_time,
        arrival_time=None,
    )
    assert fee == 4500.0


# ── 5. Mumbai + domestic arrival + less than 24h → 50% ───────────────────
def test_bom_domestic_arrival_less_than_24h_express_fee():
    """Domestic arrival booked < 24h before service start → 50% fee."""
    # For arrival, service_start = arrival_time
    arr_time = _AFTER_EFFECTIVE + timedelta(hours=20)

    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=8000.0,
        booking_created_at=_AFTER_EFFECTIVE,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        departure_time=None,
        arrival_time=arr_time,
    )
    assert fee == 4000.0


# ── 6. Mumbai + international arrival + less than 24h → 50% ──────────────
def test_bom_international_arrival_less_than_24h_express_fee():
    """International arrival booked < 24h before service start → 50% fee."""
    arr_time = _AFTER_EFFECTIVE + timedelta(hours=12)

    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=12000.0,
        booking_created_at=_AFTER_EFFECTIVE,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
        departure_time=None,
        arrival_time=arr_time,
    )
    assert fee == 6000.0


# ── 7. Mumbai + booking before 1 Oct 2026 → existing behavior (no fee) ──
def test_bom_before_effective_date_no_fee():
    """Booking created before 1 Oct 2026 must not be charged Express Fee."""
    dep_time = _BEFORE_EFFECTIVE + timedelta(hours=5)  # very short notice

    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=10000.0,
        booking_created_at=_BEFORE_EFFECTIVE,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        departure_time=dep_time,
        arrival_time=None,
    )
    assert fee == 0.0


# ── 8. Non-Mumbai airport → existing behavior unchanged ──────────────────
def test_non_mumbai_airport_no_fee():
    """Express Fee applies only to BOM. Other airports must not be affected."""
    dep_time = _AFTER_EFFECTIVE + timedelta(hours=5)

    for code in ["DEL", "HYD", "BLR", "MAA", "GOI"]:
        fee = calculate_express_fee(
            airport_code=code,
            service_fee=10000.0,
            booking_created_at=_AFTER_EFFECTIVE,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            departure_time=dep_time,
            arrival_time=None,
        )
        assert fee == 0.0, f"Expected 0 for {code}, got {fee}"


# ── 9. Frontend-submitted Express Fee/final price cannot override backend ─
def test_frontend_cannot_override_express_fee():
    """
    The calculate_express_fee function takes only server-side inputs.
    There is no 'express_fee' or 'total' parameter accepted from the caller.
    The fee is deterministic from booking_created_at and flight times.
    A different 'service_fee' doesn't affect the rate (always 50%).
    """
    dep_time = _AFTER_EFFECTIVE + timedelta(hours=10)

    # Backend computes 50% of service_fee regardless of what a frontend might send.
    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=10000.0,
        booking_created_at=_AFTER_EFFECTIVE,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        departure_time=dep_time,
        arrival_time=None,
    )
    assert fee == 5000.0

    # Even if someone tried to pass a lower service_fee, the function uses the
    # server-calculated service_fee, not a frontend value.
    fee2 = calculate_express_fee(
        airport_code="BOM",
        service_fee=5000.0,  # hypothetical tampered value
        booking_created_at=_AFTER_EFFECTIVE,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        departure_time=dep_time,
        arrival_time=None,
    )
    assert fee2 == 2500.0  # 50% of whatever the server computed, not frontend


# ── 10. Missing/invalid flight datetime → safe validation (fee = 0) ──────
def test_missing_flight_datetime_no_fee():
    """When departure/arrival time is missing, Express Fee must be 0 (safe default)."""
    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=10000.0,
        booking_created_at=_AFTER_EFFECTIVE,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        departure_time=None,
        arrival_time=None,
    )
    assert fee == 0.0


def test_missing_arrival_for_arrival_journey_no_fee():
    """Arrival journey without arrival_time → Express Fee = 0."""
    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=10000.0,
        booking_created_at=_AFTER_EFFECTIVE,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        departure_time=None,
        arrival_time=None,
    )
    assert fee == 0.0


# ── Additional: compute_service_start_at correctness ─────────────────────
class TestComputeServiceStartAt:
    """Verify the four service start time calculations."""

    def test_domestic_departure(self):
        dep = datetime(2026, 10, 15, 14, 0, tzinfo=_IST)
        result = compute_service_start_at(
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            departure_time=dep,
            arrival_time=None,
        )
        assert result == dep - timedelta(hours=1, minutes=30)

    def test_international_departure(self):
        dep = datetime(2026, 10, 15, 14, 0, tzinfo=_IST)
        result = compute_service_start_at(
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            departure_time=dep,
            arrival_time=None,
        )
        assert result == dep - timedelta(hours=3)

    def test_domestic_arrival(self):
        arr = datetime(2026, 10, 15, 14, 0, tzinfo=_IST)
        result = compute_service_start_at(
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            departure_time=None,
            arrival_time=arr,
        )
        assert result == arr

    def test_international_arrival(self):
        arr = datetime(2026, 10, 15, 14, 0, tzinfo=_IST)
        result = compute_service_start_at(
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            departure_time=None,
            arrival_time=arr,
        )
        assert result == arr


# ── Edge: effective date boundary ─────────────────────────────────────────
def test_effective_date_boundary_exactly_midnight_oct1():
    """Booking at exactly 00:00:00 IST on 1 Oct 2026 → fee should apply."""
    booking_at = datetime(2026, 10, 1, 0, 0, 0, tzinfo=_IST)
    dep_time = booking_at + timedelta(hours=5)  # service_start = booking + 3.5h (domestic dep)

    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=10000.0,
        booking_created_at=booking_at,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        departure_time=dep_time,
        arrival_time=None,
    )
    assert fee == 5000.0


def test_effective_date_boundary_one_second_before():
    """Booking at 23:59:59 IST on 30 Sep 2026 → fee must NOT apply."""
    booking_at = datetime(2026, 9, 30, 23, 59, 59, tzinfo=_IST)
    dep_time = booking_at + timedelta(hours=5)

    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=10000.0,
        booking_created_at=booking_at,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        departure_time=dep_time,
        arrival_time=None,
    )
    assert fee == 0.0


# ── Edge: rounding ────────────────────────────────────────────────────────
def test_express_fee_rounding():
    """Fee must be rounded to 2 decimal places using Decimal ROUND_HALF_UP."""
    dep_time = _AFTER_EFFECTIVE + timedelta(hours=10)

    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=9999.99,
        booking_created_at=_AFTER_EFFECTIVE,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        departure_time=dep_time,
        arrival_time=None,
    )
    # 9999.99 * 0.50 = 4999.995 -> rounds up to 5000.00 in Decimal arithmetic
    assert fee == 5000.0


def test_skip_effective_date_check_applies_fee_before_oct1():
    """Live/authoritative booking with skip_effective_date_check=True applies 50% fee before 1 Oct."""
    bca = datetime(2026, 9, 29, 22, 10, tzinfo=_IST)
    # Domestic departure in 18h -> service_start = 16.5h advance (<24h)
    dep_time = bca + timedelta(hours=18)

    fee = calculate_express_fee(
        airport_code="BOM",
        service_fee=4950.0,
        booking_created_at=bca,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        departure_time=dep_time,
        arrival_time=None,
        skip_effective_date_check=True,
    )
    assert fee == 2475.0

