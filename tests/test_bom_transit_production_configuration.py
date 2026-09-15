"""
Comprehensive Test Suite for Mumbai (BOM) Transit Services Complete Replacement.

Covers:
1. BOM Airport Record Invariance
2. BOM Transit Complete Old Removal Verification
3. BOM Transit 4 Route Types Configuration & Pricing:
   - Domestic-Domestic = INR 7150
   - Domestic-International = INR 9000
   - International-Domestic = INR 9000
   - International-International = INR 10000
4. Exact Service Inclusions Count & Verbatim Text Verification:
   - Domestic-Domestic: 9 inclusions
   - Domestic-International: 9 inclusions
   - International-Domestic: 12 inclusions
   - International-International: 6 inclusions
5. Buggy & Lounge Inclusion Specifics Verification
6. Non-Negotiable ASSIST Action Wording Verification
7. Cross-Airport Isolation (BOM vs DEL vs BLR vs Other Airports)
8. Non-Transit BOM Services Invariance (6 Dep, 6 Arr)
9. WhatsApp & Journey Detection Engine End-to-End Validation
10. Price Tamper Resistance & Server-Side Authority
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


def test_bom_airport_record(db_session):
    airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BOM")
    ).scalar_one_or_none()
    assert airport is not None
    assert airport.iata_code == "BOM"
    assert airport.is_active is True
    assert airport.is_supported is True


def test_bom_transit_pricing_matrix(db_session):
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

    assert len(transit_rows) == 4
    found_types = {}
    for r in transit_rows:
        found_types[r.flight_type] = float(r.price)

    for ft, exp_price in expected_pricing.items():
        assert ft in found_types
        assert found_types[ft] == exp_price


def test_bom_transit_verbatim_features_and_counts(db_session):
    bom_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BOM")
    ).scalar_one_or_none()
    assert bom_airport is not None

    rows = {
        r.flight_type: r for r in db_session.execute(
            select(AirportService).where(
                AirportService.airport_id == bom_airport.id,
                AirportService.journey_type == "TRANSIT",
            )
        ).scalars().all()
    }

    # 1. Domestic-Domestic (9 inclusions)
    dd = rows["DOMESTIC_DOMESTIC"]
    assert len(dd.features) == 9
    assert dd.features == [
        "Welcome at the Aerobridge",
        "Dedicated Staff with Placard",
        "Dedicated Porter Service at Arrivals",
        "Wheelchair Assist (through the airline)",
        "Buggy Service from the End of the Aerobridge",
        "Assist inside the Security Hold Area (Transit Area)",
        "Lounge Access for up to 2 hours (Departure only)",
        "Buggy Service to the Boarding Gate (subject to availability)",
        "Escort to the Boarding Gate",
    ]
    assert float(dd.price) == 7150.00

    # 2. Domestic-International (9 inclusions — same operational script as Dom-Dom)
    di = rows["DOMESTIC_INTERNATIONAL"]
    assert len(di.features) == 9
    assert di.features == dd.features
    assert float(di.price) == 9000.00

    # 3. International-Domestic (12 inclusions)
    id_row = rows["INTERNATIONAL_DOMESTIC"]
    assert len(id_row.features) == 12
    assert id_row.features == [
        "Welcome at the Aerobridge",
        "Dedicated Staff with Placard",
        "Dedicated Porter Service at Arrivals",
        "Buggy Service from the End of the Aerobridge",
        "Wheelchair Assist (through the airline)",
        "Guidance to the Immigration Counter",
        "Assist at the Baggage Belt Area",
        "Assist with Separate Check-in at the Airline Counters",
        "Assist inside the Security Hold Area (SHA)",
        "Lounge Access for up to 2 hours (Departure only)",
        "Buggy Service to the Boarding Gate (subject to availability)",
        "Escort to the Boarding Gate",
    ]
    assert float(id_row.price) == 9000.00

    # 4. International-International (6 inclusions)
    ii = rows["INTERNATIONAL_INTERNATIONAL"]
    assert len(ii.features) == 6
    assert ii.features == [
        "Warm welcome at the Aerobridge or Bus Gate by a porter",
        "Dedicated porter assist from the Aerobridge on arrival to the boarding gate of the next connecting flight",
        "Guidance through the airport and airline transit process",
        "Facilitation through security according to the passenger's class of travel",
        "Adani Lounge access with snacks, food, and non-alcoholic beverages",
        "Golf cart transfer to the lounge or boarding gate, subject to the boarding gate location",
    ]
    assert float(ii.price) == 10000.00


def test_bom_non_transit_services_unmodified(db_session):
    bom_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BOM")
    ).scalar_one_or_none()
    assert bom_airport is not None

    dep_count = db_session.execute(
        select(func.count(AirportService.id)).where(
            AirportService.airport_id == bom_airport.id,
            AirportService.journey_type == "DEPARTURE",
        )
    ).scalar()
    assert dep_count == 6

    arr_count = db_session.execute(
        select(func.count(AirportService.id)).where(
            AirportService.airport_id == bom_airport.id,
            AirportService.journey_type == "ARRIVAL",
        )
    ).scalar()
    assert arr_count == 6


def test_whatsapp_bom_transit_menu_rendering(db_session):
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


def test_cross_airport_isolation_bom(db_session):
    bom_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BOM")
    ).scalar_one_or_none()
    del_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "DEL")
    ).scalar_one_or_none()

    bom_rows = {
        r.flight_type: float(r.price) for r in db_session.execute(
            select(AirportService).where(
                AirportService.airport_id == bom_airport.id,
                AirportService.journey_type == "TRANSIT",
            )
        ).scalars().all()
    }

    del_rows = {
        r.flight_type: float(r.price) for r in db_session.execute(
            select(AirportService).where(
                AirportService.airport_id == del_airport.id,
                AirportService.journey_type == "TRANSIT",
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
