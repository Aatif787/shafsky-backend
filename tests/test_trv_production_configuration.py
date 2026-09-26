"""
Comprehensive Test Suite for Thiruvananthapuram / Trivandrum Airport (TRV) Complete Production Service & Pricing Configuration.

Validates:
1. TRV Airport Record Invariance (Single active & supported airport record)
2. TRV Active Packages Inventory (Exactly 7 Active Production Packages)
3. TRV Domestic Departure (Platinum INR 2420, Elite INR 4400)
4. TRV Domestic Arrival (Platinum INR 2420, Elite INR 4400)
5. TRV International Departure (Platinum INR 3300, Elite INR 4950)
6. TRV International Arrival (Platinum INR 2750; Elite NOT CONFIGURED)
7. TRV Transit Invariance (NOT CONFIGURED / Disabled, preserved without deletion)
8. Exact Service Inclusion Wording, Capitalization, Punctuation & ASSIST Action
9. Package Difference Validation across Tiers and Journeys
10. Authoritative Price Calculation & Client Anti-Tamper Protection
11. Journey Detection Engine & Package Selection API Resolution
12. WhatsApp Package Presentation & Inheritance Verification
13. Cross-Airport Isolation (TRV vs COK, CCU, BLR, DEL, BOM)
14. Full 20-Airport Regression Suite
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.services.journey_engine import JourneyDetectionEngine
from app.services.booking_service import BookingService
from app.integrations.whatsapp import copy as wa_copy
from app.seeds.airports.trv import (
    TRV_DOMESTIC_DEPARTURE_PLATINUM_FEATURES,
    TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES,
    TRV_DOMESTIC_ARRIVAL_PLATINUM_FEATURES,
    TRV_DOMESTIC_ARRIVAL_ELITE_FEATURES,
    TRV_INTERNATIONAL_DEPARTURE_PLATINUM_FEATURES,
    TRV_INTERNATIONAL_DEPARTURE_ELITE_FEATURES,
    TRV_INTERNATIONAL_ARRIVAL_PLATINUM_FEATURES,
    TRV_PRICING,
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
# 1. TRV Airport Record Invariance
# ==============================================================================

def test_trv_airport_record(db: Session):
    """Verify TRV airport record exists, is unique, active, supported, and correctly mapped."""
    airports = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "TRV").all()
    assert len(airports) == 1, "There must be exactly one TRV airport record"
    trv = airports[0]
    assert trv.is_active is True, "TRV must be active"
    assert trv.is_supported is True, "TRV must be supported"
    assert trv.city in ("Thiruvananthapuram", "Trivandrum"), f"Unexpected city: {trv.city}"
    assert "Thiruvananthapuram" in trv.airport_name or "Trivandrum" in trv.airport_name


# ==============================================================================
# 2. TRV Active Packages Inventory (Exactly 7 Active Packages)
# ==============================================================================

def test_trv_active_packages_inventory(db: Session):
    """Verify TRV has exactly 7 active production package records, with 0 transit and 0 intl arrival elite."""
    trv = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "TRV").first()
    assert trv is not None

    active_aps = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == trv.id,
            AirportService.is_available.is_(True)
        )
        .all()
    )
    assert len(active_aps) == 7, f"Expected exactly 7 active TRV packages, found {len(active_aps)}"

    # Check preserved legacy transit records remain disabled in DB
    transit_records = (
        db.query(AirportService)
        .filter(
            AirportService.airport_id == trv.id,
            AirportService.journey_type == "TRANSIT"
        )
        .all()
    )
    for rec in transit_records:
        assert rec.is_available is False, "Transit records must remain disabled (is_available=False)"


# ==============================================================================
# 3. TRV Domestic Departure Packages
# ==============================================================================

def test_trv_domestic_departure_packages(db: Session):
    """Verify TRV Domestic Departure: Platinum (INR 2420), Elite (INR 4400)."""
    trv = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "TRV").first()
    assert trv is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == trv.id,
            AirportService.journey_type == "DEPARTURE",
            AirportService.flight_type == "DOMESTIC",
            AirportService.is_available.is_(True)
        )
        .order_by(AirportService.display_priority)
        .all()
    )
    assert len(rows) == 2, f"Expected 2 active Dom Dep packages for TRV, found {len(rows)}"

    pkg_map = {svc.slug: (aps, svc) for aps, svc in rows}

    # Platinum (INR 2420, 8 inclusions)
    assert "platinum" in pkg_map
    aps_p, _ = pkg_map["platinum"]
    assert float(aps_p.price) == 2420.00
    assert len(aps_p.features) == 8
    assert aps_p.features == TRV_DOMESTIC_DEPARTURE_PLATINUM_FEATURES

    # Elite (INR 4400, 11 inclusions)
    assert "elite" in pkg_map
    aps_e, _ = pkg_map["elite"]
    assert float(aps_e.price) == 4400.00
    assert len(aps_e.features) == 11
    assert aps_e.features == TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES


# ==============================================================================
# 4. TRV Domestic Arrival Packages
# ==============================================================================

def test_trv_domestic_arrival_packages(db: Session):
    """Verify TRV Domestic Arrival: Platinum (INR 2420), Elite (INR 4400)."""
    trv = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "TRV").first()
    assert trv is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == trv.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.flight_type == "DOMESTIC",
            AirportService.is_available.is_(True)
        )
        .order_by(AirportService.display_priority)
        .all()
    )
    assert len(rows) == 2, f"Expected 2 active Dom Arr packages for TRV, found {len(rows)}"

    pkg_map = {svc.slug: (aps, svc) for aps, svc in rows}

    # Platinum (INR 2420, 6 inclusions)
    assert "platinum" in pkg_map
    aps_p, _ = pkg_map["platinum"]
    assert float(aps_p.price) == 2420.00
    assert len(aps_p.features) == 6
    assert aps_p.features == TRV_DOMESTIC_ARRIVAL_PLATINUM_FEATURES

    # Elite (INR 4400, 8 inclusions)
    assert "elite" in pkg_map
    aps_e, _ = pkg_map["elite"]
    assert float(aps_e.price) == 4400.00
    assert len(aps_e.features) == 8
    assert aps_e.features == TRV_DOMESTIC_ARRIVAL_ELITE_FEATURES


# ==============================================================================
# 5. TRV International Departure Packages
# ==============================================================================

def test_trv_international_departure_packages(db: Session):
    """Verify TRV International Departure: Platinum (INR 3300), Elite (INR 4950)."""
    trv = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "TRV").first()
    assert trv is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == trv.id,
            AirportService.journey_type == "DEPARTURE",
            AirportService.flight_type == "INTERNATIONAL",
            AirportService.is_available.is_(True)
        )
        .order_by(AirportService.display_priority)
        .all()
    )
    assert len(rows) == 2, f"Expected 2 active Intl Dep packages for TRV, found {len(rows)}"

    pkg_map = {svc.slug: (aps, svc) for aps, svc in rows}

    # Platinum (INR 3300, 10 inclusions)
    assert "platinum" in pkg_map
    aps_p, _ = pkg_map["platinum"]
    assert float(aps_p.price) == 3300.00
    assert len(aps_p.features) == 10
    assert aps_p.features == TRV_INTERNATIONAL_DEPARTURE_PLATINUM_FEATURES

    # Elite (INR 4950, 13 inclusions)
    assert "elite" in pkg_map
    aps_e, _ = pkg_map["elite"]
    assert float(aps_e.price) == 4950.00
    assert len(aps_e.features) == 13
    assert aps_e.features == TRV_INTERNATIONAL_DEPARTURE_ELITE_FEATURES


# ==============================================================================
# 6. TRV International Arrival Packages
# ==============================================================================

def test_trv_international_arrival_packages(db: Session):
    """Verify TRV International Arrival: Platinum (INR 2750); Elite NOT CONFIGURED."""
    trv = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "TRV").first()
    assert trv is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == trv.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.flight_type == "INTERNATIONAL",
            AirportService.is_available.is_(True)
        )
        .order_by(AirportService.display_priority)
        .all()
    )
    assert len(rows) == 1, f"Expected exactly 1 active Intl Arr package for TRV, found {len(rows)}"

    aps_p, svc_p = rows[0]
    assert svc_p.slug == "platinum"
    assert float(aps_p.price) == 2750.00
    assert len(aps_p.features) == 6
    assert aps_p.features == TRV_INTERNATIONAL_ARRIVAL_PLATINUM_FEATURES

    # Elite must NOT be configured
    elite_arr = (
        db.query(AirportService)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == trv.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.flight_type == "INTERNATIONAL",
            Service.slug == "elite",
            AirportService.is_available.is_(True)
        )
        .first()
    )
    assert elite_arr is None, "TRV International Arrival Elite must NOT be configured or active"


# ==============================================================================
# 7. TRV Transit Not Configured
# ==============================================================================

def test_trv_transit_not_configured(db: Session):
    """Verify TRV Transit is not configured and not bookable."""
    trv = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "TRV").first()
    assert trv is not None

    active_transit = (
        db.query(AirportService)
        .filter(
            AirportService.airport_id == trv.id,
            AirportService.journey_type == "TRANSIT",
            AirportService.is_available.is_(True)
        )
        .count()
    )
    assert active_transit == 0, f"Expected 0 active Transit packages for TRV, found {active_transit}"


# ==============================================================================
# 8. Exact Service Inclusion Wording, Capitalization, Punctuation & ASSIST Action
# ==============================================================================

def test_trv_exact_inclusion_texts_and_quirks():
    """Verify verbatim wording, spelling quirks, and ASSIST action."""
    # 1. CURB SIDE vs CURBSIDE
    assert "WELCOME GUEST FROM CURB SIDE AREA" in TRV_DOMESTIC_DEPARTURE_PLATINUM_FEATURES
    assert "WELCOME GUEST FROM CURBSIDE AREA" in TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES
    assert "WELCOME GUEST FROM CURB SIDE AREA" in TRV_INTERNATIONAL_DEPARTURE_PLATINUM_FEATURES
    assert "WELCOME GUEST FROM CURB SIDE AREA" in TRV_INTERNATIONAL_DEPARTURE_ELITE_FEATURES

    # 2. CHECK-IN vs CHECKIN
    assert "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER" in TRV_DOMESTIC_DEPARTURE_PLATINUM_FEATURES
    assert "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS" in TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES
    assert "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER" in TRV_INTERNATIONAL_DEPARTURE_PLATINUM_FEATURES
    assert "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER" in TRV_INTERNATIONAL_DEPARTURE_ELITE_FEATURES

    # 3. UPTO, HOUR'S, PRIORS
    assert "ASSIST GUEST UPTO BOARDING GATE" in TRV_DOMESTIC_DEPARTURE_PLATINUM_FEATURES
    assert "ASSIST GUEST TILL THE BOARDING GATE" in TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" in TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES
    assert "MINIMUM 6 HOURS PRIORS NOTICE REQUIRED FOR RESCHEDULING." in TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" in TRV_DOMESTIC_ARRIVAL_ELITE_FEATURES
    assert "MINIMUM 6 HOURS PRIORS NOTICE REQUIRED FOR RESCHEDULING." in TRV_DOMESTIC_ARRIVAL_ELITE_FEATURES
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" in TRV_INTERNATIONAL_DEPARTURE_ELITE_FEATURES
    assert "MINIMUM 6 HOURS PRIORS NOTICE REQUIRED FOR RESCHEDULING." in TRV_INTERNATIONAL_DEPARTURE_ELITE_FEATURES

    # 4. (02 HOURS)
    assert "LOUNGE SERVICE FACILITY AVAILABLE (02 HOURS)" in TRV_INTERNATIONAL_DEPARTURE_ELITE_FEATURES
    assert "LOUNGE SERVICE FACILITY AVAILABLE" in TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES

    # 5. POST IMMIGRATION. and POST CUSTOMS.
    assert "WELCOME GUEST FROM POST IMMIGRATION." in TRV_INTERNATIONAL_ARRIVAL_PLATINUM_FEATURES
    assert "ASSIST FROM POST CUSTOMS." in TRV_INTERNATIONAL_ARRIVAL_PLATINUM_FEATURES

    # 6. ASSIST action wording: NO "ASSISTANCE" or "ASSISTANT" in any TRV inclusion
    all_trv_features = (
        TRV_DOMESTIC_DEPARTURE_PLATINUM_FEATURES
        + TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES
        + TRV_DOMESTIC_ARRIVAL_PLATINUM_FEATURES
        + TRV_DOMESTIC_ARRIVAL_ELITE_FEATURES
        + TRV_INTERNATIONAL_DEPARTURE_PLATINUM_FEATURES
        + TRV_INTERNATIONAL_DEPARTURE_ELITE_FEATURES
        + TRV_INTERNATIONAL_ARRIVAL_PLATINUM_FEATURES
    )
    for feat in all_trv_features:
        assert "ASSISTANCE" not in feat.upper(), f"Found ASSISTANCE in '{feat}'"
        assert "ASSISTANT" not in feat.upper(), f"Found ASSISTANT in '{feat}'"
        assert feat.strip() != "", "Found empty feature bullet"


# ==============================================================================
# 9. Package Difference Validation
# ==============================================================================

def test_trv_package_differences():
    """Verify package difference rules."""
    # 1. Dom Dep Elite adds Lounge, Cancellation & Notice
    assert "LOUNGE SERVICE FACILITY AVAILABLE" in TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES
    assert "LOUNGE SERVICE FACILITY AVAILABLE" not in TRV_DOMESTIC_DEPARTURE_PLATINUM_FEATURES
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" in TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" not in TRV_DOMESTIC_DEPARTURE_PLATINUM_FEATURES

    # 2. Dom Arr Elite adds Cancellation & Notice to base 6 inclusions
    assert len(TRV_DOMESTIC_ARRIVAL_PLATINUM_FEATURES) == 6
    assert len(TRV_DOMESTIC_ARRIVAL_ELITE_FEATURES) == 8
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" in TRV_DOMESTIC_ARRIVAL_ELITE_FEATURES
    assert "MINIMUM 6 HOURS PRIORS NOTICE REQUIRED FOR RESCHEDULING." in TRV_DOMESTIC_ARRIVAL_ELITE_FEATURES
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" not in TRV_DOMESTIC_ARRIVAL_PLATINUM_FEATURES
    assert "MINIMUM 6 HOURS PRIORS NOTICE REQUIRED FOR RESCHEDULING." not in TRV_DOMESTIC_ARRIVAL_PLATINUM_FEATURES

    # 3. Intl Dep Elite adds 2-hour Lounge, Cancellation & Notice
    assert len(TRV_INTERNATIONAL_DEPARTURE_PLATINUM_FEATURES) == 10
    assert len(TRV_INTERNATIONAL_DEPARTURE_ELITE_FEATURES) == 13
    assert "LOUNGE SERVICE FACILITY AVAILABLE (02 HOURS)" in TRV_INTERNATIONAL_DEPARTURE_ELITE_FEATURES
    assert "LOUNGE SERVICE FACILITY AVAILABLE (02 HOURS)" not in TRV_INTERNATIONAL_DEPARTURE_PLATINUM_FEATURES

    # 4. Intl Arr only Platinum exists
    assert len(TRV_INTERNATIONAL_ARRIVAL_PLATINUM_FEATURES) == 6


# ==============================================================================
# 10. Authoritative Price Calculation & Client Anti-Tamper Protection
# ==============================================================================

def test_trv_authoritative_pricing_calculation(db: Session):
    """Verify BookingService.calculate_authoritative_price returns exact TRV prices."""
    # Domestic Departure
    assert BookingService.calculate_authoritative_price(db, "TRV", "platinum", "DEPARTURE", "DOMESTIC") == 2420.00
    assert BookingService.calculate_authoritative_price(db, "TRV", "elite", "DEPARTURE", "DOMESTIC") == 4400.00

    # Domestic Arrival
    assert BookingService.calculate_authoritative_price(db, "TRV", "platinum", "ARRIVAL", "DOMESTIC") == 2420.00
    assert BookingService.calculate_authoritative_price(db, "TRV", "elite", "ARRIVAL", "DOMESTIC") == 4400.00

    # International Departure
    assert BookingService.calculate_authoritative_price(db, "TRV", "platinum", "DEPARTURE", "INTERNATIONAL") == 3300.00
    assert BookingService.calculate_authoritative_price(db, "TRV", "elite", "DEPARTURE", "INTERNATIONAL") == 4950.00

    # International Arrival
    assert BookingService.calculate_authoritative_price(db, "TRV", "platinum", "ARRIVAL", "INTERNATIONAL") == 2750.00

    # Multi-pax multiplier check
    assert BookingService.calculate_authoritative_price(db, "TRV", "platinum", "DEPARTURE", "DOMESTIC", pax_count=2) == 4840.00
    assert BookingService.calculate_authoritative_price(db, "TRV", "elite", "ARRIVAL", "DOMESTIC", pax_count=3) == 13200.00

    # Rejection of unconfigured packages (Anti-tamper)
    with pytest.raises(HTTPException) as exc_info1:
        BookingService.calculate_authoritative_price(db, "TRV", "elite", "ARRIVAL", "INTERNATIONAL")
    assert exc_info1.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info2:
        BookingService.calculate_authoritative_price(db, "TRV", "silver", "DEPARTURE", "DOMESTIC")
    assert exc_info2.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info3:
        BookingService.calculate_authoritative_price(db, "TRV", "gold", "ARRIVAL", "DOMESTIC")
    assert exc_info3.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info4:
        BookingService.calculate_authoritative_price(db, "TRV", "meet_greet", "TRANSIT", "DOMESTIC")
    assert exc_info4.value.status_code == 400


def test_trv_client_price_tamper_attempt(db: Session):
    """Verify client price tampering via estimate-price endpoint is governed by backend-authoritative calculation."""
    # Attempt tampering with total_amount: 1, 0, 999999
    for bogus_amount in [1, 0, 999999]:
        payload = {
            "airport_code": "TRV",
            "journey_type": "DEPARTURE",
            "package_id": "platinum",
            "pax_adults": 1,
            "total_amount": bogus_amount
        }
        res = client.post("/api/bookings/estimate-price", json=payload)
        assert res.status_code == 200
        data = res.json()["data"]
        # Expected authoritative total amount is 2420.0, regardless of client bogus amount
        assert data["total_amount"] == 2420.0
        assert data["base_price"] == 2420.0


# ==============================================================================
# 11. Journey Detection Engine & Package Selection API Resolution
# ==============================================================================

def test_trv_journey_detection_api():
    """Verify Journey Detection API returns authoritative packages for TRV."""
    # 1. Domestic Departure
    res_dd = client.get("/api/journey/airports/TRV/services?journey_type=DEPARTURE&flight_type=DOMESTIC")
    assert res_dd.status_code == 200
    data_dd = res_dd.json()["data"]
    assert len(data_dd) == 2
    assert [p["service"]["slug"] for p in data_dd] == ["platinum", "elite"]
    assert [float(p["price"]) for p in data_dd] == [2420.00, 4400.00]

    # 2. Domestic Arrival
    res_da = client.get("/api/journey/airports/TRV/services?journey_type=ARRIVAL&flight_type=DOMESTIC")
    assert res_da.status_code == 200
    data_da = res_da.json()["data"]
    assert len(data_da) == 2
    assert [p["service"]["slug"] for p in data_da] == ["platinum", "elite"]
    assert [float(p["price"]) for p in data_da] == [2420.00, 4400.00]

    # 3. International Departure
    res_id = client.get("/api/journey/airports/TRV/services?journey_type=DEPARTURE&flight_type=INTERNATIONAL")
    assert res_id.status_code == 200
    data_id = res_id.json()["data"]
    assert len(data_id) == 2
    assert [p["service"]["slug"] for p in data_id] == ["platinum", "elite"]
    assert [float(p["price"]) for p in data_id] == [3300.00, 4950.00]

    # 4. International Arrival (Only Platinum)
    res_ia = client.get("/api/journey/airports/TRV/services?journey_type=ARRIVAL&flight_type=INTERNATIONAL")
    assert res_ia.status_code == 200
    data_ia = res_ia.json()["data"]
    assert len(data_ia) == 1
    assert data_ia[0]["service"]["slug"] == "platinum"
    assert float(data_ia[0]["price"]) == 2750.00

    # 5. Transit (Not configured)
    res_tr = client.get("/api/journey/airports/TRV/services?journey_type=TRANSIT&flight_type=DOMESTIC")
    assert res_tr.status_code == 200
    assert len(res_tr.json()["data"]) == 0


def test_trv_journey_engine_resolution(db: Session):
    """Verify JourneyDetectionEngine.detect_journey resolves TRV journeys."""
    res_dep = JourneyDetectionEngine.detect_journey(
        db=db,
        departure_code="TRV",
        arrival_code="DEL",
        journey_type="DEPARTURE",
        service_date="2026-10-01",
        service_time="10:00",
        flight_type="DOMESTIC",
    )
    assert res_dep.primary_airport is not None
    assert res_dep.primary_airport.iata_code == "TRV"
    assert len(res_dep.available_services) == 2
    slugs = [s.slug for s in res_dep.available_services]
    assert "platinum" in slugs
    assert "elite" in slugs


# ==============================================================================
# 12. WhatsApp Package Presentation Verification
# ==============================================================================

def test_trv_whatsapp_compact_presentation():
    """Verify WhatsApp package presentation logic handles TRV packages correctly."""
    mock_dom_dep = [
        {"id": "1", "name": "Platinum Service", "price": 2420, "features": TRV_DOMESTIC_DEPARTURE_PLATINUM_FEATURES},
        {"id": "2", "name": "Elite Service", "price": 4400, "features": TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES},
    ]
    inheritance = wa_copy.compute_package_inheritance(mock_dom_dep)
    assert len(inheritance) == 2
    assert inheritance[0]["is_base"] is True
    assert inheritance[0]["count"] == 8

    # Elite additions over Platinum
    assert inheritance[1]["is_base"] is False
    assert inheritance[1]["prev_tier_name"] == "Platinum"
    assert len(inheritance[1]["additions"]) > 0

    # Level 2 detail text for Elite preserves exact inclusion wording
    elite_detail = wa_copy.selected_package_details_text(mock_dom_dep[1], mock_dom_dep)
    assert "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME" in elite_detail
    assert "MINIMUM 6 HOURS PRIORS NOTICE REQUIRED FOR RESCHEDULING." in elite_detail


# ==============================================================================
# 13. Cross-Airport Isolation
# ==============================================================================

def test_trv_cross_airport_isolation(db: Session):
    """Verify TRV pricing does not leak to other airports and other airport services cannot be booked at TRV."""
    # 1. Kochi (COK) pricing remains distinct (Domestic Departure Silver is 3500.00, not 2420.00)
    cok_price = BookingService.calculate_authoritative_price(
        db=db,
        airport_code="COK",
        service_tier_or_slug="silver",
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        pax_count=1,
    )
    assert cok_price == 3500.00
    assert cok_price != 2420.00

    # 2. Check Kolkata (CCU) Domestic Departure Silver remains 2200.00, distinct from TRV 2420.00
    ccu_price = BookingService.calculate_authoritative_price(
        db=db,
        airport_code="CCU",
        service_tier_or_slug="silver",
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
        pax_count=1,
    )
    assert ccu_price == 2200.00
    assert ccu_price != 2420.00

    # 3. Check BOM Transit is rejected at TRV
    with pytest.raises(HTTPException):
        BookingService.calculate_authoritative_price(
            db=db,
            airport_code="TRV",
            service_tier_or_slug="silver",
            journey_type="TRANSIT",
            flight_type="DOMESTIC",
            pax_count=1,
        )


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
        "BOM": (12, 16),
        "CCU": (5, 7),
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
        "TRV": 7,
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
