"""
Comprehensive Test Suite for Goa Mopa Airport (GOX) Complete Production Service, Pricing & Transit Configuration.

Validates:
1. GOX Airport Record Invariance
2. GOX Active Packages Inventory (Exactly 14 Active Mappings)
3. GOX Domestic Departure (Silver INR 2500, Gold INR 3000, Elite INR 4500)
4. GOX Domestic Arrival (Silver INR 2500, Gold INR 3000, Elite INR 4500)
5. GOX International Departure (Silver INR 4500, Gold INR 5000, Elite INR 7000)
6. GOX International Arrival (Silver INR 2500, Elite INR 4500; Gold NOT CONFIGURED)
7. GOX Transit (Dom-Dom INR 4500, Dom-Intl INR 6500, Intl-Intl INR 7000; Intl-Dom NOT CONFIGURED)
8. Exact Service Inclusion Wording, Capitalization, Punctuation & ASSIST Action
9. Package Difference Validation across Tiers and Journeys
10. Unconfigured / Unsupported Combination Rejection
11. Authoritative Price Calculation & Client Anti-Tamper Protection
12. Journey Detection Engine & Package Selection API Resolution
13. WhatsApp Compact Inheritance & Detailed Package Presentation
14. Complete Isolation between GOX (Goa Mopa) and GOI (Goa Dabolim)
15. Full Regression Suite across all other 19 supported airports
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.main import app
from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.services.journey_engine import JourneyDetectionEngine
from app.services.booking_service import BookingService
from app.integrations.whatsapp import copy as wa_copy
from app.seeds.seed_journey_data import (
    GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES,
    GOX_DOMESTIC_DEPARTURE_GOLD_FEATURES,
    GOX_DOMESTIC_DEPARTURE_ELITE_FEATURES,
    GOX_DOMESTIC_ARRIVAL_SILVER_FEATURES,
    GOX_DOMESTIC_ARRIVAL_GOLD_FEATURES,
    GOX_DOMESTIC_ARRIVAL_ELITE_FEATURES,
    GOX_INTERNATIONAL_DEPARTURE_SILVER_FEATURES,
    GOX_INTERNATIONAL_DEPARTURE_GOLD_FEATURES,
    GOX_INTERNATIONAL_DEPARTURE_ELITE_FEATURES,
    GOX_INTERNATIONAL_ARRIVAL_SILVER_FEATURES,
    GOX_INTERNATIONAL_ARRIVAL_ELITE_FEATURES,
    GOX_TRANSIT_DOMESTIC_DOMESTIC_FEATURES,
    GOX_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES,
    GOX_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES,
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
# 1. GOX Airport Record Invariance
# ==============================================================================

def test_gox_airport_record(db: Session):
    """Verify GOX airport record exists, is unique, active, supported, and correctly named."""
    airports = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "GOX").all()
    assert len(airports) == 1, "There must be exactly one GOX airport record"
    gox = airports[0]
    assert gox.is_active is True, "GOX must be active"
    assert gox.is_supported is True, "GOX must be supported"
    assert gox.city == "Goa Mopa", f"Expected city 'Goa Mopa', got '{gox.city}'"
    assert "Manohar International Airport" in gox.airport_name, f"Unexpected airport name: {gox.airport_name}"


# ==============================================================================
# 2. GOX Active Packages Inventory (Exactly 14 Active Packages)
# ==============================================================================

def test_gox_active_packages_inventory(db: Session):
    """Verify GOX has exactly 14 active production package records."""
    gox = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "GOX").first()
    assert gox is not None

    active_aps = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == gox.id,
            AirportService.is_available.is_(True)
        )
        .all()
    )
    assert len(active_aps) == 14, f"Expected exactly 14 active GOX packages, found {len(active_aps)}"


# ==============================================================================
# 3. GOX Domestic Departure Packages
# ==============================================================================

def test_gox_domestic_departure_packages(db: Session):
    """Verify GOX Domestic Departure: Silver (₹2,500), Gold (₹3,000), Elite (₹4,500)."""
    gox = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "GOX").first()
    assert gox is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == gox.id,
            AirportService.journey_type == "DEPARTURE",
            AirportService.flight_type == "DOMESTIC",
            AirportService.is_available.is_(True)
        )
        .order_by(AirportService.display_priority)
        .all()
    )
    assert len(rows) == 3, f"Expected 3 active Dom Dep packages for GOX, found {len(rows)}"

    pkg_map = {svc.slug: (aps, svc) for aps, svc in rows}

    # Silver (INR 2500, 9 inclusions)
    assert "silver" in pkg_map
    aps_s, _ = pkg_map["silver"]
    assert float(aps_s.price) == 2500.00
    assert aps_s.display_priority == 1
    assert len(aps_s.features) == 9
    assert aps_s.features == GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES

    # Gold (INR 3000, 10 inclusions)
    assert "gold" in pkg_map
    aps_g, _ = pkg_map["gold"]
    assert float(aps_g.price) == 3000.00
    assert aps_g.display_priority == 2
    assert len(aps_g.features) == 10
    assert aps_g.features == GOX_DOMESTIC_DEPARTURE_GOLD_FEATURES

    # Elite (INR 4500, 12 inclusions)
    assert "elite" in pkg_map
    aps_e, _ = pkg_map["elite"]
    assert float(aps_e.price) == 4500.00
    assert aps_e.display_priority == 3
    assert len(aps_e.features) == 12
    assert aps_e.features == GOX_DOMESTIC_DEPARTURE_ELITE_FEATURES


# ==============================================================================
# 4. GOX Domestic Arrival Packages
# ==============================================================================

def test_gox_domestic_arrival_packages(db: Session):
    """Verify GOX Domestic Arrival: Silver (₹2,500), Gold (₹3,000), Elite (₹4,500)."""
    gox = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "GOX").first()
    assert gox is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == gox.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.flight_type == "DOMESTIC",
            AirportService.is_available.is_(True)
        )
        .order_by(AirportService.display_priority)
        .all()
    )
    assert len(rows) == 3, f"Expected 3 active Dom Arr packages for GOX, found {len(rows)}"

    pkg_map = {svc.slug: (aps, svc) for aps, svc in rows}

    # Silver (INR 2500, 6 inclusions)
    assert "silver" in pkg_map
    aps_s, _ = pkg_map["silver"]
    assert float(aps_s.price) == 2500.00
    assert len(aps_s.features) == 6
    assert aps_s.features == GOX_DOMESTIC_ARRIVAL_SILVER_FEATURES

    # Gold (INR 3000, 6 inclusions - same base as Silver)
    assert "gold" in pkg_map
    aps_g, _ = pkg_map["gold"]
    assert float(aps_g.price) == 3000.00
    assert len(aps_g.features) == 6
    assert aps_g.features == GOX_DOMESTIC_ARRIVAL_GOLD_FEATURES
    assert aps_g.features == aps_s.features

    # Elite (INR 4500, 8 inclusions - 6 base + cancellation + rescheduling)
    assert "elite" in pkg_map
    aps_e, _ = pkg_map["elite"]
    assert float(aps_e.price) == 4500.00
    assert len(aps_e.features) == 8
    assert aps_e.features == GOX_DOMESTIC_ARRIVAL_ELITE_FEATURES
    assert aps_e.features[:6] == aps_s.features


# ==============================================================================
# 5. GOX International Departure Packages
# ==============================================================================

def test_gox_international_departure_packages(db: Session):
    """Verify GOX International Departure: Silver (₹4,500), Gold (₹5,000), Elite (₹7,000)."""
    gox = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "GOX").first()
    assert gox is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == gox.id,
            AirportService.journey_type == "DEPARTURE",
            AirportService.flight_type == "INTERNATIONAL",
            AirportService.is_available.is_(True)
        )
        .order_by(AirportService.display_priority)
        .all()
    )
    assert len(rows) == 3, f"Expected 3 active Intl Dep packages for GOX, found {len(rows)}"

    pkg_map = {svc.slug: (aps, svc) for aps, svc in rows}

    # Silver (INR 4500, 10 inclusions)
    assert "silver" in pkg_map
    aps_s, _ = pkg_map["silver"]
    assert float(aps_s.price) == 4500.00
    assert len(aps_s.features) == 10
    assert aps_s.features == GOX_INTERNATIONAL_DEPARTURE_SILVER_FEATURES

    # Gold (INR 5000, 11 inclusions)
    assert "gold" in pkg_map
    aps_g, _ = pkg_map["gold"]
    assert float(aps_g.price) == 5000.00
    assert len(aps_g.features) == 11
    assert aps_g.features == GOX_INTERNATIONAL_DEPARTURE_GOLD_FEATURES

    # Elite (INR 7000, 13 inclusions)
    assert "elite" in pkg_map
    aps_e, _ = pkg_map["elite"]
    assert float(aps_e.price) == 7000.00
    assert len(aps_e.features) == 13
    assert aps_e.features == GOX_INTERNATIONAL_DEPARTURE_ELITE_FEATURES


# ==============================================================================
# 6. GOX International Arrival Packages (Silver & Elite ONLY, Gold UNCONFIGURED)
# ==============================================================================

def test_gox_international_arrival_packages(db: Session):
    """Verify GOX International Arrival: Silver (₹2,500), Elite (₹4,500); Gold NOT CONFIGURED."""
    gox = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "GOX").first()
    assert gox is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == gox.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.flight_type == "INTERNATIONAL",
            AirportService.is_available.is_(True)
        )
        .order_by(AirportService.display_priority)
        .all()
    )
    assert len(rows) == 2, f"Expected exactly 2 active Intl Arr packages for GOX, found {len(rows)}"

    pkg_map = {svc.slug: (aps, svc) for aps, svc in rows}

    # Gold must NOT be active
    assert "gold" not in pkg_map, "Gold must NOT be configured for GOX International Arrival"

    # Silver (INR 2500, 3 inclusions)
    assert "silver" in pkg_map
    aps_s, _ = pkg_map["silver"]
    assert float(aps_s.price) == 2500.00
    assert len(aps_s.features) == 3
    assert aps_s.features == GOX_INTERNATIONAL_ARRIVAL_SILVER_FEATURES

    # Elite (INR 4500, 5 inclusions)
    assert "elite" in pkg_map
    aps_e, _ = pkg_map["elite"]
    assert float(aps_e.price) == 4500.00
    assert len(aps_e.features) == 5
    assert aps_e.features == GOX_INTERNATIONAL_ARRIVAL_ELITE_FEATURES
    assert aps_e.features[:3] == aps_s.features


# ==============================================================================
# 7. GOX Transit Packages (Dom-Dom, Dom-Intl, Intl-Intl; Intl-Dom UNCONFIGURED)
# ==============================================================================

def test_gox_transit_packages(db: Session):
    """Verify GOX Transit routes and pricing; Intl-Dom must NOT be configured."""
    gox = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "GOX").first()
    assert gox is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == gox.id,
            AirportService.journey_type == "TRANSIT",
            AirportService.is_available.is_(True)
        )
        .order_by(AirportService.display_priority)
        .all()
    )
    assert len(rows) == 3, f"Expected exactly 3 active Transit packages for GOX, found {len(rows)}"

    transit_map = {aps.flight_type: (aps, svc) for aps, svc in rows}

    # 1. Domestic -> Domestic: INR 4500 (7 inclusions)
    assert "DOMESTIC_DOMESTIC" in transit_map
    aps_dd, svc_dd = transit_map["DOMESTIC_DOMESTIC"]
    assert float(aps_dd.price) == 4500.00
    assert aps_dd.currency == "INR"
    assert len(aps_dd.features) == 7
    assert aps_dd.features == GOX_TRANSIT_DOMESTIC_DOMESTIC_FEATURES

    # 2. Domestic -> International: INR 6500 (10 inclusions)
    assert "DOMESTIC_INTERNATIONAL" in transit_map
    aps_di, svc_di = transit_map["DOMESTIC_INTERNATIONAL"]
    assert float(aps_di.price) == 6500.00
    assert aps_di.currency == "INR"
    assert len(aps_di.features) == 10
    assert aps_di.features == GOX_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES

    # 3. International -> International: INR 7000 (7 inclusions)
    assert "INTERNATIONAL_INTERNATIONAL" in transit_map
    aps_ii, svc_ii = transit_map["INTERNATIONAL_INTERNATIONAL"]
    assert float(aps_ii.price) == 7000.00
    assert aps_ii.currency == "INR"
    assert len(aps_ii.features) == 7
    assert aps_ii.features == GOX_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES

    # 4. International -> Domestic: NOT CONFIGURED
    assert "INTERNATIONAL_DOMESTIC" not in transit_map, "INTERNATIONAL_DOMESTIC transit must NOT be configured"


# ==============================================================================
# 8. Exact Service Inclusion Wording, Capitalization, Punctuation & ASSIST Action
# ==============================================================================

def test_gox_exact_inclusion_texts_and_quirks():
    """Verify non-negotiable verbatim wording, spelling quirks, and ASSIST action."""
    # 1. Dom Dep Silver vs Gold spelling quirks
    assert "WELCOME GUEST FROM CURB SIDE AREA" in GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES
    assert "WELCOME GUEST FROM CURBSIDE AREA" in GOX_DOMESTIC_DEPARTURE_GOLD_FEATURES
    assert "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER" in GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES
    assert "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS" in GOX_DOMESTIC_DEPARTURE_GOLD_FEATURES
    assert "BUGGY SERVICE AVAILABLE TILL THE BOARDING GATE (SHARING BASIS)" in GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES
    assert "BUGGY SERVICE AVAILABLE TILL THE BOARDING GATE" in GOX_DOMESTIC_DEPARTURE_GOLD_FEATURES
    assert "ASSIST GUEST UPTO BOARDING GATE" in GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES
    assert "ASSIST GUEST TILL THE BOARDING GATE" in GOX_DOMESTIC_DEPARTURE_GOLD_FEATURES

    # 2. Elite cancellation benefits spelling quirk: "HOUR’S"
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" in GOX_DOMESTIC_DEPARTURE_ELITE_FEATURES
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" in GOX_DOMESTIC_ARRIVAL_ELITE_FEATURES
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" in GOX_INTERNATIONAL_DEPARTURE_ELITE_FEATURES
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME." in GOX_INTERNATIONAL_ARRIVAL_ELITE_FEATURES

    # 3. Wheelchair casing: THROUGH AIRLINES vs Through Airlines
    assert "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)" in GOX_TRANSIT_DOMESTIC_DOMESTIC_FEATURES
    assert "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)" in GOX_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES
    assert "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)" in GOX_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES
    assert "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)" in GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES
    assert "WHEELCHAIR SERVICE AVAILABLE WITH DEDICATED STAFF(Through Airlines)" in GOX_DOMESTIC_ARRIVAL_SILVER_FEATURES

    # 4. S.H.A. transit area vs security hold area
    assert "ASSIST IN S.H.A.(TRANSIT AREA)" in GOX_TRANSIT_DOMESTIC_DOMESTIC_FEATURES
    assert "ASSIST IN S.H.A.(TRANSIT AREA)" in GOX_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES
    assert "ASSIST IN S.H.A.(SECURITY HOLD AREA)" in GOX_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES
    assert "ASSIST IN S.H.A.(SECURITY HOLD AREA)" in GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES

    # 5. ASSIST action wording: NO "ASSISTANCE" or "ASSISTANT" in any GOX inclusion
    all_gox_features = (
        GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES
        + GOX_DOMESTIC_DEPARTURE_GOLD_FEATURES
        + GOX_DOMESTIC_DEPARTURE_ELITE_FEATURES
        + GOX_DOMESTIC_ARRIVAL_SILVER_FEATURES
        + GOX_DOMESTIC_ARRIVAL_GOLD_FEATURES
        + GOX_DOMESTIC_ARRIVAL_ELITE_FEATURES
        + GOX_INTERNATIONAL_DEPARTURE_SILVER_FEATURES
        + GOX_INTERNATIONAL_DEPARTURE_GOLD_FEATURES
        + GOX_INTERNATIONAL_DEPARTURE_ELITE_FEATURES
        + GOX_INTERNATIONAL_ARRIVAL_SILVER_FEATURES
        + GOX_INTERNATIONAL_ARRIVAL_ELITE_FEATURES
        + GOX_TRANSIT_DOMESTIC_DOMESTIC_FEATURES
        + GOX_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES
        + GOX_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES
    )
    for feat in all_gox_features:
        assert "ASSISTANCE" not in feat.upper(), f"Found ASSISTANCE in '{feat}'"
        assert "ASSISTANT" not in feat.upper(), f"Found ASSISTANT in '{feat}'"


# ==============================================================================
# 9. Package Difference Validation
# ==============================================================================

def test_gox_package_differences():
    """Verify package difference rules."""
    # 1. Dom Dep Gold adds Lounge
    assert "LOUNGE SERVICE FACILITY AVAILABLE" in GOX_DOMESTIC_DEPARTURE_GOLD_FEATURES
    assert "LOUNGE SERVICE FACILITY AVAILABLE" not in GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES

    # 2. Dom Dep Elite adds Cancellation & Rescheduling
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" in GOX_DOMESTIC_DEPARTURE_ELITE_FEATURES
    assert "UNLIMITED RESCHEDULING" in GOX_DOMESTIC_DEPARTURE_ELITE_FEATURES
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" not in GOX_DOMESTIC_DEPARTURE_GOLD_FEATURES

    # 3. Intl Dep Gold adds 2-hour Lounge
    assert "LOUNGE SERVICE FACILITY AVAILABLE (02 HOURS)" in GOX_INTERNATIONAL_DEPARTURE_GOLD_FEATURES
    assert "LOUNGE SERVICE FACILITY AVAILABLE (02 HOURS)" not in GOX_INTERNATIONAL_DEPARTURE_SILVER_FEATURES

    # 4. Intl Arr Elite adds Rescheduling & Cancellation
    assert "UNLIMITED RESCHEDULING" in GOX_INTERNATIONAL_ARRIVAL_ELITE_FEATURES
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME." in GOX_INTERNATIONAL_ARRIVAL_ELITE_FEATURES
    assert "UNLIMITED RESCHEDULING" not in GOX_INTERNATIONAL_ARRIVAL_SILVER_FEATURES


# ==============================================================================
# 10. Authoritative Price Calculation & Client Anti-Tamper Protection
# ==============================================================================

def test_gox_authoritative_pricing_calculation(db: Session):
    """Verify BookingService.calculate_authoritative_price returns exact GOX prices."""
    # Domestic Departure
    assert BookingService.calculate_authoritative_price(db, "GOX", "silver", "DEPARTURE", "DOMESTIC") == 2500.00
    assert BookingService.calculate_authoritative_price(db, "GOX", "gold", "DEPARTURE", "DOMESTIC") == 3000.00
    assert BookingService.calculate_authoritative_price(db, "GOX", "elite", "DEPARTURE", "DOMESTIC") == 4500.00

    # Domestic Arrival
    assert BookingService.calculate_authoritative_price(db, "GOX", "silver", "ARRIVAL", "DOMESTIC") == 2500.00
    assert BookingService.calculate_authoritative_price(db, "GOX", "gold", "ARRIVAL", "DOMESTIC") == 3000.00
    assert BookingService.calculate_authoritative_price(db, "GOX", "elite", "ARRIVAL", "DOMESTIC") == 4500.00

    # International Departure
    assert BookingService.calculate_authoritative_price(db, "GOX", "silver", "DEPARTURE", "INTERNATIONAL") == 4500.00
    assert BookingService.calculate_authoritative_price(db, "GOX", "gold", "DEPARTURE", "INTERNATIONAL") == 5000.00
    assert BookingService.calculate_authoritative_price(db, "GOX", "elite", "DEPARTURE", "INTERNATIONAL") == 7000.00

    # International Arrival
    assert BookingService.calculate_authoritative_price(db, "GOX", "silver", "ARRIVAL", "INTERNATIONAL") == 2500.00
    assert BookingService.calculate_authoritative_price(db, "GOX", "elite", "ARRIVAL", "INTERNATIONAL") == 4500.00

    # Transit
    assert BookingService.calculate_authoritative_price(db, "GOX", "meet_greet", "TRANSIT", "DOMESTIC_DOMESTIC") == 4500.00
    assert BookingService.calculate_authoritative_price(db, "GOX", "meet_greet", "TRANSIT", "DOMESTIC_INTERNATIONAL") == 6500.00
    assert BookingService.calculate_authoritative_price(db, "GOX", "meet_greet", "TRANSIT", "INTERNATIONAL_INTERNATIONAL") == 7000.00

    # Multi-pax multiplier check
    assert BookingService.calculate_authoritative_price(db, "GOX", "silver", "DEPARTURE", "DOMESTIC", pax_count=3) == 7500.00

    # Rejection of unconfigured packages (Anti-tamper)
    with pytest.raises(HTTPException) as exc_info1:
        BookingService.calculate_authoritative_price(db, "GOX", "gold", "ARRIVAL", "INTERNATIONAL")
    assert exc_info1.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info2:
        BookingService.calculate_authoritative_price(db, "GOX", "meet_greet", "TRANSIT", "INTERNATIONAL_DOMESTIC")
    assert exc_info2.value.status_code == 400


# ==============================================================================
# 11. Journey Detection Engine & Package Selection API Resolution
# ==============================================================================

def test_gox_journey_detection_api():
    """Verify Journey Detection API returns authoritative packages for GOX."""
    # 1. Domestic Departure
    res_dd = client.get("/api/journey/airports/GOX/services?journey_type=DEPARTURE&flight_type=DOMESTIC")
    assert res_dd.status_code == 200
    data_dd = res_dd.json()["data"]
    assert len(data_dd) == 3
    assert [p["service"]["slug"] for p in data_dd] == ["silver", "gold", "elite"]
    assert [float(p["price"]) for p in data_dd] == [2500.00, 3000.00, 4500.00]

    # 2. Domestic Arrival
    res_da = client.get("/api/journey/airports/GOX/services?journey_type=ARRIVAL&flight_type=DOMESTIC")
    assert res_da.status_code == 200
    data_da = res_da.json()["data"]
    assert len(data_da) == 3
    assert [p["service"]["slug"] for p in data_da] == ["silver", "gold", "elite"]
    assert [float(p["price"]) for p in data_da] == [2500.00, 3000.00, 4500.00]

    # 3. International Departure
    res_id = client.get("/api/journey/airports/GOX/services?journey_type=DEPARTURE&flight_type=INTERNATIONAL")
    assert res_id.status_code == 200
    data_id = res_id.json()["data"]
    assert len(data_id) == 3
    assert [p["service"]["slug"] for p in data_id] == ["silver", "gold", "elite"]
    assert [float(p["price"]) for p in data_id] == [4500.00, 5000.00, 7000.00]

    # 4. International Arrival (Only Silver & Elite)
    res_ia = client.get("/api/journey/airports/GOX/services?journey_type=ARRIVAL&flight_type=INTERNATIONAL")
    assert res_ia.status_code == 200
    data_ia = res_ia.json()["data"]
    assert len(data_ia) == 2
    assert [p["service"]["slug"] for p in data_ia] == ["silver", "elite"]
    assert [float(p["price"]) for p in data_ia] == [2500.00, 4500.00]


# ==============================================================================
# 12. Complete Isolation between GOX (Goa Mopa) and GOI (Goa Dabolim)
# ==============================================================================

def test_gox_goi_complete_isolation(db: Session):
    """Verify GOX and GOI remain strictly isolated with independent configurations."""
    goi = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "GOI").first()
    gox = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "GOX").first()
    assert goi is not None
    assert gox is not None
    assert goi.id != gox.id

    # GOI active package count must remain 5
    goi_count = (
        db.query(AirportService)
        .filter(AirportService.airport_id == goi.id, AirportService.is_available.is_(True))
        .count()
    )
    assert goi_count == 5, f"GOI active package count must be 5, found {goi_count}"

    # GOX active package count must be 14
    gox_count = (
        db.query(AirportService)
        .filter(AirportService.airport_id == gox.id, AirportService.is_available.is_(True))
        .count()
    )
    assert gox_count == 14, f"GOX active package count must be 14, found {gox_count}"

    # GOI Domestic Departure Gold is ₹4,000, while GOX Domestic Departure Gold is ₹3,000
    p_goi_gold = BookingService.calculate_authoritative_price(db, "GOI", "gold", "DEPARTURE", "DOMESTIC")
    p_gox_gold = BookingService.calculate_authoritative_price(db, "GOX", "gold", "DEPARTURE", "DOMESTIC")
    assert p_goi_gold == 4000.00
    assert p_gox_gold == 3000.00
    assert p_goi_gold != p_gox_gold

    # GOI International Departure Silver is ₹2,000, while GOX International Departure Silver is ₹4,500
    p_goi_intl = BookingService.calculate_authoritative_price(db, "GOI", "silver", "DEPARTURE", "INTERNATIONAL")
    p_gox_intl = BookingService.calculate_authoritative_price(db, "GOX", "silver", "DEPARTURE", "INTERNATIONAL")
    assert p_goi_intl == 2000.00
    assert p_gox_intl == 4500.00
    assert p_goi_intl != p_gox_intl

    # Elite is unconfigured at GOI
    with pytest.raises(HTTPException):
        BookingService.calculate_authoritative_price(db, "GOI", "elite", "DEPARTURE", "DOMESTIC")


# ==============================================================================
# 13. WhatsApp Compact Package Presentation Verification
# ==============================================================================

def test_gox_whatsapp_compact_presentation():
    """Verify WhatsApp package presentation logic handles GOX packages correctly."""
    # Test Domestic Departure services
    mock_dom_dep = [
        {"id": "1", "name": "Silver Service", "price": 2500, "features": GOX_DOMESTIC_DEPARTURE_SILVER_FEATURES},
        {"id": "2", "name": "Gold Service", "price": 3000, "features": GOX_DOMESTIC_DEPARTURE_GOLD_FEATURES},
        {"id": "3", "name": "Elite Service", "price": 4500, "features": GOX_DOMESTIC_DEPARTURE_ELITE_FEATURES},
    ]
    inheritance = wa_copy.compute_package_inheritance(mock_dom_dep)
    assert len(inheritance) == 3
    assert inheritance[0]["is_base"] is True
    assert inheritance[0]["count"] == 9

    # Gold additions over Silver
    assert inheritance[1]["is_base"] is False
    assert inheritance[1]["prev_tier_name"] == "Silver"
    assert len(inheritance[1]["additions"]) > 0

    # Elite additions over Gold
    assert inheritance[2]["is_base"] is False
    assert inheritance[2]["prev_tier_name"] == "Gold"
    assert len(inheritance[2]["additions"]) > 0

    # Level 2 detail text for Elite preserves exact inclusion wording
    elite_detail = wa_copy.selected_package_details_text(mock_dom_dep[2], mock_dom_dep)
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" in elite_detail


# ==============================================================================
# 14. Full 20-Airport Regression Suite
# ==============================================================================

def test_full_20_airports_regression_suite(db: Session):
    """Verify all 20 supported airports maintain their exact production active package inventory."""
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
        if isinstance(expected, tuple):
            assert active_count in expected, f"Airport {code} active service count mismatch: expected one of {expected}, got {active_count}"
        else:
            assert active_count == expected, f"Airport {code} active service count mismatch: expected {expected}, got {active_count}"
