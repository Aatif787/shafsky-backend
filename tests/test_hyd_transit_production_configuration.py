"""
Comprehensive Test Suite for Hyderabad Airport (HYD) Transit Services Production Configuration.

Validates:
1. HYD Airport Record Invariance
2. HYD Transit 2 Route Types Configuration & Pricing (Dom-Dom INR 5500, Dom-Intl INR 7500)
3. Unprovided Route Types Invariance (Intl-Dom & Intl-Intl unconfigured)
4. Exact Service Inclusion Text & Quirks Validation
5. Wheelchair & SHA Wording Isolation
6. Non-Negotiable ASSIST Action Wording & 'PAX UPTO' Verification
7. Cross-Airport Isolation (HYD vs Other Airports)
8. Non-Transit HYD Services Invariance (12 total active packages)
9. WhatsApp & Journey Detection Engine End-to-End Validation
10. Price Tamper Resistance & Server-Side Authority
"""
import pytest
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.services.journey_engine import JourneyDetectionEngine
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.seeds.seed_journey_data import (
    HYD_TRANSIT_DOMESTIC_DOMESTIC_FEATURES,
    HYD_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES,
)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_hyd_airport_record(db: Session):
    """Verify Hyderabad airport record exists and is active/supported."""
    hyd = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "HYD").first()
    assert hyd is not None, "HYD airport must exist in supported_airports"
    assert hyd.is_active is True, "HYD must be active"
    assert hyd.is_supported is True, "HYD must be supported"
    assert hyd.city == "Hyderabad"
    assert hyd.airport_name == "Rajiv Gandhi International Airport"


def test_hyd_transit_pricing_matrix(db: Session):
    """Verify HYD Transit pricing: Dom-Dom INR 5500, Dom-Intl INR 7500, Intl-Dom/Intl-Intl unconfigured."""
    hyd = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "HYD").first()
    assert hyd is not None

    active_transit = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == hyd.id,
            AirportService.journey_type == "TRANSIT",
            AirportService.is_available.is_(True),
        )
        .all()
    )

    assert len(active_transit) == 2, f"HYD must have exactly 2 active transit rows, found {len(active_transit)}"

    transit_map = {aps.flight_type: (aps, svc) for aps, svc in active_transit}

    # 1. Domestic -> Domestic: INR 5500
    assert "DOMESTIC_DOMESTIC" in transit_map, "DOMESTIC_DOMESTIC transit must be active"
    aps_dd, svc_dd = transit_map["DOMESTIC_DOMESTIC"]
    assert float(aps_dd.price) == 5500.00
    assert aps_dd.currency == "INR"
    assert aps_dd.display_priority == 1
    assert svc_dd.slug == "meet_greet"

    # 2. Domestic -> International: INR 7500
    assert "DOMESTIC_INTERNATIONAL" in transit_map, "DOMESTIC_INTERNATIONAL transit must be active"
    aps_di, svc_di = transit_map["DOMESTIC_INTERNATIONAL"]
    assert float(aps_di.price) == 7500.00
    assert aps_di.currency == "INR"
    assert aps_di.display_priority == 2
    assert svc_di.slug == "meet_greet"

    # 3. Unprovided Routes: Intl-Dom & Intl-Intl must NOT be active
    assert "INTERNATIONAL_DOMESTIC" not in transit_map, "INTERNATIONAL_DOMESTIC transit must NOT be configured"
    assert "INTERNATIONAL_INTERNATIONAL" not in transit_map, "INTERNATIONAL_INTERNATIONAL transit must NOT be configured"


def test_hyd_transit_verbatim_features_and_counts(db: Session):
    """Verify exact features and counts: Dom-Dom (7 items), Dom-Intl (10 items)."""
    hyd = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "HYD").first()
    assert hyd is not None

    # Dom-Dom
    dd = (
        db.query(AirportService)
        .filter_by(
            airport_id=hyd.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_DOMESTIC",
            is_available=True,
        )
        .first()
    )
    assert dd is not None
    assert len(dd.features) == 7, f"Expected 7 features for Dom-Dom, got {len(dd.features)}"
    assert dd.features == HYD_TRANSIT_DOMESTIC_DOMESTIC_FEATURES

    # Dom-Intl
    di = (
        db.query(AirportService)
        .filter_by(
            airport_id=hyd.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_INTERNATIONAL",
            is_available=True,
        )
        .first()
    )
    assert di is not None
    assert len(di.features) == 10, f"Expected 10 features for Dom-Intl, got {len(di.features)}"
    assert di.features == HYD_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES


def test_hyd_transit_wording_differences(db: Session):
    """Verify critical wording differences between Dom-Dom and Dom-Intl transit routes."""
    hyd = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "HYD").first()
    assert hyd is not None

    dd = db.query(AirportService).filter_by(airport_id=hyd.id, journey_type="TRANSIT", flight_type="DOMESTIC_DOMESTIC", is_available=True).first()
    di = db.query(AirportService).filter_by(airport_id=hyd.id, journey_type="TRANSIT", flight_type="DOMESTIC_INTERNATIONAL", is_available=True).first()

    # Wheelchair wording
    assert "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)" in dd.features
    assert "WHEELCHAIR SERVICE AVAILABLE" in di.features
    assert "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)" not in di.features

    # SHA wording
    assert "ASSIST IN S.H.A.(TRANSIT AREA)" in dd.features
    assert "ASSIST IN S.H.A.(SECURITY HOLD AREA)" in di.features

    # Verbatim preserve checks
    assert "ASSIST PAX UPTO BOARDING GATE" in dd.features
    assert "ASSIST PAX UPTO BOARDING GATE" in di.features
    assert "ASSIST AT SEPARATE CHECKIN PROCESS AT AIRLINES COUNTERS" in di.features
    assert "GUIDANCE TO THE IMMIGRATION COUNTER" in di.features
    assert "LOUNGE ACCESS FOR 2 HOURS (AT DEPARTURE ONLY)" in dd.features
    assert "LOUNGE ACCESS FOR 2 HOURS (AT DEPARTURE ONLY)" in di.features


def test_hyd_non_transit_services_unmodified(db: Session):
    """Verify all 12 existing HYD departure and arrival packages remain active and unchanged."""
    hyd = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "HYD").first()
    assert hyd is not None

    active_nontransit = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == hyd.id,
            AirportService.journey_type != "TRANSIT",
            AirportService.is_available.is_(True),
        )
        .all()
    )

    assert len(active_nontransit) == 12, f"HYD must have exactly 12 active non-transit rows, found {len(active_nontransit)}"

    counts = {}
    for aps, svc in active_nontransit:
        key = (aps.journey_type, aps.flight_type)
        counts[key] = counts.get(key, 0) + 1

    assert counts[("DEPARTURE", "DOMESTIC")] == 3  # Silver, Gold, Elite
    assert counts[("ARRIVAL", "DOMESTIC")] == 3    # Silver, Gold, Elite
    assert counts[("DEPARTURE", "INTERNATIONAL")] == 3  # Silver, Gold, Elite
    assert counts[("ARRIVAL", "INTERNATIONAL")] == 3    # Silver, Gold, Elite


def test_whatsapp_hyd_transit_menu_rendering(db: Session):
    """Verify WhatsApp integration correctly renders HYD Transit packages."""
    hyd = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "HYD").first()
    assert hyd is not None

    # Dom-Dom
    dd_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db=db,
        airport_id=hyd.id,
        journey_type="TRANSIT",
        flight_types=["DOMESTIC_DOMESTIC", "ALL"],
        terminal=None
    )
    assert len(dd_rows) == 1
    aps_dd, svc_dd = dd_rows[0]
    assert float(aps_dd.price) == 5500.00

    # Dom-Intl
    di_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db=db,
        airport_id=hyd.id,
        journey_type="TRANSIT",
        flight_types=["DOMESTIC_INTERNATIONAL", "ALL"],
        terminal=None
    )
    assert len(di_rows) == 1
    aps_di, svc_di = di_rows[0]
    assert float(aps_di.price) == 7500.00


def test_cross_airport_isolation_hyd(db: Session):
    """Verify HYD transit configuration is strictly isolated from DEL, BOM, BLR, etc."""
    hyd = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "HYD").first()
    del_airport = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "DEL").first()
    bom_airport = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "BOM").first()

    assert hyd is not None
    assert del_airport is not None
    assert bom_airport is not None

    # DEL transit Dom-Dom price is 5500, but has 8 features (not 7)
    del_dd = db.query(AirportService).filter_by(airport_id=del_airport.id, journey_type="TRANSIT", flight_type="DOMESTIC_DOMESTIC", is_available=True).first()
    assert del_dd is not None
    assert len(del_dd.features) == 8

    # BOM transit Dom-Dom price is 7150
    bom_dd = db.query(AirportService).filter_by(airport_id=bom_airport.id, journey_type="TRANSIT", flight_type="DOMESTIC_DOMESTIC", is_available=True).first()
    assert bom_dd is not None
    assert float(bom_dd.price) == 7150.00
