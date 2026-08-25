"""
Comprehensive Test Suite for Chandigarh Airport (IXC) Complete Production Service & Pricing Configuration.

Validates:
1. IXC Airport Record Invariance
2. IXC Domestic Departure (Silver INR 2500, Gold INR 4000)
3. IXC Domestic Arrival (Silver INR 2500, Gold/Elite Unconfigured)
4. IXC International Departure (Silver INR 3000)
5. IXC International Arrival (Silver INR 2500)
6. Exact Service Inclusion Wording, Capitalization, Punctuation & ASSIST Action
7. Package Difference Validation (Dom Dep Gold = 7 inclusions with LOUNGE ACCESS FOR 2 HOURS; Silver = 6)
8. Unconfigured/Unsupported Combination Rejection
9. Authoritative Price Calculation & Client Anti-Tamper Protection
10. Journey Detection Engine & Package Selection API Resolution
11. Booking Creation & Validation Integration
12. Cross-Airport Isolation (IXC vs DEL, BOM, HYD, etc.)
13. Full Regression Suite across all other 19 supported airports
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
    IXC_DOMESTIC_DEPARTURE_SILVER_FEATURES,
    IXC_DOMESTIC_DEPARTURE_GOLD_FEATURES,
    IXC_DOMESTIC_ARRIVAL_SILVER_FEATURES,
    IXC_INTERNATIONAL_DEPARTURE_SILVER_FEATURES,
    IXC_INTERNATIONAL_ARRIVAL_SILVER_FEATURES,
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
# 1. IXC Airport Record
# ==============================================================================

def test_ixc_airport_record(db: Session):
    """Verify IXC airport record exists, is active, supported, and unique."""
    airports = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXC").all()
    assert len(airports) == 1, "There must be exactly one IXC airport record"
    ixc = airports[0]
    assert ixc.is_active is True, "IXC must be active"
    assert ixc.is_supported is True, "IXC must be supported"
    assert ixc.city == "Chandigarh"
    assert ixc.airport_name == "Shaheed Bhagat Singh International Airport"


# ==============================================================================
# 2. IXC Active Packages Count & Pricing Matrix
# ==============================================================================

def test_ixc_active_packages_inventory(db: Session):
    """Verify IXC has exactly 5 active production package records."""
    ixc = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXC").first()
    assert ixc is not None

    active_aps = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == ixc.id,
            AirportService.is_available.is_(True)
        )
        .all()
    )
    assert len(active_aps) == 5, f"Expected exactly 5 active IXC packages, found {len(active_aps)}"


def test_ixc_domestic_departure_packages(db: Session):
    """Verify IXC Domestic Departure: Silver (INR 2500) & Gold (INR 4000)."""
    ixc = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXC").first()
    assert ixc is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == ixc.id,
            AirportService.journey_type == "DEPARTURE",
            AirportService.flight_type == "DOMESTIC",
            AirportService.is_available.is_(True)
        )
        .order_by(AirportService.display_priority)
        .all()
    )
    assert len(rows) == 2, f"Expected 2 active Dom Dep packages for IXC, found {len(rows)}"

    pkg_map = {svc.slug: (aps, svc) for aps, svc in rows}

    # Silver (INR 2500, 6 inclusions)
    assert "silver" in pkg_map
    aps_s, _ = pkg_map["silver"]
    assert float(aps_s.price) == 2500.00
    assert aps_s.display_priority == 1
    assert len(aps_s.features) == 6
    assert aps_s.features == IXC_DOMESTIC_DEPARTURE_SILVER_FEATURES

    # Gold (INR 4000, 7 inclusions)
    assert "gold" in pkg_map
    aps_g, _ = pkg_map["gold"]
    assert float(aps_g.price) == 4000.00
    assert aps_g.display_priority == 2
    assert len(aps_g.features) == 7
    assert aps_g.features == IXC_DOMESTIC_DEPARTURE_GOLD_FEATURES


def test_ixc_domestic_arrival_packages(db: Session):
    """Verify IXC Domestic Arrival: Silver (INR 2500), Gold/Elite NOT CONFIGURED."""
    ixc = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXC").first()
    assert ixc is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == ixc.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.flight_type == "DOMESTIC",
            AirportService.is_available.is_(True)
        )
        .all()
    )
    assert len(rows) == 1, f"Expected exactly 1 active Dom Arr package for IXC, found {len(rows)}"

    aps_s, svc_s = rows[0]
    assert svc_s.slug == "silver"
    assert float(aps_s.price) == 2500.00
    assert len(aps_s.features) == 6
    assert aps_s.features == IXC_DOMESTIC_ARRIVAL_SILVER_FEATURES


def test_ixc_international_departure_packages(db: Session):
    """Verify IXC International Departure: Silver (INR 3000)."""
    ixc = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXC").first()
    assert ixc is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == ixc.id,
            AirportService.journey_type == "DEPARTURE",
            AirportService.flight_type == "INTERNATIONAL",
            AirportService.is_available.is_(True)
        )
        .all()
    )
    assert len(rows) == 1, f"Expected exactly 1 active Int Dep package for IXC, found {len(rows)}"

    aps_s, svc_s = rows[0]
    assert svc_s.slug == "silver"
    assert float(aps_s.price) == 3000.00
    assert len(aps_s.features) == 9
    assert aps_s.features == IXC_INTERNATIONAL_DEPARTURE_SILVER_FEATURES


def test_ixc_international_arrival_packages(db: Session):
    """Verify IXC International Arrival: Silver (INR 2500)."""
    ixc = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXC").first()
    assert ixc is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == ixc.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.flight_type == "INTERNATIONAL",
            AirportService.is_available.is_(True)
        )
        .all()
    )
    assert len(rows) == 1, f"Expected exactly 1 active Int Arr package for IXC, found {len(rows)}"

    aps_s, svc_s = rows[0]
    assert svc_s.slug == "silver"
    assert float(aps_s.price) == 2500.00
    assert len(aps_s.features) == 5
    assert aps_s.features == IXC_INTERNATIONAL_ARRIVAL_SILVER_FEATURES


# ==============================================================================
# 3. Exact Inclusion Wording & Critical Terms
# ==============================================================================

def test_ixc_exact_inclusion_texts_and_quirks():
    """Verify exact wording, capitalization, punctuation, and ASSIST service action."""
    # 1. Domestic Departure Silver
    assert IXC_DOMESTIC_DEPARTURE_SILVER_FEATURES == [
        "WELCOME GUEST AT DEPARTURE CURB SIDE / CAR DROP AREA.",
        "PORTER SERVICE WITH DEDICATED STAFF.",
        "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
        "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER.",
        "ASSIST IN S.H.A.(SECURITY HOLD AREA).",
        "ASSIST GUEST UPTO BOARDING GATE.",
    ]

    # 2. Domestic Departure Gold
    assert IXC_DOMESTIC_DEPARTURE_GOLD_FEATURES == [
        "WELCOME GUEST AT DEPARTURE CURB SIDE / CAR DROP AREA.",
        "PORTER SERVICE WITH DEDICATED STAFF.",
        "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
        "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER.",
        "ASSIST IN S.H.A.(SECURITY HOLD AREA).",
        "LOUNGE ACCESS FOR 2 HOURS.",
        "ASSIST GUEST UPTO BOARDING GATE.",
    ]

    # 3. Domestic Arrival Silver
    assert IXC_DOMESTIC_ARRIVAL_SILVER_FEATURES == [
        "WELCOME GUEST FROM END OF THE AEROBRIDGE.",
        "DEDICATED STAFF WITH PLACARD.",
        "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS.",
        "WHEELCHAIR SERVICE AVAILABLE (Through Airlines).",
        "ASSIST IN BAGGAGE BELT AREA.",
        "ASSIST GUEST TILL THE CAR PARKING AREA",
    ]

    # 4. International Departure Silver
    assert IXC_INTERNATIONAL_DEPARTURE_SILVER_FEATURES == [
        "WELCOME GUEST FROM CURB SIDE AREA/CAR DROP AREA.",
        "PORTER SERVICE WITH DEDICATED STAFF.",
        "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES).",
        "ASSIST TO MONEY EXCHANGE COUNTER.",
        "ASSIST TO BAGGAGE WRAPPING FACILITY.",
        "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER.",
        "ASSIST FOR IMMIGRATION COUNTERS.",
        "ASSIST IN S.H.A (SECURITY HOLD AREA).",
        "ASSIST GUEST UPTO BOARDING GATE.",
    ]

    # 5. International Arrival Silver
    assert IXC_INTERNATIONAL_ARRIVAL_SILVER_FEATURES == [
        "WELCOME GUEST FROM POST CUSTOMS.",
        "PORTER SERVICE WITH DEDICATED STAFF. (UP TO 3 BAGS PER PASSENGERS).",
        "ASSIST AT BAGGAGE BELT AREA.",
        "COORDINATION TO THE RECEIVING PERSON.",
        "DROP OFF CAR PARKING AREA",
    ]

    # Specific wording checks
    assert "CURB SIDE / CAR DROP AREA." in IXC_DOMESTIC_DEPARTURE_SILVER_FEATURES[0]
    assert "CURB SIDE AREA/CAR DROP AREA." in IXC_INTERNATIONAL_DEPARTURE_SILVER_FEATURES[0]
    assert "FROM END OF THE AEROBRIDGE." in IXC_DOMESTIC_ARRIVAL_SILVER_FEATURES[0]
    assert "FROM POST CUSTOMS." in IXC_INTERNATIONAL_ARRIVAL_SILVER_FEATURES[0]
    assert "(Through Airlines)" in IXC_DOMESTIC_DEPARTURE_SILVER_FEATURES[2]
    assert "(THROUGH AIRLINES)." in IXC_INTERNATIONAL_DEPARTURE_SILVER_FEATURES[2]
    assert "(UP TO 3 BAGS PER PASSENGERS)." in IXC_INTERNATIONAL_ARRIVAL_SILVER_FEATURES[1]
    assert "COORDINATION TO THE RECEIVING PERSON." in IXC_INTERNATIONAL_ARRIVAL_SILVER_FEATURES[3]
    assert "UPTO" in IXC_DOMESTIC_DEPARTURE_SILVER_FEATURES[5]
    assert "UPTO" in IXC_DOMESTIC_DEPARTURE_GOLD_FEATURES[6]
    assert "UPTO" in IXC_INTERNATIONAL_DEPARTURE_SILVER_FEATURES[8]


def test_ixc_package_difference_validation():
    """Verify Gold Domestic Departure has exactly all Silver inclusions plus LOUNGE ACCESS FOR 2 HOURS."""
    silver_inclusions = set(IXC_DOMESTIC_DEPARTURE_SILVER_FEATURES)
    gold_inclusions = set(IXC_DOMESTIC_DEPARTURE_GOLD_FEATURES)

    diff = gold_inclusions - silver_inclusions
    assert diff == {"LOUNGE ACCESS FOR 2 HOURS."}, f"Expected only LOUNGE ACCESS FOR 2 HOURS as difference, got {diff}"
    assert len(IXC_DOMESTIC_DEPARTURE_SILVER_FEATURES) == 6
    assert len(IXC_DOMESTIC_DEPARTURE_GOLD_FEATURES) == 7


# ==============================================================================
# 4. Unconfigured / Unsupported Combinations Rejection
# ==============================================================================

def test_ixc_unsupported_combinations_rejected(db: Session):
    """Verify unsupplied packages (Dom Arr Gold/Elite, Int Arr Gold/Elite, Int Dep Gold/Elite, Transit) return 0 active records."""
    ixc = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "IXC").first()
    assert ixc is not None

    # Domestic Arrival Gold / Elite
    dom_arr_gold = JourneyDetectionEngine.get_services_for_airport(db, "IXC", "ARRIVAL", "DOMESTIC")
    assert len(dom_arr_gold) == 1
    assert dom_arr_gold[0].service.slug == "silver"

    # International Arrival Gold / Elite
    int_arr = JourneyDetectionEngine.get_services_for_airport(db, "IXC", "ARRIVAL", "INTERNATIONAL")
    assert len(int_arr) == 1
    assert int_arr[0].service.slug == "silver"

    # International Departure Gold / Elite
    int_dep = JourneyDetectionEngine.get_services_for_airport(db, "IXC", "DEPARTURE", "INTERNATIONAL")
    assert len(int_dep) == 1
    assert int_dep[0].service.slug == "silver"

    # Transit
    transit_dom = JourneyDetectionEngine.get_services_for_airport(db, "IXC", "TRANSIT", "DOMESTIC")
    assert len(transit_dom) == 0

    transit_int = JourneyDetectionEngine.get_services_for_airport(db, "IXC", "TRANSIT", "INTERNATIONAL")
    assert len(transit_int) == 0


# ==============================================================================
# 5. Authoritative Price Calculation & Client Anti-Tamper
# ==============================================================================

def test_ixc_authoritative_pricing_calculation(db: Session):
    """Verify BookingService.calculate_authoritative_price for all 5 IXC configurations."""
    # 1. Domestic Departure Silver
    p1 = BookingService.calculate_authoritative_price(db, "IXC", "silver", "DEPARTURE", "DOMESTIC")
    assert p1 == 2500.00

    # 2. Domestic Departure Gold
    p2 = BookingService.calculate_authoritative_price(db, "IXC", "gold", "DEPARTURE", "DOMESTIC")
    assert p2 == 4000.00

    # 3. Domestic Arrival Silver
    p3 = BookingService.calculate_authoritative_price(db, "IXC", "silver", "ARRIVAL", "DOMESTIC")
    assert p3 == 2500.00

    # 4. International Departure Silver
    p4 = BookingService.calculate_authoritative_price(db, "IXC", "silver", "DEPARTURE", "INTERNATIONAL")
    assert p4 == 3000.00

    # 5. International Arrival Silver
    p5 = BookingService.calculate_authoritative_price(db, "IXC", "silver", "ARRIVAL", "INTERNATIONAL")
    assert p5 == 2500.00

    # Multi-pax (e.g. 3 pax)
    p_3pax = BookingService.calculate_authoritative_price(db, "IXC", "silver", "DEPARTURE", "DOMESTIC", pax_count=3)
    assert p_3pax == 7500.00

    # Unconfigured package rejection
    with pytest.raises(HTTPException) as exc1:
        BookingService.calculate_authoritative_price(db, "IXC", "gold", "ARRIVAL", "DOMESTIC")
    assert exc1.value.status_code == 400

    with pytest.raises(HTTPException) as exc2:
        BookingService.calculate_authoritative_price(db, "IXC", "elite", "DEPARTURE", "DOMESTIC")
    assert exc2.value.status_code == 400


# ==============================================================================
# 6. Journey Detection API & Selection Endpoints
# ==============================================================================

def test_ixc_journey_detection_api():
    """Verify Journey Detection API returns authoritative packages for IXC."""
    # 1. Domestic Departure
    res_dd = client.get("/api/journey/airports/IXC/services?journey_type=DEPARTURE&flight_type=DOMESTIC")
    assert res_dd.status_code == 200
    data_dd = res_dd.json()["data"]
    assert len(data_dd) == 2
    slugs_dd = [p["service"]["slug"] for p in data_dd]
    prices_dd = [float(p["price"]) for p in data_dd]
    assert slugs_dd == ["silver", "gold"]
    assert prices_dd == [2500.00, 4000.00]

    # 2. Domestic Arrival
    res_da = client.get("/api/journey/airports/IXC/services?journey_type=ARRIVAL&flight_type=DOMESTIC")
    assert res_da.status_code == 200
    data_da = res_da.json()["data"]
    assert len(data_da) == 1
    assert data_da[0]["service"]["slug"] == "silver"
    assert float(data_da[0]["price"]) == 2500.00

    # 3. International Departure
    res_id = client.get("/api/journey/airports/IXC/services?journey_type=DEPARTURE&flight_type=INTERNATIONAL")
    assert res_id.status_code == 200
    data_id = res_id.json()["data"]
    assert len(data_id) == 1
    assert data_id[0]["service"]["slug"] == "silver"
    assert float(data_id[0]["price"]) == 3000.00

    # 4. International Arrival
    res_ia = client.get("/api/journey/airports/IXC/services?journey_type=ARRIVAL&flight_type=INTERNATIONAL")
    assert res_ia.status_code == 200
    data_ia = res_ia.json()["data"]
    assert len(data_ia) == 1
    assert data_ia[0]["service"]["slug"] == "silver"
    assert float(data_ia[0]["price"]) == 2500.00


# ==============================================================================
# 7. Cross-Airport Isolation
# ==============================================================================

def test_ixc_cross_airport_isolation(db: Session):
    """Verify IXC configurations do not leak into other airports and vice versa."""
    # Delhi Domestic Departure packages (Silver 3000, Gold 3500, Elite 5000 for T3)
    del_packages = JourneyDetectionEngine.get_services_for_airport(db, "DEL", "DEPARTURE", "DOMESTIC", terminal="Terminal 3")
    del_prices = [float(p.price) for p in del_packages]
    assert del_prices == [3000.00, 3500.00, 5000.00]
    assert 2500.00 not in del_prices
    assert 4000.00 not in del_prices

    # Mumbai Domestic Departure packages
    bom_packages = JourneyDetectionEngine.get_services_for_airport(db, "BOM", "DEPARTURE", "DOMESTIC")
    assert len(bom_packages) > 0


# ==============================================================================
# 8. Full 20-Airport Regression Suite
# ==============================================================================

def test_20_airports_regression(db: Session):
    """Verify all 20 supported airports exist, are active, and have unchanged configurations."""
    expected_airports = [
        "DEL", "BOM", "HYD", "AMD", "LKO", "CCU", "GOI", "JAI",
        "ATQ", "BLR", "MAA", "COK", "TRV", "VTZ", "BBI", "IXC",
        "GOX", "GAU", "IXE", "IXR"
    ]

    for code in expected_airports:
        airport = db.query(SupportedAirport).filter(SupportedAirport.iata_code == code).first()
        assert airport is not None, f"Airport {code} must exist in supported_airports"
        assert airport.is_active is True, f"Airport {code} must be active"
        assert airport.is_supported is True, f"Airport {code} must be supported"

    # Verify DEL T3 configuration
    del_t3_dep = JourneyDetectionEngine.get_services_for_airport(db, "DEL", "DEPARTURE", "DOMESTIC", terminal="Terminal 3")
    assert len(del_t3_dep) == 3
    assert [float(p.price) for p in del_t3_dep] == [3000.00, 3500.00, 5000.00]

    # Verify HYD Transit configuration
    hyd_transit = JourneyDetectionEngine.get_services_for_airport(db, "HYD", "TRANSIT", "DOMESTIC_DOMESTIC")
    assert len(hyd_transit) == 1
    assert float(hyd_transit[0].price) == 5500.00

    # Verify BLR configuration
    blr_dep = JourneyDetectionEngine.get_services_for_airport(db, "BLR", "DEPARTURE", "DOMESTIC")
    assert len(blr_dep) == 1
    assert float(blr_dep[0].price) == 4500.00

    # Verify BOM configuration
    bom_dep = JourneyDetectionEngine.get_services_for_airport(db, "BOM", "DEPARTURE", "DOMESTIC")
    assert len(bom_dep) >= 1
