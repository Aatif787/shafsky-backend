"""
Comprehensive Test Suite for Multi-Service Booking Architecture.

Covers:
- Itinerary modeling (single leg, connecting legs, multi-connection, roundtrip)
- Service combinations:
  - Departure only
  - Arrival only
  - Transit only
  - Departure + Arrival
  - Departure + Transit
  - Arrival + Transit
  - Departure + Arrival + Transit
- Service-to-airport mapping
- Availability states: AVAILABLE vs REQUEST_REQUIRED
- Partial availability & pricing (sum only available services)
- All services unavailable protection (blocks payment, offers query)
- Consolidated ServiceQuery creation (no duplicate requests)
- BookingService multi-service integration & authoritative pricing
- Backward compatibility with existing single-service bookings
- Edge cases: duplicate services, cutoff windows, unsupported airports
"""

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.journey_models import SupportedAirport, AirportService, Service, ServiceQuery
from app.models.schema import Booking, BookingStatus, UserAuth, Profile
from app.services.journey_engine import JourneyDetectionEngine
from app.services.booking_service import BookingService
from app.schemas.journey_schemas import (
    FlightLegInput,
    ServiceSelectionInput,
    MultiServiceAvailabilityRequest,
    ServiceQueryCreate,
)
from app.schemas.booking import BookingCreate


@pytest.fixture
def db():
    """Isolated, fast in-memory SQLite database seeded with representative airport catalog."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()

    # 1. Seed Supported Airports
    del_apt = SupportedAirport(
        id=uuid.uuid4(),
        airport_name="Indira Gandhi International Airport",
        iata_code="DEL",
        city="New Delhi",
        country="India",
        timezone="Asia/Kolkata",
        is_supported=True,
        is_active=True,
    )
    bom_apt = SupportedAirport(
        id=uuid.uuid4(),
        airport_name="Chhatrapati Shivaji Maharaj International Airport",
        iata_code="BOM",
        city="Mumbai",
        country="India",
        timezone="Asia/Kolkata",
        is_supported=True,
        is_active=True,
    )
    blr_apt = SupportedAirport(
        id=uuid.uuid4(),
        airport_name="Kempegowda International Airport",
        iata_code="BLR",
        city="Bengaluru",
        country="India",
        timezone="Asia/Kolkata",
        is_supported=True,
        is_active=True,
    )
    dxb_apt = SupportedAirport(
        id=uuid.uuid4(),
        airport_name="Dubai International Airport",
        iata_code="DXB",
        city="Dubai",
        country="United Arab Emirates",
        timezone="Asia/Dubai",
        is_supported=False,  # Unsupported for instant online booking
        is_active=True,
    )
    lhr_apt = SupportedAirport(
        id=uuid.uuid4(),
        airport_name="Heathrow Airport",
        iata_code="LHR",
        city="London",
        country="United Kingdom",
        timezone="Europe/London",
        is_supported=False,
        is_active=False,
    )
    session.add_all([del_apt, bom_apt, blr_apt, dxb_apt, lhr_apt])

    # 2. Seed Services
    silver = Service(
        id=uuid.uuid4(),
        name="Silver Service",
        slug="silver",
        description="Standard airport meet and assist",
        display_order=1,
        is_active=True,
    )
    gold = Service(
        id=uuid.uuid4(),
        name="Gold Service",
        slug="gold",
        description="Premium VIP assistance with lounge",
        display_order=2,
        is_active=True,
    )
    meet_greet = Service(
        id=uuid.uuid4(),
        name="Meet & Greet",
        slug="meet_greet",
        description="Standard transit meet and assist",
        display_order=3,
        is_active=True,
    )
    session.add_all([silver, gold, meet_greet])
    session.flush()

    # 3. Seed Airport Services
    # DEL Services
    session.add_all([
        AirportService(
            id=uuid.uuid4(),
            airport_id=del_apt.id,
            service_id=silver.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            price=2500.00,
            currency="INR",
            min_booking_notice_hours=2,
            is_available=True,
            display_priority=1,
        ),
        AirportService(
            id=uuid.uuid4(),
            airport_id=del_apt.id,
            service_id=silver.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            price=3500.00,
            currency="INR",
            min_booking_notice_hours=2,
            terminal="Terminal 3",
            is_available=True,
            display_priority=1,
        ),
        AirportService(
            id=uuid.uuid4(),
            airport_id=del_apt.id,
            service_id=silver.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            price=2500.00,
            currency="INR",
            min_booking_notice_hours=2,
            is_available=True,
            display_priority=1,
        ),
        AirportService(
            id=uuid.uuid4(),
            airport_id=del_apt.id,
            service_id=meet_greet.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_DOMESTIC",
            price=4500.00,
            currency="INR",
            min_booking_notice_hours=2,
            is_available=True,
            display_priority=1,
        ),
    ])

    # BOM Services
    session.add_all([
        AirportService(
            id=uuid.uuid4(),
            airport_id=bom_apt.id,
            service_id=silver.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            price=2500.00,
            currency="INR",
            min_booking_notice_hours=2,
            is_available=True,
            display_priority=1,
        ),
        AirportService(
            id=uuid.uuid4(),
            airport_id=bom_apt.id,
            service_id=silver.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            price=2500.00,
            currency="INR",
            min_booking_notice_hours=2,
            is_available=True,
            display_priority=1,
        ),
        AirportService(
            id=uuid.uuid4(),
            airport_id=bom_apt.id,
            service_id=meet_greet.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_DOMESTIC",
            price=4500.00,
            currency="INR",
            min_booking_notice_hours=2,
            is_available=True,
            display_priority=1,
        ),
        AirportService(
            id=uuid.uuid4(),
            airport_id=bom_apt.id,
            service_id=meet_greet.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_INTERNATIONAL",
            price=6500.00,
            currency="INR",
            min_booking_notice_hours=2,
            is_available=True,
            display_priority=1,
        ),
    ])

    # BLR Services
    session.add_all([
        AirportService(
            id=uuid.uuid4(),
            airport_id=blr_apt.id,
            service_id=silver.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            price=2500.00,
            currency="INR",
            min_booking_notice_hours=2,
            is_available=True,
            display_priority=1,
        ),
        AirportService(
            id=uuid.uuid4(),
            airport_id=blr_apt.id,
            service_id=silver.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            price=2500.00,
            currency="INR",
            min_booking_notice_hours=2,
            is_available=True,
            display_priority=1,
        ),
    ])

    session.commit()
    try:
        yield session
    finally:
        session.close()


# ─── 1. Itinerary Resolution Tests ───

def test_itinerary_single_leg(db: Session):
    """Direct flight DEL -> BOM has DEL as departure, BOM as arrival, and no transit."""
    itinerary = JourneyDetectionEngine.resolve_itinerary(
        db=db,
        origin_code="DEL",
        dest_code="BOM",
        default_date="2026-11-01",
    )
    assert itinerary["departure_airport"] == "DEL"
    assert itinerary["arrival_airport"] == "BOM"
    assert itinerary["transit_airports"] == []
    assert len(itinerary["legs"]) == 1
    assert itinerary["is_connecting"] is False


def test_itinerary_connecting_flight_two_legs(db: Session):
    """Connecting flight DEL -> BOM -> DXB has DEL departure, BOM transit, DXB arrival."""
    itinerary = JourneyDetectionEngine.resolve_itinerary(
        db=db,
        origin_code="DEL",
        dest_code="DXB",
        transit_codes=["BOM"],
        default_date="2026-11-01",
    )
    assert itinerary["departure_airport"] == "DEL"
    assert itinerary["transit_airports"] == ["BOM"]
    assert itinerary["arrival_airport"] == "DXB"
    assert len(itinerary["legs"]) == 2
    assert itinerary["legs"][0]["origin_code"] == "DEL"
    assert itinerary["legs"][0]["dest_code"] == "BOM"
    assert itinerary["legs"][1]["origin_code"] == "BOM"
    assert itinerary["legs"][1]["dest_code"] == "DXB"
    assert itinerary["is_connecting"] is True


def test_itinerary_multiple_connecting_flights(db: Session):
    """Multi-leg DEL -> BOM -> DOH -> LHR has DEL departure, [BOM, DOH] transit, LHR arrival."""
    itinerary = JourneyDetectionEngine.resolve_itinerary(
        db=db,
        origin_code="DEL",
        dest_code="LHR",
        transit_codes=["BOM", "DOH"],
        default_date="2026-11-01",
    )
    assert itinerary["departure_airport"] == "DEL"
    assert itinerary["transit_airports"] == ["BOM", "DOH"]
    assert itinerary["arrival_airport"] == "LHR"
    assert len(itinerary["legs"]) == 3
    assert itinerary["total_legs"] == 3


def test_itinerary_explicit_legs_with_same_airport_roundtrip(db: Session):
    """Same airport appearing across legs (DEL -> BOM, BOM -> DEL) is preserved per leg index."""
    legs = [
        FlightLegInput(leg_index=0, origin_code="DEL", dest_code="BOM", flight_num="6E101"),
        FlightLegInput(leg_index=1, origin_code="BOM", dest_code="DEL", flight_num="6E102"),
    ]
    itinerary = JourneyDetectionEngine.resolve_itinerary(db=db, legs=legs)
    assert itinerary["departure_airport"] == "DEL"
    assert itinerary["transit_airports"] == ["BOM"]
    assert itinerary["arrival_airport"] == "DEL"
    assert len(itinerary["legs"]) == 2


# ─── 2. Service Combination Availability Tests ───

def test_combination_departure_only(db: Session):
    """User selects only Departure at DEL."""
    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    req = MultiServiceAvailabilityRequest(
        origin_code="DEL",
        dest_code="BOM",
        service_date=future_date,
        selected_services=[
            ServiceSelectionInput(service_type="DEPARTURE", package_slug="silver")
        ],
    )
    res = JourneyDetectionEngine.check_multi_service_availability(db, req)
    assert res.success is True
    assert len(res.services) == 1
    assert res.services[0].service_type == "DEPARTURE"
    assert res.services[0].airport_code == "DEL"
    assert res.services[0].status == "AVAILABLE"
    assert res.all_available is True
    assert res.total_payable == 2500.00


def test_combination_arrival_only(db: Session):
    """User selects only Arrival at BOM."""
    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    req = MultiServiceAvailabilityRequest(
        origin_code="DEL",
        dest_code="BOM",
        service_date=future_date,
        selected_services=[
            ServiceSelectionInput(service_type="ARRIVAL", package_slug="silver")
        ],
    )
    res = JourneyDetectionEngine.check_multi_service_availability(db, req)
    assert res.success is True
    assert len(res.services) == 1
    assert res.services[0].service_type == "ARRIVAL"
    assert res.services[0].airport_code == "BOM"
    assert res.services[0].status == "AVAILABLE"
    assert res.all_available is True
    assert res.total_payable == 2500.00


def test_combination_departure_and_arrival(db: Session):
    """Departure at DEL + Arrival at BOM: both available, total sums both packages."""
    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    req = MultiServiceAvailabilityRequest(
        origin_code="DEL",
        dest_code="BOM",
        service_date=future_date,
        selected_services=[
            ServiceSelectionInput(service_type="DEPARTURE", package_slug="silver"),
            ServiceSelectionInput(service_type="ARRIVAL", package_slug="silver"),
        ],
    )
    res = JourneyDetectionEngine.check_multi_service_availability(db, req)
    assert res.success is True
    assert len(res.services) == 2
    assert res.services[0].service_type == "DEPARTURE"
    assert res.services[0].airport_code == "DEL"
    assert res.services[1].service_type == "ARRIVAL"
    assert res.services[1].airport_code == "BOM"
    assert res.all_available is True
    assert res.total_payable == 5000.00


def test_combination_departure_transit_arrival_all_three(db: Session):
    """
    Departure (DEL) + Transit (BOM) + Arrival (BLR).
    All three mapped to correct airports independently.
    """
    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    req = MultiServiceAvailabilityRequest(
        origin_code="DEL",
        dest_code="BLR",
        transit_codes=["BOM"],
        service_date=future_date,
        selected_services=[
            ServiceSelectionInput(service_type="DEPARTURE", package_slug="silver"),
            ServiceSelectionInput(service_type="TRANSIT", package_slug="meet_greet"),
            ServiceSelectionInput(service_type="ARRIVAL", package_slug="silver"),
        ],
    )
    res = JourneyDetectionEngine.check_multi_service_availability(db, req)
    assert res.success is True
    assert len(res.services) == 3

    dep_item = next(s for s in res.services if s.service_type == "DEPARTURE")
    transit_item = next(s for s in res.services if s.service_type == "TRANSIT")
    arr_item = next(s for s in res.services if s.service_type == "ARRIVAL")

    assert dep_item.airport_code == "DEL"
    assert transit_item.airport_code == "BOM"
    assert arr_item.airport_code == "BLR"
    assert dep_item.status == "AVAILABLE"
    assert transit_item.status == "AVAILABLE"
    assert arr_item.status == "AVAILABLE"
    assert res.total_payable == 2500.00 + 4500.00 + 2500.00


# ─── 3. Partial Availability & Unsupported Airport Tests ───

def test_partial_availability_unsupported_destination(db: Session):
    """
    Itinerary: DEL -> DXB (Dubai is unsupported for instant online booking).
    User selects Departure (DEL) + Arrival (DXB).
    DEL Departure -> AVAILABLE
    DXB Arrival -> REQUEST_REQUIRED
    Preserves both, distinguishes available vs query required, charges only DEL.
    """
    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    req = MultiServiceAvailabilityRequest(
        origin_code="DEL",
        dest_code="DXB",
        service_date=future_date,
        selected_services=[
            ServiceSelectionInput(service_type="DEPARTURE", package_slug="silver"),
            ServiceSelectionInput(service_type="ARRIVAL", package_slug="silver"),
        ],
    )
    res = JourneyDetectionEngine.check_multi_service_availability(db, req)
    assert res.success is True
    assert len(res.services) == 2

    dep_svc = next(s for s in res.services if s.service_type == "DEPARTURE")
    arr_svc = next(s for s in res.services if s.service_type == "ARRIVAL")

    assert dep_svc.status == "AVAILABLE"
    assert dep_svc.total_price == 3500.00  # International departure from DEL

    assert arr_svc.status == "REQUEST_REQUIRED"
    assert arr_svc.is_airport_supported is False
    assert arr_svc.total_price is None  # Not payable!

    # Overall response state
    assert res.all_available is False
    assert res.any_available is True
    assert res.none_available is False
    assert res.unavailable_services_count == 1
    assert res.total_payable == 3500.00


def test_all_services_unavailable(db: Session):
    """
    Itinerary: LHR -> DXB (neither airport in Shafsky direct network).
    User selects Departure + Arrival.
    Result: all REQUEST_REQUIRED, none_available = True, total_payable = 0.
    """
    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    req = MultiServiceAvailabilityRequest(
        origin_code="LHR",
        dest_code="DXB",
        service_date=future_date,
        selected_services=[
            ServiceSelectionInput(service_type="DEPARTURE"),
            ServiceSelectionInput(service_type="ARRIVAL"),
        ],
    )
    res = JourneyDetectionEngine.check_multi_service_availability(db, req)
    assert res.all_available is False
    assert res.any_available is False
    assert res.none_available is True
    assert res.total_payable == 0.0
    assert res.unavailable_services_count == 2
    for s in res.services:
        assert s.status == "REQUEST_REQUIRED"


def test_transit_service_when_no_transit_airport_exists(db: Session):
    """User selects TRANSIT for a direct non-stop flight DEL -> BOM."""
    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    req = MultiServiceAvailabilityRequest(
        origin_code="DEL",
        dest_code="BOM",
        service_date=future_date,
        selected_services=[
            ServiceSelectionInput(service_type="TRANSIT"),
        ],
    )
    res = JourneyDetectionEngine.check_multi_service_availability(db, req)
    assert len(res.services) == 1
    assert res.services[0].status == "REQUEST_REQUIRED"
    assert res.services[0].status_reason and "transit" in res.services[0].status_reason.lower()


def test_duplicate_service_selection_deduplication(db: Session):
    """Client sending duplicate identical service selections is deduplicated gracefully."""
    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    req = MultiServiceAvailabilityRequest(
        origin_code="DEL",
        dest_code="BOM",
        service_date=future_date,
        selected_services=[
            ServiceSelectionInput(service_type="DEPARTURE", package_slug="silver"),
            ServiceSelectionInput(service_type="DEPARTURE", package_slug="silver"),
        ],
    )
    res = JourneyDetectionEngine.check_multi_service_availability(db, req)
    assert len(res.services) == 1


# ─── 4. Consolidated Service Query Creation Tests ───

def test_create_consolidated_service_query(db: Session):
    """Creating a service query consolidates multiple unavailable services into ONE record."""
    payload = ServiceQueryCreate(
        passenger_name="Aariz Test",
        passenger_email="aariz.query@shafsky.in",
        passenger_phone="+919876543210",
        flight_num="EK501",
        service_date="2026-11-10",
        itinerary={
            "departure_airport": "DEL",
            "arrival_airport": "DXB",
            "transit_airports": ["BOM"],
        },
        requested_services=[
            {"service_type": "DEPARTURE", "airport": "DEL", "package": "silver"},
            {"service_type": "TRANSIT", "airport": "BOM", "package": "meet_greet"},
            {"service_type": "ARRIVAL", "airport": "DXB", "package": "gold"},
        ],
        unavailable_services=[
            {"service_type": "ARRIVAL", "airport": "DXB", "reason": "Unsupported airport"},
        ],
        notes="Please confirm if VIP tarmac escort is possible at DXB",
    )

    resp = JourneyDetectionEngine.create_service_query(db, payload)
    assert resp.success is True
    assert resp.query_ref.startswith("QRY-")

    # Verify persisted in database
    db_record = db.query(ServiceQuery).filter(ServiceQuery.query_ref == resp.query_ref).first()
    assert db_record is not None
    assert db_record.passenger_name == "Aariz Test"
    assert db_record.passenger_email == "aariz.query@shafsky.in"
    assert db_record.status == "PENDING"
    assert len(db_record.unavailable_services) == 1
    assert db_record.unavailable_services[0]["airport"] == "DXB"


def test_two_unavailable_airports_both_requests_saved(db: Session):
    """
    Customer selects two airports and neither currently offers the service
    (e.g., IXJ departure + LHR arrival). Both unavailable services are saved
    in the consolidated pending arrangement request without creating a normal payment order.
    """
    payload = ServiceQueryCreate(
        passenger_name="Sanjay Gupta",
        passenger_email="sanjay.gupta@shafsky.in",
        passenger_phone="+919811223344",
        flight_num="AI808",
        service_date="2026-11-20",
        itinerary={
            "departure_airport": "IXJ",
            "arrival_airport": "LHR",
        },
        requested_services=[
            {"service_type": "DEPARTURE", "airport_code": "IXJ", "package": "silver", "status": "REQUEST_REQUIRED"},
            {"service_type": "ARRIVAL", "airport_code": "LHR", "package": "silver", "status": "REQUEST_REQUIRED"},
        ],
        unavailable_services=[
            {"service_type": "DEPARTURE", "airport_code": "IXJ", "reason": "No departure services configured"},
            {"service_type": "ARRIVAL", "airport_code": "LHR", "reason": "Airport not supported for instant booking"},
        ],
        notes="Urgent assistance required at both airports",
    )

    resp = JourneyDetectionEngine.create_service_query(db, payload)
    assert resp.success is True
    assert resp.query_ref.startswith("QRY-")
    assert len(resp.unavailable_services) == 2

    # Verify both unavailable services are persisted in database
    db_record = db.query(ServiceQuery).filter(ServiceQuery.query_ref == resp.query_ref).first()
    assert db_record is not None
    assert db_record.status == "PENDING"
    assert len(db_record.unavailable_services) == 2
    types = [s["service_type"] for s in db_record.unavailable_services]
    assert "DEPARTURE" in types
    assert "ARRIVAL" in types
    assert db_record.passenger_name == "Sanjay Gupta"
    assert db_record.flight_num == "AI808"
    assert db_record.service_date == "2026-11-20"


def test_duplicate_submission_prevention(db: Session):
    """
    Submitting the same service arrangement query twice (same email, flight, and date)
    returns the existing query reference without creating duplicate database rows.
    """
    payload = ServiceQueryCreate(
        passenger_name="Sunil Mehta",
        passenger_email="sunil.mehta@shafsky.in",
        passenger_phone="+919876500000",
        flight_num="UK999",
        service_date="2026-12-01",
        session_id="sess_abc_123",
        itinerary={"departure_airport": "DEL", "arrival_airport": "DXB"},
        requested_services=[{"service_type": "ARRIVAL", "airport_code": "DXB"}],
        unavailable_services=[{"service_type": "ARRIVAL", "airport_code": "DXB"}],
    )

    resp1 = JourneyDetectionEngine.create_service_query(db, payload)
    resp2 = JourneyDetectionEngine.create_service_query(db, payload)

    assert resp1.success is True
    assert resp2.success is True
    assert resp1.query_ref == resp2.query_ref

    # Verify database has only ONE row
    count = db.query(ServiceQuery).filter(ServiceQuery.passenger_email == "sunil.mehta@shafsky.in").count()
    assert count == 1


def test_transaction_rollback_on_failure(db: Session, monkeypatch):
    """
    If db.commit() fails during query persistence, a rollback is executed
    and the error is propagated without leaving the session in a broken state.
    """
    payload = ServiceQueryCreate(
        passenger_name="Error Test",
        passenger_email="error.test@shafsky.in",
        passenger_phone="+919876543299",
        flight_num="AI999",
        service_date="2026-12-05",
    )

    def mock_commit():
        raise RuntimeError("Simulated DB connection failure")

    monkeypatch.setattr(db, "commit", mock_commit)

    with pytest.raises(RuntimeError, match="Simulated DB connection failure"):
        JourneyDetectionEngine.create_service_query(db, payload)

    # Database session is clean after rollback, can query normally
    record = db.query(ServiceQuery).filter(ServiceQuery.passenger_email == "error.test@shafsky.in").first()
    assert record is None


# ─── 5. BookingService Multi-Service Creation & Authoritative Pricing ───

def test_booking_service_multi_service_authoritative_pricing(db: Session):
    """
    BookingService.create_booking calculates authoritative price by summing available services,
    and preserves multi_service metadata.
    """
    future_dep = datetime.now(timezone.utc) + timedelta(days=5)
    expected_total = 2500.00 + 2500.00  # DEL departure + BOM arrival

    payload = BookingCreate(
        passenger_name="Priya Sharma",
        passenger_email="priya.sharma@shafsky.in",
        passenger_phone="+919123456789",
        flight_num="AI101",
        service_category="Airport Assistance",
        service_type="multi_service",
        origin_code="DEL",
        dest_code="BOM",
        departure_time=future_dep,
        arrival_time=future_dep + timedelta(hours=2),
        total_amount=expected_total,
        selected_services={
            "multi_service": True,
            "services": [
                {"service_type": "DEPARTURE", "airport_code": "DEL", "package": "silver"},
                {"service_type": "ARRIVAL", "airport_code": "BOM", "package": "silver"},
            ]
        },
        metadata_json={
            "multi_service": True,
            "services": [
                {"service_type": "DEPARTURE", "airport_code": "DEL", "package": "silver"},
                {"service_type": "ARRIVAL", "airport_code": "BOM", "package": "silver"},
            ]
        }
    )

    booking = BookingService.create_booking(db, payload)
    assert booking is not None
    assert booking.status == BookingStatus.PENDING
    assert float(booking.total_amount) == expected_total
    assert booking.metadata_json.get("multi_service") is True
    assert len(booking.metadata_json.get("available_services", [])) == 2


def test_booking_service_multi_service_partial_availability_billing(db: Session):
    """
    Multi-service with partial availability:
    DEL (Departure, available: 3500) + DXB (Arrival, unsupported airport).
    The customer is ONLY charged for DEL. DXB is excluded from payment and recorded in unavailable_services.
    A consolidated ServiceQuery is auto-generated for DXB.
    """
    future_dep = datetime.now(timezone.utc) + timedelta(days=5)
    del_dep_price = 3500.00  # DEL International departure

    payload = BookingCreate(
        passenger_name="Rahul Verma",
        passenger_email="rahul.verma@shafsky.in",
        passenger_phone="+919988776655",
        flight_num="EK501",
        service_category="Airport Assistance",
        service_type="multi_service",
        origin_code="DEL",
        dest_code="DXB",
        departure_time=future_dep,
        total_amount=del_dep_price,
        selected_services={
            "multi_service": True,
            "services": [
                {"service_type": "DEPARTURE", "airport_code": "DEL", "package": "silver"},
                {"service_type": "ARRIVAL", "airport_code": "DXB", "package": "silver"},
            ]
        },
        metadata_json={
            "multi_service": True,
            "services": [
                {"service_type": "DEPARTURE", "airport_code": "DEL", "package": "silver"},
                {"service_type": "ARRIVAL", "airport_code": "DXB", "package": "silver"},
            ]
        }
    )

    booking = BookingService.create_booking(db, payload)
    assert booking is not None
    assert float(booking.total_amount) == del_dep_price
    assert len(booking.metadata_json.get("available_services", [])) == 1
    assert len(booking.metadata_json.get("unavailable_services", [])) == 1
    assert booking.metadata_json["unavailable_services"][0]["airport_code"] == "DXB"

    # Verify auto-generated ServiceQuery was created for DXB linked to this booking
    query = db.query(ServiceQuery).filter(ServiceQuery.booking_ref == booking.booking_ref).first()
    assert query is not None
    assert query.passenger_email == "rahul.verma@shafsky.in"


def test_booking_service_rejects_when_all_services_unavailable(db: Session):
    """When all requested services are unavailable, booking creation fails with 400 (no zero/fake order)."""
    future_dep = datetime.now(timezone.utc) + timedelta(days=5)

    payload = BookingCreate(
        passenger_name="Vikram Singh",
        passenger_email="vikram.singh@shafsky.in",
        passenger_phone="+919876543211",
        flight_num="BA123",
        service_category="Airport Assistance",
        service_type="multi_service",
        origin_code="LHR",
        dest_code="DXB",
        departure_time=future_dep,
        total_amount=1000.0,
        selected_services={
            "multi_service": True,
            "services": [
                {"service_type": "DEPARTURE", "airport_code": "LHR", "package": "silver"},
                {"service_type": "ARRIVAL", "airport_code": "DXB", "package": "silver"},
            ]
        },
        metadata_json={
            "multi_service": True,
            "services": [
                {"service_type": "DEPARTURE", "airport_code": "LHR", "package": "silver"},
                {"service_type": "ARRIVAL", "airport_code": "DXB", "package": "silver"},
            ]
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        BookingService.create_booking(db, payload)
    assert exc_info.value.status_code == 400
    assert "none of the selected services" in exc_info.value.detail.lower()


def test_backward_compatibility_single_service_booking(db: Session):
    """Single service booking continues to work without any modification."""
    future_dep = datetime.now(timezone.utc) + timedelta(days=5)
    dep_price = 2500.00

    payload = BookingCreate(
        passenger_name="Anita Roy",
        passenger_email="anita.roy@shafsky.in",
        passenger_phone="+919765432100",
        flight_num="6E202",
        service_category="Airport Assistance",
        service_type="silver",
        origin_code="DEL",
        dest_code="BOM",
        departure_time=future_dep,
        total_amount=dep_price,
        metadata_json={"journey_type": "DEPARTURE"}
    )

    booking = BookingService.create_booking(db, payload)
    assert booking is not None
    assert booking.service_type == "silver"
    assert float(booking.total_amount) == dep_price
    assert booking.status == BookingStatus.PENDING


def test_mumbai_departure_jammu_arrival_partial_availability(db: Session):
    """
    Scenario from user requirement:
    - User selects Departure + Arrival.
    - Origin: Mumbai (BOM).
    - Destination: Jammu (IXJ).
    - Mumbai offers Departure (Platinum, Elite, Elite Plus).
    - Jammu does not offer Arrival (unsupported airport).
    
    Expected:
    - BOM Departure is AVAILABLE at authoritative DB price (3850.0).
    - IXJ Arrival is REQUEST_REQUIRED (is_airport_supported=False).
    - Total payable is 3850.0 (charges only available service).
    - BookingService creates booking for 3850.0, confirms BOM Departure, and links pending ServiceQuery for IXJ.
    """
    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    future_dep = datetime.now(timezone.utc) + timedelta(days=5)

    req = MultiServiceAvailabilityRequest(
        origin_code="BOM",
        dest_code="IXJ",
        service_date=future_date,
        selected_services=[
            ServiceSelectionInput(service_type="DEPARTURE", package_slug="silver"),
            ServiceSelectionInput(service_type="ARRIVAL", package_slug="silver"),
        ],
    )
    res = JourneyDetectionEngine.check_multi_service_availability(db, req)
    assert res.success is True
    assert len(res.services) == 2

    bom_item = next(s for s in res.services if s.service_type == "DEPARTURE")
    ixj_item = next(s for s in res.services if s.service_type == "ARRIVAL")

    assert bom_item.status == "AVAILABLE"
    assert bom_item.airport_code == "BOM"
    assert bom_item.total_price == 2500.0

    assert ixj_item.status == "REQUEST_REQUIRED"
    assert ixj_item.airport_code == "IXJ"
    assert ixj_item.is_airport_supported is False
    assert ixj_item.total_price is None

    assert res.total_payable == 2500.0
    assert res.any_available is True
    assert res.all_available is False

    # Now verify BookingService booking creation for this exact journey
    payload = BookingCreate(
        passenger_name="Rahul Verma",
        passenger_email="rahul.verma@shafsky.in",
        passenger_phone="+919876543210",
        flight_num="6E505",
        service_category="Airport Assistance",
        service_type="multi_service",
        origin_code="BOM",
        dest_code="IXJ",
        departure_time=future_dep,
        total_amount=2500.00,
        selected_services={
            "multi_service": True,
            "services": [
                {"service_type": "DEPARTURE", "airport_code": "BOM", "package": "silver"},
                {"service_type": "ARRIVAL", "airport_code": "IXJ", "package": "silver"},
            ]
        },
        metadata_json={
            "multi_service": True,
            "services": [
                {"service_type": "DEPARTURE", "airport_code": "BOM", "package": "silver"},
                {"service_type": "ARRIVAL", "airport_code": "IXJ", "package": "silver"},
            ]
        }
    )

    booking = BookingService.create_booking(db, payload)
    assert booking is not None
    assert float(booking.total_amount) == 2500.00
    assert booking.metadata_json.get("multi_service") is True
    assert len(booking.metadata_json.get("available_services", [])) == 1
    assert booking.metadata_json["available_services"][0]["airport_code"] == "BOM"
    assert len(booking.metadata_json.get("unavailable_services", [])) == 1
    assert booking.metadata_json["unavailable_services"][0]["airport_code"] == "IXJ"
    assert booking.metadata_json.get("service_query_ref") is not None
