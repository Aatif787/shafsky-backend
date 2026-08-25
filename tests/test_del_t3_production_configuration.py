"""
Comprehensive Test Suite for Delhi Airport Terminal 3 (DEL-T3) Domestic Arrival & Departure Production Configuration.

Validates:
1. DEL Airport Record Invariance
2. DEL T3 Domestic Departure Pricing & Verbatim Inclusions (Silver 8, Gold 9, Elite 11)
3. DEL T3 Domestic Arrival Pricing & Verbatim Inclusions (Silver 6, Gold 7, Elite 7)
4. Exact Service Inclusion Text & Quirks (SEPERATE, CHECK IN, UPTO, AVAILABLITY, etc.)
5. Silver vs Gold vs Elite Inclusions Differences
6. Cross-Terminal Isolation (T3 vs T1 & T2)
7. Invariance of Other DEL Services (T1 & T2, International T3, Transit)
8. WhatsApp & Journey Detection Engine End-to-End Resolution
9. Cross-Airport Isolation (DEL vs BOM, HYD, BLR, etc.)
"""
import pytest
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.services.journey_engine import JourneyDetectionEngine
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.seeds.seed_journey_data import (
    DEL_T3_DOMESTIC_DEPARTURE_SILVER_FEATURES,
    DEL_T3_DOMESTIC_DEPARTURE_GOLD_FEATURES,
    DEL_T3_DOMESTIC_DEPARTURE_ELITE_FEATURES,
    DEL_T3_DOMESTIC_ARRIVAL_SILVER_FEATURES,
    DEL_T3_DOMESTIC_ARRIVAL_GOLD_FEATURES,
    DEL_T3_DOMESTIC_ARRIVAL_ELITE_FEATURES,
)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_del_airport_record(db: Session):
    """Verify Delhi airport record exists and is active/supported."""
    del_airport = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "DEL").first()
    assert del_airport is not None, "DEL airport must exist in supported_airports"
    assert del_airport.is_active is True, "DEL must be active"
    assert del_airport.is_supported is True, "DEL must be supported"
    assert del_airport.city == "New Delhi"
    assert del_airport.airport_name == "Indira Gandhi International Airport"


def test_del_t3_domestic_departure_packages(db: Session):
    """Verify DEL T3 Domestic Departure packages: Silver (3000), Gold (3500), Elite (5000)."""
    del_airport = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "DEL").first()
    assert del_airport is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == del_airport.id,
            AirportService.journey_type == "DEPARTURE",
            AirportService.flight_type == "DOMESTIC",
            AirportService.terminal == "Terminal 3",
            AirportService.is_available.is_(True),
        )
        .order_by(AirportService.display_priority)
        .all()
    )

    assert len(rows) == 3, f"Expected 3 T3 Domestic Departure packages, found {len(rows)}"

    pkg_map = {svc.slug: (aps, svc) for aps, svc in rows}

    # Silver (INR 3,000, 8 inclusions)
    assert "silver" in pkg_map
    aps_s, _ = pkg_map["silver"]
    assert float(aps_s.price) == 3000.00
    assert aps_s.display_priority == 1
    assert len(aps_s.features) == 8
    assert aps_s.features == DEL_T3_DOMESTIC_DEPARTURE_SILVER_FEATURES

    # Gold (INR 3,500, 9 inclusions)
    assert "gold" in pkg_map
    aps_g, _ = pkg_map["gold"]
    assert float(aps_g.price) == 3500.00
    assert aps_g.display_priority == 2
    assert len(aps_g.features) == 9
    assert aps_g.features == DEL_T3_DOMESTIC_DEPARTURE_GOLD_FEATURES

    # Elite (INR 5,000, 11 inclusions)
    assert "elite" in pkg_map
    aps_e, _ = pkg_map["elite"]
    assert float(aps_e.price) == 5000.00
    assert aps_e.display_priority == 3
    assert len(aps_e.features) == 11
    assert aps_e.features == DEL_T3_DOMESTIC_DEPARTURE_ELITE_FEATURES


def test_del_t3_domestic_arrival_packages(db: Session):
    """Verify DEL T3 Domestic Arrival packages: Silver (3000), Gold (3500), Elite (5000)."""
    del_airport = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "DEL").first()
    assert del_airport is not None

    rows = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == del_airport.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.flight_type == "DOMESTIC",
            AirportService.terminal == "Terminal 3",
            AirportService.is_available.is_(True),
        )
        .order_by(AirportService.display_priority)
        .all()
    )

    assert len(rows) == 3, f"Expected 3 T3 Domestic Arrival packages, found {len(rows)}"

    pkg_map = {svc.slug: (aps, svc) for aps, svc in rows}

    # Silver (INR 3,000, 6 inclusions)
    assert "silver" in pkg_map
    aps_s, _ = pkg_map["silver"]
    assert float(aps_s.price) == 3000.00
    assert aps_s.display_priority == 1
    assert len(aps_s.features) == 6
    assert aps_s.features == DEL_T3_DOMESTIC_ARRIVAL_SILVER_FEATURES

    # Gold (INR 3,500, 7 inclusions)
    assert "gold" in pkg_map
    aps_g, _ = pkg_map["gold"]
    assert float(aps_g.price) == 3500.00
    assert aps_g.display_priority == 2
    assert len(aps_g.features) == 7
    assert aps_g.features == DEL_T3_DOMESTIC_ARRIVAL_GOLD_FEATURES

    # Elite (INR 5,000, 7 inclusions - same supplied inclusions as Gold)
    assert "elite" in pkg_map
    aps_e, _ = pkg_map["elite"]
    assert float(aps_e.price) == 5000.00
    assert aps_e.display_priority == 3
    assert len(aps_e.features) == 7
    assert aps_e.features == DEL_T3_DOMESTIC_ARRIVAL_ELITE_FEATURES


def test_del_t3_exact_wording_and_quirks(db: Session):
    """Verify exact spelling quirks, punctuation, and terms."""
    del_airport = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "DEL").first()
    assert del_airport is not None

    dep_silver = db.query(AirportService).filter_by(
        airport_id=del_airport.id, journey_type="DEPARTURE", flight_type="DOMESTIC", terminal="Terminal 3", is_available=True
    ).join(Service).filter(Service.slug == "silver").first()

    dep_gold = db.query(AirportService).filter_by(
        airport_id=del_airport.id, journey_type="DEPARTURE", flight_type="DOMESTIC", terminal="Terminal 3", is_available=True
    ).join(Service).filter(Service.slug == "gold").first()

    dep_elite = db.query(AirportService).filter_by(
        airport_id=del_airport.id, journey_type="DEPARTURE", flight_type="DOMESTIC", terminal="Terminal 3", is_available=True
    ).join(Service).filter(Service.slug == "elite").first()

    arr_silver = db.query(AirportService).filter_by(
        airport_id=del_airport.id, journey_type="ARRIVAL", flight_type="DOMESTIC", terminal="Terminal 3", is_available=True
    ).join(Service).filter(Service.slug == "silver").first()

    arr_gold = db.query(AirportService).filter_by(
        airport_id=del_airport.id, journey_type="ARRIVAL", flight_type="DOMESTIC", terminal="Terminal 3", is_available=True
    ).join(Service).filter(Service.slug == "gold").first()

    arr_elite = db.query(AirportService).filter_by(
        airport_id=del_airport.id, journey_type="ARRIVAL", flight_type="DOMESTIC", terminal="Terminal 3", is_available=True
    ).join(Service).filter(Service.slug == "elite").first()

    # 1. Departure Quirks
    assert "ASSIST FROM SEPERATE ENTRY GATE." in dep_silver.features
    assert "ASSIST SEPARATE BAGGAGE CHECK IN AT AIRLINE COUNTER." in dep_silver.features
    assert "ASSIST GUEST UPTO BOARDING GATE." in dep_silver.features
    assert "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)." in dep_silver.features

    # 2. Departure Gold vs Silver (Lounge)
    assert "LOUNGE ACCESS FOR 2 HOURS." in dep_gold.features
    assert "LOUNGE ACCESS FOR 2 HOURS." not in dep_silver.features

    # 3. Departure Elite vs Gold (Rescheduling & Cancellation)
    assert "UNLIMITED RESCHEDULING (PRIOR 12 HOURS)" in dep_elite.features
    assert "CANCELLATION BENEFITS UPTO 12 HOURS OF SERVICE TIME." in dep_elite.features
    assert "UNLIMITED RESCHEDULING (PRIOR 12 HOURS)" not in dep_gold.features
    assert "CANCELLATION BENEFITS UPTO 12 HOURS OF SERVICE TIME." not in dep_gold.features

    # 4. Arrival Quirks
    assert "BUGGY SERVICE AVAILABLE. (AS PER THE AVAILABLITY)." in arr_silver.features
    assert "BUGGY SERVICE AVAILABLE. (AS PER THE AVAILABLITY)." in arr_gold.features
    assert "BUGGY SERVICE AVAILABLE. (AS PER THE AVAILABLITY)." in arr_elite.features

    # 5. Arrival Gold vs Silver (Lounge)
    assert "LOUNGE ACCESS FOR 2 HOURS." in arr_gold.features
    assert "LOUNGE ACCESS FOR 2 HOURS." not in arr_silver.features

    # 6. Arrival Elite (Same inclusions as Gold, no rescheduling/cancellation)
    assert arr_elite.features == arr_gold.features
    assert "UNLIMITED RESCHEDULING (PRIOR 12 HOURS)" not in arr_elite.features
    assert "CANCELLATION BENEFITS UPTO 12 HOURS OF SERVICE TIME." not in arr_elite.features


def test_del_terminal_isolation(db: Session):
    """Verify strict isolation between Terminal 3 and Terminal 1 & 2."""
    del_airport = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "DEL").first()
    assert del_airport is not None

    # T3 Departure: 3 packages
    t3_dep = JourneyDetectionEngine.get_services_for_airport(
        db, airport_iata="DEL", journey_type="DEPARTURE", flight_type="DOMESTIC", terminal="Terminal 3"
    )
    assert len(t3_dep) == 3
    for aps in t3_dep:
        assert aps.terminal == "Terminal 3"

    # T1 & T2 Departure: 2 packages
    t12_dep = JourneyDetectionEngine.get_services_for_airport(
        db, airport_iata="DEL", journey_type="DEPARTURE", flight_type="DOMESTIC", terminal="Terminal 1 & 2"
    )
    assert len(t12_dep) == 2
    for aps in t12_dep:
        assert aps.terminal == "Terminal 1 & 2"

    # T3 Arrival: 3 packages
    t3_arr = JourneyDetectionEngine.get_services_for_airport(
        db, airport_iata="DEL", journey_type="ARRIVAL", flight_type="DOMESTIC", terminal="Terminal 3"
    )
    assert len(t3_arr) == 3
    for aps in t3_arr:
        assert aps.terminal == "Terminal 3"

    # T1 & T2 Arrival: 2 packages
    t12_arr = JourneyDetectionEngine.get_services_for_airport(
        db, airport_iata="DEL", journey_type="ARRIVAL", flight_type="DOMESTIC", terminal="Terminal 1 & 2"
    )
    assert len(t12_arr) == 2
    for aps in t12_arr:
        assert aps.terminal == "Terminal 1 & 2"


def test_del_other_services_unmodified(db: Session):
    """Verify all other DEL services remain intact and active."""
    del_airport = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "DEL").first()
    assert del_airport is not None

    # 1. International Departure T3 (3 packages: 5500, 6500, 7000)
    intl_dep = (
        db.query(AirportService)
        .filter_by(
            airport_id=del_airport.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            is_available=True,
        )
        .order_by(AirportService.price)
        .all()
    )
    assert len(intl_dep) == 3
    assert [float(r.price) for r in intl_dep] == [5500.0, 6500.0, 7000.0]

    # 2. International Arrival T3 (3 packages: 5500, 6000, 7000)
    intl_arr = (
        db.query(AirportService)
        .filter_by(
            airport_id=del_airport.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            is_available=True,
        )
        .order_by(AirportService.price)
        .all()
    )
    assert len(intl_arr) == 3
    assert [float(r.price) for r in intl_arr] == [5500.0, 6000.0, 7000.0]

    # 3. Transit (4 routes: 5500, 7500, 7500, 9500)
    transit_rows = (
        db.query(AirportService)
        .filter_by(airport_id=del_airport.id, journey_type="TRANSIT", is_available=True)
        .order_by(AirportService.display_priority)
        .all()
    )
    assert len(transit_rows) == 4
    assert [float(r.price) for r in transit_rows] == [5500.0, 7500.0, 7500.0, 9500.0]


def test_whatsapp_del_t3_menu_rendering(db: Session):
    """Verify WhatsApp menu rendering for DEL T3."""
    del_airport = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "DEL").first()
    assert del_airport is not None

    # T3 Domestic Departure
    t3_dep_pkgs = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db, del_airport.id, "DEPARTURE", ["DOMESTIC", "ALL"], terminal="Terminal 3"
    )
    assert len(t3_dep_pkgs) == 3
    assert [float(aps.price) for aps, svc in t3_dep_pkgs] == [3000.0, 3500.0, 5000.0]

    # T3 Domestic Arrival
    t3_arr_pkgs = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db, del_airport.id, "ARRIVAL", ["DOMESTIC", "ALL"], terminal="Terminal 3"
    )
    assert len(t3_arr_pkgs) == 3
    assert [float(aps.price) for aps, svc in t3_arr_pkgs] == [3000.0, 3500.0, 5000.0]


def test_cross_airport_isolation_del_t3(db: Session):
    """Verify DEL T3 packages do not leak to other airports."""
    bom_airport = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "BOM").first()
    hyd_airport = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "HYD").first()

    assert bom_airport is not None
    assert hyd_airport is not None

    # BOM Departure Domestic packages
    bom_pkgs = JourneyDetectionEngine.get_services_for_airport(
        db, airport_iata="BOM", journey_type="DEPARTURE", flight_type="DOMESTIC"
    )
    assert len(bom_pkgs) == 3

    # HYD Transit packages
    hyd_transit = JourneyDetectionEngine.get_services_for_airport(
        db, airport_iata="HYD", journey_type="TRANSIT", flight_type="DOMESTIC_DOMESTIC"
    )
    assert len(hyd_transit) == 1
    assert float(hyd_transit[0].price) == 5500.0
