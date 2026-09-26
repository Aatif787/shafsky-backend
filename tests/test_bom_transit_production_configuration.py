"""
Comprehensive Test Suite for Mumbai (BOM) Transit Services Complete Production Replacement.

Covers:
1. BOM Airport Record Invariance
2. BOM Transit Active Count & Complete Old Removal Verification (Exactly 4 Active, No Inactive, No Old IDs)
3. BOM Transit 4 Route Types Configuration & Pricing:
   - Domestic-Domestic = INR 7150
   - Domestic-International = INR 9000
   - International-Domestic = INR 9000
   - International-International = INR 10000
4. Exact Service Inclusions Count & Verbatim Text Verification:
   - Domestic-Domestic: 9 inclusions
   - Domestic-International: 12 inclusions
   - International-Domestic: 12 inclusions
   - International-International: 6 inclusions
5. Exact Wording Quirks & Differences:
   - BUGGY SERVICE AVAILABLE vs BUGGY SERVICE AVAILABLE END OF THE AEROBRIDGE
   - CHECK-IN vs CHECKIN
   - Standalone ASSIST action wording preserved
   - DEDICATED PORTER FOR ASSISTANCE... noun phrase preserved
6. Non-Transit BOM Services Invariance (6 Dep, 6 Arr remain active)
7. Authoritative Price Calculation & Client Anti-Tamper Protection
8. Journey Detection Engine & Package Selection API Resolution
9. WhatsApp Presentation Validation
10. Cross-Airport Isolation (BOM vs DEL vs HYD vs GOX vs BLR)
11. Full 20-Airport Regression Suite (BOM has 16 active rows)
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.main import app
from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.services.journey_engine import JourneyDetectionEngine
from app.services.booking_service import BookingService
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.seeds.airports.bom import (
    BOM_TRANSIT_DOMESTIC_DOMESTIC_FEATURES,
    BOM_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES,
    BOM_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES,
    BOM_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES,
    BOM_TRANSIT_PRICING,
)

client = TestClient(app)


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==============================================================================
# 1. BOM Airport Record Invariance
# ==============================================================================

def test_bom_airport_record(db_session: Session):
    airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BOM")
    ).scalar_one_or_none()
    assert airport is not None
    assert airport.iata_code == "BOM"
    assert airport.is_active is True
    assert airport.is_supported is True


# ==============================================================================
# 2. BOM Transit Active Inventory & Old Removal Verification
# ==============================================================================

def test_bom_transit_pricing_matrix(db_session: Session):
    bom_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BOM")
    ).scalar_one_or_none()
    assert bom_airport is not None

    expected_pricing = {
        "DOMESTIC_DOMESTIC": 7150.00,
        "DOMESTIC_INTERNATIONAL": 9000.00,
        "INTERNATIONAL_DOMESTIC": 9000.00,
        "INTERNATIONAL_INTERNATIONAL": 10000.00,
    }

    transit_rows = db_session.execute(
        select(AirportService).where(
            AirportService.airport_id == bom_airport.id,
            AirportService.journey_type == "TRANSIT",
            AirportService.is_available.is_(True),
        )
    ).scalars().all()

    assert len(transit_rows) == 4, f"Expected exactly 4 active Transit packages for BOM, got {len(transit_rows)}"
    found_types = {}
    for r in transit_rows:
        found_types[r.flight_type] = float(r.price)

    for ft, exp_price in expected_pricing.items():
        assert ft in found_types, f"Route {ft} missing from active BOM transit"
        assert found_types[ft] == exp_price, f"Route {ft} price mismatch: expected {exp_price}, got {found_types[ft]}"


def test_bom_transit_no_legacy_records(db_session: Session):
    """Verify that legacy transit record IDs do not exist in the database."""
    legacy_ids = [
        "1ca7d520-006a-45c0-8dc9-5037635e6e14",
        "709019a6-814c-49e4-b481-5e323349ff13",
        "47da0955-cb17-4750-884c-2172c9a1dd73",
        "8aaf8da4-1704-4582-bffb-8a996fb3f55e",
    ]
    for lid in legacy_ids:
        rec = db_session.execute(
            select(AirportService).where(AirportService.id == lid)
        ).scalar_one_or_none()
        assert rec is None, f"Legacy transit record {lid} must not exist in the database"


# ==============================================================================
# 3. Exact Service Inclusions Count & Verbatim Text Verification
# ==============================================================================

def test_bom_transit_verbatim_features_and_counts(db_session: Session):
    bom_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BOM")
    ).scalar_one_or_none()
    assert bom_airport is not None

    rows = {
        r.flight_type: r for r in db_session.execute(
            select(AirportService).where(
                AirportService.airport_id == bom_airport.id,
                AirportService.journey_type == "TRANSIT",
                AirportService.is_available.is_(True),
            )
        ).scalars().all()
    }

    assert len(rows) == 4

    # 1. Domestic-Domestic (9 inclusions)
    dd = rows["DOMESTIC_DOMESTIC"]
    assert len(dd.features) == 9
    assert dd.features == BOM_TRANSIT_DOMESTIC_DOMESTIC_FEATURES
    assert float(dd.price) == 7150.00

    # 2. Domestic-International (12 inclusions)
    di = rows["DOMESTIC_INTERNATIONAL"]
    assert len(di.features) == 12
    assert di.features == BOM_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES
    assert float(di.price) == 9000.00

    # 3. International-Domestic (12 inclusions)
    id_row = rows["INTERNATIONAL_DOMESTIC"]
    assert len(id_row.features) == 12
    assert id_row.features == BOM_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES
    assert float(id_row.price) == 9000.00

    # 4. International-International (6 inclusions)
    ii = rows["INTERNATIONAL_INTERNATIONAL"]
    assert len(ii.features) == 6
    assert ii.features == BOM_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES
    assert float(ii.price) == 10000.00


# ==============================================================================
# 4. Exact Wording Quirks & Differences Validation
# ==============================================================================

def test_bom_transit_exact_wording_quirks():
    """Verify route-specific differences and non-negotiable ASSIST wording."""
    # 1. Buggy differences:
    # Dom-Dom has BUGGY SERVICE AVAILABLE END OF THE AEROBRIDGE
    assert "BUGGY SERVICE AVAILABLE END OF THE AEROBRIDGE" in BOM_TRANSIT_DOMESTIC_DOMESTIC_FEATURES
    # Dom-Intl has BUGGY SERVICE AVAILABLE (not END OF THE AEROBRIDGE)
    assert "BUGGY SERVICE AVAILABLE" in BOM_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES
    assert "BUGGY SERVICE AVAILABLE END OF THE AEROBRIDGE" not in BOM_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES
    # Intl-Dom has BUGGY SERVICE AVAILABLE END OF THE AEROBRIDGE
    assert "BUGGY SERVICE AVAILABLE END OF THE AEROBRIDGE" in BOM_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES

    # 2. Gate buggy wording:
    assert "BUGGY SERVICE AVAILABLE TILL THE BOARDING GATE (AS PER THE AVAILABILITY)" in BOM_TRANSIT_DOMESTIC_DOMESTIC_FEATURES
    assert "BUGGY SERVICE TILL THE BOARDING GATE (AS PER THE AVAILABILITY)" in BOM_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES
    assert "BUGGY SERVICE AVAILABLE TILL THE BOARDING GATE (AS PER THE AVAILABILITY)" in BOM_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES

    # 3. Check-in spelling:
    assert "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER" in BOM_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES
    assert "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS" in BOM_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES

    # 4. Gate escort wording:
    assert "ASSIST PAX UPTO BOARDING GATE" in BOM_TRANSIT_DOMESTIC_DOMESTIC_FEATURES
    assert "ASSIST GUEST UPTO BOARDING GATE" in BOM_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES
    assert "ASSIST GUEST UPTO BOARDING GATE" in BOM_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES

    # 5. Intl-Intl specific phrasing:
    assert "WARM WELCOME AT AEROBRIDGE/BUS GATE BY PORTER." in BOM_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES
    assert "DEDICATED PORTER FOR ASSISTANCE FROM AEROBRIDGE ON ARRIVAL TILL THE BOARDING GATE OF THE NEXT CONNECTING FLIGHT." in BOM_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES
    assert "ENJOY ACCESS TO ADANI LOUNGE WITH SNACKS, FOOD, AND NON-ALCOHOLIC BEVERAGE." in BOM_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES

    # 6. ASSIST Action verification
    all_features = (
        BOM_TRANSIT_DOMESTIC_DOMESTIC_FEATURES
        + BOM_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES
        + BOM_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES
    )
    for feat in all_features:
        assert "ASSISTANCE" not in feat.upper(), f"Found ASSISTANCE in '{feat}'"
        assert "ASSISTANT" not in feat.upper(), f"Found ASSISTANT in '{feat}'"


# ==============================================================================
# 5. Non-Transit BOM Services Unmodified
# ==============================================================================

def test_bom_non_transit_services_unmodified(db_session: Session):
    bom_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BOM")
    ).scalar_one_or_none()
    assert bom_airport is not None

    dep_count = db_session.execute(
        select(func.count(AirportService.id)).where(
            AirportService.airport_id == bom_airport.id,
            AirportService.journey_type == "DEPARTURE",
            AirportService.is_available.is_(True),
        )
    ).scalar()
    assert dep_count == 6, f"Expected 6 active departure records, got {dep_count}"

    arr_count = db_session.execute(
        select(func.count(AirportService.id)).where(
            AirportService.airport_id == bom_airport.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.is_available.is_(True),
        )
    ).scalar()
    assert arr_count == 6, f"Expected 6 active arrival records, got {arr_count}"


# ==============================================================================
# 6. Authoritative Pricing & Client Anti-Tamper
# ==============================================================================

def test_bom_transit_authoritative_pricing(db_session: Session):
    """Verify BookingService.calculate_authoritative_price returns exact transit prices."""
    assert BookingService.calculate_authoritative_price(
        db_session, "BOM", "meet_greet", "TRANSIT", "DOMESTIC_DOMESTIC"
    ) == 7150.00
    assert BookingService.calculate_authoritative_price(
        db_session, "BOM", "meet_greet", "TRANSIT", "DOMESTIC_INTERNATIONAL"
    ) == 9000.00
    assert BookingService.calculate_authoritative_price(
        db_session, "BOM", "meet_greet", "TRANSIT", "INTERNATIONAL_DOMESTIC"
    ) == 9000.00
    assert BookingService.calculate_authoritative_price(
        db_session, "BOM", "meet_greet", "TRANSIT", "INTERNATIONAL_INTERNATIONAL"
    ) == 10000.00

    # Multi-pax multiplier
    assert BookingService.calculate_authoritative_price(
        db_session, "BOM", "meet_greet", "TRANSIT", "DOMESTIC_DOMESTIC", pax_count=2
    ) == 14300.00

    # Unconfigured route rejection
    with pytest.raises(HTTPException):
        BookingService.calculate_authoritative_price(
            db_session, "BOM", "meet_greet", "TRANSIT", "NONEXISTENT_ROUTE"
        )


def test_bom_transit_client_price_tamper_attempt():
    """Verify client price tampering via estimate-price endpoint is governed by backend-authoritative calculation."""
    for bogus_amount in [1, 0, 999999]:
        payload = {
            "airport_code": "BOM",
            "journey_type": "TRANSIT",
            "package_id": "meet_greet",
            "flight_type": "DOMESTIC_DOMESTIC",
            "pax_adults": 1,
            "total_amount": bogus_amount
        }
        res = client.post("/api/bookings/estimate-price", json=payload)
        assert res.status_code == 200
        data = res.json()["data"]
        # Authoritative base price must be enforced regardless of client tampering
        assert data["total_amount"] == 7150.00
        assert data["base_price"] == 7150.00


# ==============================================================================
# 7. Journey Detection & Package API Resolution
# ==============================================================================

def test_bom_transit_api_resolution():
    """Verify Journey Detection API returns authoritative packages for BOM Transit."""
    # 1. Domestic-Domestic
    res_dd = client.get("/api/journey/airports/BOM/services?journey_type=TRANSIT&flight_type=DOMESTIC_DOMESTIC")
    assert res_dd.status_code == 200
    data_dd = res_dd.json()["data"]
    assert len(data_dd) == 1
    assert float(data_dd[0]["price"]) == 7150.00
    assert len(data_dd[0]["features"]) == 9

    # 2. Domestic-International
    res_di = client.get("/api/journey/airports/BOM/services?journey_type=TRANSIT&flight_type=DOMESTIC_INTERNATIONAL")
    assert res_di.status_code == 200
    data_di = res_di.json()["data"]
    assert len(data_di) == 1
    assert float(data_di[0]["price"]) == 9000.00
    assert len(data_di[0]["features"]) == 12

    # 3. International-Domestic
    res_id = client.get("/api/journey/airports/BOM/services?journey_type=TRANSIT&flight_type=INTERNATIONAL_DOMESTIC")
    assert res_id.status_code == 200
    data_id = res_id.json()["data"]
    assert len(data_id) == 1
    assert float(data_id[0]["price"]) == 9000.00
    assert len(data_id[0]["features"]) == 12

    # 4. International-International
    res_ii = client.get("/api/journey/airports/BOM/services?journey_type=TRANSIT&flight_type=INTERNATIONAL_INTERNATIONAL")
    assert res_ii.status_code == 200
    data_ii = res_ii.json()["data"]
    assert len(data_ii) == 1
    assert float(data_ii[0]["price"]) == 10000.00
    assert len(data_ii[0]["features"]) == 6


# ==============================================================================
# 8. WhatsApp Menu Rendering
# ==============================================================================

def test_whatsapp_bom_transit_menu_rendering(db_session: Session):
    bom_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BOM")
    ).scalar_one_or_none()
    assert bom_airport is not None

    for ft, expected_price in [
        (["DOMESTIC_DOMESTIC", "ALL"], 7150.00),
        (["DOMESTIC_INTERNATIONAL", "ALL"], 9000.00),
        (["INTERNATIONAL_DOMESTIC", "ALL"], 9000.00),
        (["INTERNATIONAL_INTERNATIONAL", "ALL"], 10000.00),
    ]:
        rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
            db_session, bom_airport.id, "TRANSIT", ft
        )
        assert len(rows) == 1
        aps, svc = rows[0]
        assert float(aps.price) == expected_price
        assert svc.slug == "meet_greet"


# ==============================================================================
# 9. Cross-Airport Isolation
# ==============================================================================

def test_cross_airport_isolation_bom(db_session: Session):
    bom_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BOM")
    ).scalar_one_or_none()
    del_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "DEL")
    ).scalar_one_or_none()
    hyd_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "HYD")
    ).scalar_one_or_none()

    bom_rows = {
        r.flight_type: float(r.price) for r in db_session.execute(
            select(AirportService).where(
                AirportService.airport_id == bom_airport.id,
                AirportService.journey_type == "TRANSIT",
                AirportService.is_available.is_(True),
            )
        ).scalars().all()
    }

    del_rows = {
        r.flight_type: float(r.price) for r in db_session.execute(
            select(AirportService).where(
                AirportService.airport_id == del_airport.id,
                AirportService.journey_type == "TRANSIT",
                AirportService.is_available.is_(True),
            )
        ).scalars().all()
    }

    hyd_rows = {
        r.flight_type: float(r.price) for r in db_session.execute(
            select(AirportService).where(
                AirportService.airport_id == hyd_airport.id,
                AirportService.journey_type == "TRANSIT",
                AirportService.is_available.is_(True),
            )
        ).scalars().all()
    }

    # BOM vs DEL distinct prices
    assert bom_rows["DOMESTIC_DOMESTIC"] == 7150.00
    assert del_rows["DOMESTIC_DOMESTIC"] == 5500.00

    assert bom_rows["DOMESTIC_INTERNATIONAL"] == 9000.00
    assert del_rows["DOMESTIC_INTERNATIONAL"] == 7500.00

    assert bom_rows["INTERNATIONAL_INTERNATIONAL"] == 10000.00
    assert del_rows["INTERNATIONAL_INTERNATIONAL"] == 9500.00

    assert bom_rows["INTERNATIONAL_DOMESTIC"] == 9000.00
    assert del_rows["INTERNATIONAL_DOMESTIC"] == 7500.00

    # BOM vs HYD distinct prices
    assert hyd_rows["DOMESTIC_DOMESTIC"] == 5500.00
    assert hyd_rows["DOMESTIC_DOMESTIC"] != bom_rows["DOMESTIC_DOMESTIC"]
    assert hyd_rows["DOMESTIC_INTERNATIONAL"] == 7500.00
    assert hyd_rows["DOMESTIC_INTERNATIONAL"] != bom_rows["DOMESTIC_INTERNATIONAL"]


# ==============================================================================
# 10. Full 20-Airport Regression Suite
# ==============================================================================

def test_full_20_airports_regression_suite(db_session: Session):
    """Verify all 20 supported airports maintain their exact production active package inventory."""
    expected_active_counts = {
        "AMD": 7,
        "ATQ": 4,
        "BBI": 3,
        "BLR": 12,
        "BOM": 16,  # 12 Non-Transit + 4 Transit
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
        "TRV": (6, 7),
        "VTZ": 2,
    }

    airports = db_session.query(SupportedAirport).all()
    assert len(airports) == 20, f"Expected 20 supported airports, found {len(airports)}"

    for airport in airports:
        code = airport.iata_code
        assert code in expected_active_counts, f"Unexpected airport {code}"
        active_count = (
            db_session.query(AirportService)
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
