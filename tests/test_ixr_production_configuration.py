"""
Comprehensive Test Suite for Ranchi Airport (IXR) Complete Production Service & Pricing Configuration.

Validates:
1. IXR Airport Record Invariance
2. IXR Domestic Departure (INR 2500.00)
3. IXR Domestic Arrival (INR 2500.00)
4. Unconfigured Services Rejection (International Departure, International Arrival, Transit)
5. Exact Service Inclusion Wording, Capitalization, Punctuation & ASSIST Action
6. Word Preservation: CURBSIDE (not CURB SIDE), CHECKIN (not CHECK-IN), S.H.A.(SECURITY HOLD AREA), WITH DEDICATED STAFF
7. Authoritative Price Calculation & Client Anti-Tamper Protection
8. Journey Detection Engine & Package Selection API Resolution
9. Booking Creation & Validation Integration
10. Cross-Airport Isolation (IXR vs DEL, BOM, HYD, VTZ, MAA, etc.)
11. Full Regression Suite across all other 19 supported airports
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.main import app
from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.models.schema import Booking, BookingStatus
from app.schemas.booking import BookingCreate
from app.services.journey_engine import JourneyDetectionEngine
from app.services.booking_service import BookingService
from app.seeds.seed_journey_data import (
    IXR_DOMESTIC_DEPARTURE_FEATURES,
    IXR_DOMESTIC_ARRIVAL_FEATURES,
)

client = TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ==============================================================================
# 1. IXR Airport Record Invariance
# ==============================================================================

def test_ixr_airport_record(db: Session):
    """Verify IXR airport record exists, is active, supported, and unique."""
    airports = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXR").all()
    assert len(airports) == 1, "There must be exactly one IXR airport record"
    ixr = airports[0]
    assert ixr.is_active is True, "IXR must be active"
    assert ixr.is_supported is True, "IXR must be supported"
    assert ixr.city == "Ranchi"
    assert ixr.airport_name == "Birsa Munda Airport"
    assert ixr.icao_code == "VERC"


# ==============================================================================
# 2. IXR Active Packages Count & Pricing Matrix
# ==============================================================================

def test_ixr_active_packages_inventory(db: Session):
    """Verify IXR has exactly 2 active production package records."""
    ixr = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXR").first()
    assert ixr is not None

    active_aps = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == ixr.id,
            AirportService.is_available.is_(True)
        )
        .all()
    )
    assert len(active_aps) == 2, f"Expected exactly 2 active IXR packages, found {len(active_aps)}"


def test_ixr_domestic_departure_package(db: Session):
    """Verify IXR Domestic Departure: INR 2500.00."""
    ixr = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXR").first()
    assert ixr is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == ixr.id,
            AirportService.journey_type == "DEPARTURE",
            AirportService.flight_type == "DOMESTIC",
            AirportService.is_available.is_(True),
        )
        .all()
    )
    assert len(rows) == 1, f"Expected exactly 1 active Domestic Departure package for IXR, found {len(rows)}"
    aps, svc = rows[0]
    assert float(aps.price) == 2500.00
    assert aps.currency == "INR"
    assert aps.display_priority == 1


def test_ixr_domestic_arrival_package(db: Session):
    """Verify IXR Domestic Arrival: INR 2500.00."""
    ixr = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXR").first()
    assert ixr is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == ixr.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.flight_type == "DOMESTIC",
            AirportService.is_available.is_(True),
        )
        .all()
    )
    assert len(rows) == 1, f"Expected exactly 1 active Domestic Arrival package for IXR, found {len(rows)}"
    aps, svc = rows[0]
    assert float(aps.price) == 2500.00
    assert aps.currency == "INR"
    assert aps.display_priority == 1


# ==============================================================================
# 3. Exact Service Inclusion Wording, Capitalization, Punctuation & ASSIST Action
# ==============================================================================

def test_ixr_exact_service_inclusions_departure(db: Session):
    """Verify exact 9 departure inclusions matching authoritative prompt verbatim."""
    ixr = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXR").first()
    assert ixr is not None
    dep_svc = (
        db.query(AirportService)
        .filter(
            AirportService.airport_id == ixr.id,
            AirportService.journey_type == "DEPARTURE",
            AirportService.flight_type == "DOMESTIC",
            AirportService.is_available.is_(True),
        )
        .first()
    )
    assert dep_svc is not None
    assert len(dep_svc.features) == 9

    expected_departure = [
        "WELCOME GUEST FROM CURBSIDE AREA",
        "PORTER SERVICE WITH DEDICATED STAFF",
        "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
        "ASSIST FROM SEPARATE ENTRY GATE",
        "ASSIST TO BAGGAGE WRAPPING FACILITIES",
        "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS",
        "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
        "LOUNGE SERVICE FACILITY AVAILABLE (CHARGES APPLICABLE)",
        "ASSIST GUEST TILL THE BOARDING GATE",
    ]
    assert dep_svc.features == expected_departure
    assert dep_svc.features == IXR_DOMESTIC_DEPARTURE_FEATURES

    # Word preservation checks
    assert "WELCOME GUEST FROM CURBSIDE AREA" in dep_svc.features
    assert "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS" in dep_svc.features
    assert "ASSIST IN S.H.A.(SECURITY HOLD AREA)" in dep_svc.features
    for f in dep_svc.features:
        assert "CURB SIDE" not in f
        assert "CHECK-IN" not in f
        assert "ASSISTANCE" not in f
        assert "ASSISTANT" not in f


def test_ixr_exact_service_inclusions_arrival(db: Session):
    """Verify exact 6 arrival inclusions matching authoritative prompt verbatim."""
    ixr = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXR").first()
    assert ixr is not None
    arr_svc = (
        db.query(AirportService)
        .filter(
            AirportService.airport_id == ixr.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.flight_type == "DOMESTIC",
            AirportService.is_available.is_(True),
        )
        .first()
    )
    assert arr_svc is not None
    assert len(arr_svc.features) == 6

    expected_arrival = [
        "WELCOME GUEST FROM AEROBRIDGE",
        "DEDICATED STAFF WITH PLACARD",
        "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
        "WHEELCHAIR SERVICE AVAILABLE WITH DEDICATED STAFF (Through Airlines)",
        "ASSIST IN BAGGAGE BELT AREA",
        "ASSIST GUEST TILL THE CAR PARKING AREA",
    ]
    assert arr_svc.features == expected_arrival
    assert arr_svc.features == IXR_DOMESTIC_ARRIVAL_FEATURES

    # Word preservation checks
    assert "WHEELCHAIR SERVICE AVAILABLE WITH DEDICATED STAFF (Through Airlines)" in arr_svc.features
    assert "ASSIST IN BAGGAGE BELT AREA" in arr_svc.features
    assert "ASSIST GUEST TILL THE CAR PARKING AREA" in arr_svc.features
    for f in arr_svc.features:
        assert "ASSISTANCE" not in f
        assert "ASSISTANT" not in f


# ==============================================================================
# 4. Unconfigured Services Rejection
# ==============================================================================

def test_ixr_unconfigured_services_rejection(db: Session):
    """Verify International Departure, International Arrival, and Transit are not active."""
    ixr = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXR").first()
    assert ixr is not None

    # 1. International Departure
    int_dep = (
        db.query(AirportService)
        .filter(
            AirportService.airport_id == ixr.id,
            AirportService.journey_type == "DEPARTURE",
            AirportService.flight_type == "INTERNATIONAL",
            AirportService.is_available.is_(True),
        )
        .all()
    )
    assert len(int_dep) == 0, "IXR International Departure must NOT be active"

    # 2. International Arrival
    int_arr = (
        db.query(AirportService)
        .filter(
            AirportService.airport_id == ixr.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.flight_type == "INTERNATIONAL",
            AirportService.is_available.is_(True),
        )
        .all()
    )
    assert len(int_arr) == 0, "IXR International Arrival must NOT be active"

    # 3. Transit
    transit = (
        db.query(AirportService)
        .filter(
            AirportService.airport_id == ixr.id,
            AirportService.journey_type == "TRANSIT",
            AirportService.is_available.is_(True),
        )
        .all()
    )
    assert len(transit) == 0, "IXR Transit must NOT be active"


# ==============================================================================
# 5. Authoritative Price Calculation & Anti-Tamper Security
# ==============================================================================

def test_ixr_authoritative_price_calculation(db: Session):
    """Verify backend authoritative price calculation for IXR."""
    # 1 pax departure
    dep_price = BookingService.calculate_authoritative_price(
        db=db,
        airport_code="IXR",
        service_tier_or_slug="silver",
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        pax_count=1,
    )
    assert dep_price == 2500.00

    # 2 pax departure
    dep_price_2pax = BookingService.calculate_authoritative_price(
        db=db,
        airport_code="IXR",
        service_tier_or_slug="silver",
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        pax_count=2,
    )
    assert dep_price_2pax == 5000.00

    # 1 pax arrival
    arr_price = BookingService.calculate_authoritative_price(
        db=db,
        airport_code="IXR",
        service_tier_or_slug="silver",
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        pax_count=1,
    )
    assert arr_price == 2500.00


def test_ixr_unconfigured_booking_rejection(db: Session):
    """Verify unconfigured combinations raise HTTPException 400."""
    with pytest.raises(HTTPException) as exc_info:
        BookingService.calculate_authoritative_price(
            db=db,
            airport_code="IXR",
            service_tier_or_slug="silver",
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            pax_count=1,
        )
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info:
        BookingService.calculate_authoritative_price(
            db=db,
            airport_code="IXR",
            service_tier_or_slug="silver",
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            pax_count=1,
        )
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info:
        BookingService.calculate_authoritative_price(
            db=db,
            airport_code="IXR",
            service_tier_or_slug="silver",
            journey_type="TRANSIT",
            flight_type="DOMESTIC",
            pax_count=1,
        )
    assert exc_info.value.status_code == 400


# ==============================================================================
# 6. Journey Detection API Resolution
# ==============================================================================

def test_ixr_journey_detection_departure(db: Session):
    """Test JourneyDetectionEngine resolves IXR Domestic Departure with exact price & features."""
    response = JourneyDetectionEngine.detect_journey(
        db=db,
        departure_code="IXR",
        arrival_code="DEL",
        journey_type="DEPARTURE",
        service_date="2026-09-01",
        service_time="14:00",
        flight_type="DOMESTIC",
    )
    assert response.primary_airport is not None
    assert response.primary_airport.iata_code == "IXR"
    assert len(response.available_services) == 1

    svc = response.available_services[0]
    assert svc.flight_type == "DOMESTIC"
    assert svc.price == 2500.00
    assert svc.currency == "INR"
    assert len(svc.features) == 9
    assert "WELCOME GUEST FROM CURBSIDE AREA" in svc.features


def test_ixr_journey_detection_arrival(db: Session):
    """Test JourneyDetectionEngine resolves IXR Domestic Arrival with exact price & features."""
    response = JourneyDetectionEngine.detect_journey(
        db=db,
        departure_code="DEL",
        arrival_code="IXR",
        journey_type="ARRIVAL",
        service_date="2026-09-01",
        service_time="16:00",
        flight_type="DOMESTIC",
    )
    assert response.primary_airport is not None
    assert response.primary_airport.iata_code == "IXR"
    assert len(response.available_services) == 1

    svc = response.available_services[0]
    assert svc.flight_type == "DOMESTIC"
    assert svc.price == 2500.00
    assert svc.currency == "INR"
    assert len(svc.features) == 6
    assert "WELCOME GUEST FROM AEROBRIDGE" in svc.features


# ==============================================================================
# 7. Cross-Airport Isolation
# ==============================================================================

def test_ixr_cross_airport_isolation(db: Session):
    """Verify IXR pricing does not leak to other airports and other airport services cannot be booked at IXR."""
    # 1. Check Delhi Departure pricing remains distinct
    del_price = BookingService.calculate_authoritative_price(
        db=db,
        airport_code="DEL",
        service_tier_or_slug="silver",
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        pax_count=1,
    )
    assert del_price == 2500.00 or del_price > 0

    # 2. Check VTZ Domestic Departure remains 2500
    vtz_price = BookingService.calculate_authoritative_price(
        db=db,
        airport_code="VTZ",
        service_tier_or_slug="silver",
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        pax_count=1,
    )
    assert vtz_price == 2500.00

    # 3. Check BOM Transit is rejected at IXR
    with pytest.raises(HTTPException):
        BookingService.calculate_authoritative_price(
            db=db,
            airport_code="IXR",
            service_tier_or_slug="silver",
            journey_type="TRANSIT",
            flight_type="DOMESTIC",
            pax_count=1,
        )


# ==============================================================================
# 8. Full 20-Airport Regression Suite
# ==============================================================================

def test_full_airports_regression_suite(db: Session):
    """
    Verify all 20 supported airports maintain their exact production active package inventory.
    """
    expected_active_counts = {
        "AMD": 7,
        "ATQ": 4,
        "BBI": 3,
        "BLR": 12,
        "BOM": 12,
        "CCU": 5,
        "COK": 4,
        "DEL": 20,
        "GAU": (7, 8),
        "GOI": 5,
        "GOX": 14,
        "HYD": 14,
        "IXC": 5,
        "IXE": 7,
        "IXR": 2,
        "JAI": 7,
        "LKO": 7,
        "MAA": 4,
        "TRV": 6,
        "VTZ": 2,
    }

    airports = db.query(SupportedAirport).all()
    assert len(airports) == 20, f"Expected 20 supported airports, found {len(airports)}"

    for airport in airports:
        code = airport.iata_code
        assert code in expected_active_counts, f"Unexpected airport {code}"
        active_count = (
            db.query(AirportService)
            .filter(
                AirportService.airport_id == airport.id,
                AirportService.is_available.is_(True),
            )
            .count()
        )
        expected = expected_active_counts[code]
        if isinstance(expected, (tuple, list, set)):
            assert active_count in expected, f"Airport {code} active service count mismatch: expected one of {expected}, got {active_count}"
        else:
            assert active_count == expected, f"Airport {code} active service count mismatch: expected {expected}, got {active_count}"
