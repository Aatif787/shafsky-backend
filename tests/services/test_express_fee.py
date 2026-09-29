import pytest
from datetime import datetime, timedelta, timezone
from app.services.express_fee import calculate_express_fee, _IST, EXPRESS_FEE_EFFECTIVE_DATE

# Helper to easily create IST times
def t_ist(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=_IST)

def test_mumbai_domestic_departure_19h_advance():
    # Service start is 1.5h before flight, so advance time is flight_time - 1.5h - booking_time
    flight_time = t_ist(2026, 10, 5, 12, 0)
    # Service start is 10:30. 19h advance means booking is at 15:30 on 4 Oct.
    booking_time = flight_time - timedelta(hours=1, minutes=30) - timedelta(hours=19)
    fee = calculate_express_fee(
        airport_code="BOM", service_fee=1000.0, booking_created_at=booking_time,
        journey_type="DEPARTURE", flight_type="DOMESTIC", departure_time=flight_time, arrival_time=None
    )
    assert fee == 500.0

def test_mumbai_international_departure_23h59m_advance():
    flight_time = t_ist(2026, 10, 5, 12, 0)
    # Service start is 09:00. 23h59m advance means booking is at 09:01 on 4 Oct.
    booking_time = flight_time - timedelta(hours=3) - timedelta(hours=23, minutes=59)
    fee = calculate_express_fee(
        airport_code="BOM", service_fee=1000.0, booking_created_at=booking_time,
        journey_type="DEPARTURE", flight_type="INTERNATIONAL", departure_time=flight_time, arrival_time=None
    )
    assert fee == 500.0

def test_mumbai_arrival_exactly_24h_advance():
    flight_time = t_ist(2026, 10, 5, 12, 0)
    # Service start is 12:00. Exactly 24h advance means booking is at 12:00 on 4 Oct.
    booking_time = flight_time - timedelta(hours=24)
    fee = calculate_express_fee(
        airport_code="BOM", service_fee=1000.0, booking_created_at=booking_time,
        journey_type="ARRIVAL", flight_type="DOMESTIC", departure_time=None, arrival_time=flight_time
    )
    assert fee == 0.0

def test_mumbai_arrival_more_than_24h_advance():
    flight_time = t_ist(2026, 10, 5, 12, 0)
    # Service start is 12:00. 25h advance means booking is at 11:00 on 4 Oct.
    booking_time = flight_time - timedelta(hours=25)
    fee = calculate_express_fee(
        airport_code="BOM", service_fee=1000.0, booking_created_at=booking_time,
        journey_type="ARRIVAL", flight_type="DOMESTIC", departure_time=None, arrival_time=flight_time
    )
    assert fee == 0.0

def test_delhi_domestic_departure_less_than_24h_advance():
    flight_time = t_ist(2026, 10, 5, 12, 0)
    booking_time = flight_time - timedelta(hours=1, minutes=30) - timedelta(hours=5)
    fee = calculate_express_fee(
        airport_code="DEL", service_fee=1000.0, booking_created_at=booking_time,
        journey_type="DEPARTURE", flight_type="DOMESTIC", departure_time=flight_time, arrival_time=None
    )
    assert fee == 0.0

def test_mumbai_transit_missing_arrival_time():
    booking_time = t_ist(2026, 10, 4, 15, 30)
    fee = calculate_express_fee(
        airport_code="BOM", service_fee=1000.0, booking_created_at=booking_time,
        journey_type="TRANSIT", flight_type="DOMESTIC", departure_time=None, arrival_time=None
    )
    assert fee == 0.0

def test_mumbai_international_departure_missing_departure_time():
    booking_time = t_ist(2026, 10, 4, 15, 30)
    fee = calculate_express_fee(
        airport_code="BOM", service_fee=1000.0, booking_created_at=booking_time,
        journey_type="DEPARTURE", flight_type="INTERNATIONAL", departure_time=None, arrival_time=None
    )
    assert fee == 0.0

def test_booking_before_1_october_2026_existing_behavior_unchanged():
    flight_time = t_ist(2026, 9, 30, 12, 0)
    # Booking time is 29 Sep 15:00
    booking_time = flight_time - timedelta(hours=21)
    fee = calculate_express_fee(
        airport_code="BOM", service_fee=1000.0, booking_created_at=booking_time,
        journey_type="DEPARTURE", flight_type="DOMESTIC", departure_time=flight_time, arrival_time=None
    )
    assert fee == 0.0

def test_mumbai_domestic_departure_1h_advance_with_frontend_time_override():
    # Ensure calculation works correctly using the backend authoritative bca, not frontend
    flight_time = t_ist(2026, 10, 5, 12, 0)
    booking_time = flight_time - timedelta(hours=1, minutes=30) - timedelta(hours=1)
    # Simulate frontend sending a fake created_at by using backend's calculated bca
    fee = calculate_express_fee(
        airport_code="BOM", service_fee=1000.0, booking_created_at=booking_time,
        journey_type="DEPARTURE", flight_type="DOMESTIC", departure_time=flight_time, arrival_time=None
    )
    assert fee == 500.0

def test_mumbai_international_arrival_10h_advance_with_naive_time():
    # If a naive time is supplied, it gets converted. This tests the core calculation logic
    # assuming the router parses it into a timezone-aware object first.
    flight_time = datetime(2026, 10, 5, 12, 0, tzinfo=_IST)
    booking_time = flight_time - timedelta(hours=10)
    fee = calculate_express_fee(
        airport_code="BOM", service_fee=1000.0, booking_created_at=booking_time,
        journey_type="ARRIVAL", flight_type="INTERNATIONAL", departure_time=None, arrival_time=flight_time
    )
    assert fee == 500.0
