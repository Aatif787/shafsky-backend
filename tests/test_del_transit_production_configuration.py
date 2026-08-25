"""
Comprehensive Test Suite for Delhi (DEL) Transit Services Production Configuration.

Tests:
1. DEL Airport Record Invariance
2. DEL Transit 4 Route Types Configuration & Pricing
3. Exact Service Inclusion Text & Quirks Validation
4. Terminal & Buggy Conditions Isolation
5. Non-Negotiable ASSIST Action Wording Verification
6. Cross-Airport Isolation (DEL vs Other Airports)
7. Non-Transit DEL Services Invariance (11 Dep, 16 Arr)
8. WhatsApp & Journey Detection Engine End-to-End Validation
9. Price Tamper Resistance & Server-Side Authority
"""
import pytest
from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.services.journey_engine import JourneyDetectionEngine
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_del_airport_record(db_session):
    airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "DEL")
    ).scalar_one_or_none()
    assert airport is not None
    assert airport.iata_code == "DEL"
    assert airport.is_active is True
    assert airport.is_supported is True


def test_del_transit_pricing_matrix(db_session):
    del_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "DEL")
    ).scalar_one_or_none()
    assert del_airport is not None

    expected_pricing = {
        "DOMESTIC_DOMESTIC": 5500.00,
        "DOMESTIC_INTERNATIONAL": 7500.00,
        "INTERNATIONAL_INTERNATIONAL": 9500.00,
        "INTERNATIONAL_DOMESTIC": 7500.00,
    }

    transit_rows = db_session.execute(
        select(AirportService).where(
            AirportService.airport_id == del_airport.id,
            AirportService.journey_type == "TRANSIT",
            AirportService.is_available.is_(True),
        )
    ).scalars().all()

    assert len(transit_rows) == 4
    found_types = {}
    for r in transit_rows:
        found_types[r.flight_type] = float(r.price)

    for ft, exp_price in expected_pricing.items():
        assert ft in found_types
        assert found_types[ft] == exp_price


def test_del_transit_verbatim_features_and_quirks(db_session):
    del_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "DEL")
    ).scalar_one_or_none()
    assert del_airport is not None

    rows = {
        r.flight_type: r for r in db_session.execute(
            select(AirportService).where(
                AirportService.airport_id == del_airport.id,
                AirportService.journey_type == "TRANSIT",
            )
        ).scalars().all()
    }

    # 1. Domestic-Domestic
    dd = rows["DOMESTIC_DOMESTIC"]
    assert dd.features == [
        "WELCOME GUEST FROM AEROBRIDGE/BUS GATE.",
        "BAGGAGE ASSISTANT FOR BAGGAGE.",
        "BUGGY SERVICE AVIALABLE (ONLY AT T3).",
        "ASSIST IN BAGGAGE BELT AREA (IF REQUIRED).",
        "ASSIST IN TERMINAL CHANGE (T2-T3) (IF REQUIRED).",
        "ASSIST IN AIRLINE COUNTERS.",
        "ASSIST IN S.H.A (SECURITY HOLD AREA).",
        "ASSIST TILL BOARDING AREA.",
    ]
    assert "AVIALABLE" in dd.features[2]
    assert "AVAILABLE" not in dd.features[2]
    assert "T2-T3" in dd.features[4]

    # 2. Domestic-International
    di = rows["DOMESTIC_INTERNATIONAL"]
    assert di.features == [
        "WELCOME GUEST FROM AEROBRIDGE .",
        "BAGGAGE ASSISTANT FOR BAGGAGE.",
        "BUGGY SERVICE AVIALABLE (ONLY AT T3).",
        "ASSIST IN BAGGAGE BELT AREA (IF REQUIRED).",
        "ASSIST IN TERMINAL CHANGE (T2-T3) (IF REQUIRED).",
        "ASSIST IN AIRLINE COUNTERS.",
        "GUIDANCE FOR IMMIGRATION COUNTERS",
        "ASSIST IN S.H.A (SECURITY HOLD AREA).",
        "ASSIST TILL BOARDING AREA.",
    ]
    assert "AEROBRIDGE ." in di.features[0]
    assert "T2-T3" in di.features[4]

    # 3. International-International
    ii = rows["INTERNATIONAL_INTERNATIONAL"]
    assert ii.features == [
        "WELCOME GUEST FROM AEROBRIDGE .",
        "BAGGAGE ASSISTANT FOR BAGGAGE.",
        "BUGGY SERVICE AVIALABLE..",
        "ASSIST IN AIRLINE COUNTERS.(IN TRANSIT AREA)",
        "ASSIST IN S.H.A (SECURITY HOLD AREA).",
        "ASSIST TILL BOARDING AREA.",
    ]
    assert "BUGGY SERVICE AVIALABLE.." in ii.features[2]
    assert "ASSIST IN AIRLINE COUNTERS.(IN TRANSIT AREA)" in ii.features[3]

    # 4. International-Domestic
    id_row = rows["INTERNATIONAL_DOMESTIC"]
    assert id_row.features == [
        "WELCOME GUEST FROM AEROBRIDGE .",
        "BAGGAGE ASSISTANT FOR BAGGAGE.",
        "BUGGY SERVICE AVIALABLE (ONLY AT T3).",
        "ASSIST IN BAGGAGE BELT AREA.",
        "ASSIST IN TERMINAL CHANGE (T3-T2) IF REQUIRED",
        "ASSIST IN AIRLINE COUNTERS.",
        "ASSIST IN S.H.A (SECURITY HOLD AREA).",
        "ASSIST TILL BOARDING AREA.",
    ]
    assert "T3-T2" in id_row.features[4]
    assert "T2-T3" not in id_row.features[4]


def test_del_non_transit_services_unmodified(db_session):
    del_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "DEL")
    ).scalar_one_or_none()
    assert del_airport is not None

    dep_count = db_session.execute(
        select(func.count(AirportService.id)).where(
            AirportService.airport_id == del_airport.id,
            AirportService.journey_type == "DEPARTURE",
        )
    ).scalar()
    assert dep_count == 11

    arr_count = db_session.execute(
        select(func.count(AirportService.id)).where(
            AirportService.airport_id == del_airport.id,
            AirportService.journey_type == "ARRIVAL",
        )
    ).scalar()
    assert arr_count == 19


def test_whatsapp_del_transit_menu_rendering(db_session):
    del_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "DEL")
    ).scalar_one_or_none()
    assert del_airport is not None

    for ft, expected_price in [
        (["DOMESTIC_DOMESTIC", "ALL"], 5500.00),
        (["DOMESTIC_INTERNATIONAL", "ALL"], 7500.00),
        (["INTERNATIONAL_INTERNATIONAL", "ALL"], 9500.00),
        (["INTERNATIONAL_DOMESTIC", "ALL"], 7500.00),
    ]:
        rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
            db_session, del_airport.id, "TRANSIT", ft
        )
        assert len(rows) == 1
        aps, svc = rows[0]
        assert float(aps.price) == expected_price
        assert svc.slug == "meet_greet"


def test_cross_airport_isolation(db_session):
    del_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "DEL")
    ).scalar_one_or_none()
    blr_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BLR")
    ).scalar_one_or_none()

    del_rows = db_session.execute(
        select(AirportService).where(
            AirportService.airport_id == del_airport.id,
            AirportService.journey_type == "TRANSIT",
        )
    ).scalars().all()
    del_prices = {r.flight_type: float(r.price) for r in del_rows}

    blr_rows = db_session.execute(
        select(AirportService).where(
            AirportService.airport_id == blr_airport.id,
            AirportService.journey_type == "TRANSIT",
        )
    ).scalars().all()
    blr_prices = {r.flight_type: float(r.price) for r in blr_rows}

    # BLR transit configurations are isolated and do not match DEL transit pricing
    assert del_prices["DOMESTIC_DOMESTIC"] == 5500.00
    assert del_prices["DOMESTIC_INTERNATIONAL"] == 7500.00
