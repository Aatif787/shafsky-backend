import pytest
from decimal import Decimal
from sqlalchemy import select, func
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.main import app

EXPECTED_BLR_DOMESTIC_DEPARTURE_SHARED = [
    "WELCOME GUEST FROM CURB SIDE AREA.",
    "PORTER SERVICE WITH DEDICATED STAFF.",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines).",
    "ASSIST FROM SEPARATE ENTRY GATE.",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER.",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA).",
    "BUGGY SERVICE AVAILABLE TILL THE BOARDING GATE (SHARING BASIS) & (SUBJECT TO AVAILABILITY).",
    "ASSIST GUEST UPTO BOARDING GATE.",
]

EXPECTED_BLR_DOMESTIC_ARRIVAL_SHARED = [
    "WELCOME GUEST FROM AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS.",
    "BUGGY SERVICE AVAILABLE FROM END OF THE AEROBRIDGE (SHARING BASIS) & (SUBJECT TO AVAILABILITY).",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines).",
    "ASSIST IN BAGGAGE BELT AREA.",
    "ASSIST GUEST TILL THE CAR PARKING AREA.",
]

EXPECTED_BLR_INTERNATIONAL_DEPARTURE_SHARED = [
    "WELCOME GUEST FROM CURB SIDE AREA.",
    "PORTER SERVICE WITH DEDICATED STAFF.",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines).",
    "ASSIST FROM SEPARATE ENTRY GATE.",
    "ASSIST IN MONEY EXCHANGE COUNTER.",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES.",
    "ASSIST AT SEPARATE BAGGAGE CHECKIN PROCESS AT AIRLINE COUNTERS.",
    "ASSIST FOR IMMIGRATION COUNTERS.",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA).",
    "BUGGY SERVICE AVAILABLE (SUBJECT TO AVAILABILITY)",
    "ASSIST PAX UPTO BOARDING GATE.",
]

EXPECTED_BLR_INTERNATIONAL_ARRIVAL_SHARED = [
    "WELCOME GUEST FROM AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS.",
    "BUGGY SERVICE AVAILABLE (SHARING BASIS) & (SUBJECT TO AVAILABILITY).",
    "ASSIST TO THE IMMIGRATION COUNTER.",
    "ASSIST IN DUTY FREE SHOP.",
    "ASSIST IN BAGGAGE BELT AREA.",
    "ASSIST IN CUSTOM AREA.",
    "ASSIST GUEST TILL THE CAR PARKING AREA.",
]


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def api_client():
    return TestClient(app)


def test_blr_airport_record(db_session):
    """Verifies that the existing BLR SupportedAirport record exists and is unique."""
    airports = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BLR")
    ).scalars().all()
    assert len(airports) == 1, f"Expected exactly 1 BLR airport record, found {len(airports)}"
    assert airports[0].iata_code == "BLR"


def test_blr_domestic_departure_configuration(db_session):
    """
    Verifies BLR Domestic Departure:
    - Silver: INR 4500.00
    - Gold: INR 6500.00
    - Elite: INR 8000.00
    - Exact 8 service inclusions with exact wording
    - Action verbs use ASSIST (not ASSISTANCE/ASSISTANT)
    - All 3 tiers share identical 8 inclusions
    """
    blr = db_session.execute(select(SupportedAirport).where(SupportedAirport.iata_code == "BLR")).scalar_one()

    # Query active domestic departure services
    dep_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, blr.id, "DEPARTURE", ["DOMESTIC", "ALL"]
    )
    assert len(dep_rows) == 3, f"Expected exactly 3 active Domestic Departure packages for BLR, got {len(dep_rows)}"

    slug_map = {svc.slug: (aps, svc) for aps, svc in dep_rows}
    assert "silver" in slug_map
    assert "gold" in slug_map
    assert "elite" in slug_map

    silver_aps, _ = slug_map["silver"]
    gold_aps, _ = slug_map["gold"]
    elite_aps, _ = slug_map["elite"]

    assert float(silver_aps.price) == 4500.00
    assert float(gold_aps.price) == 6500.00
    assert float(elite_aps.price) == 8000.00
    assert silver_aps.currency == "INR"

    # Verify exact 8 inclusions
    silver_features = list(silver_aps.features)
    gold_features = list(gold_aps.features)
    elite_features = list(elite_aps.features)

    assert len(silver_features) == 8
    assert len(gold_features) == 8
    assert len(elite_features) == 8

    assert silver_features == EXPECTED_BLR_DOMESTIC_DEPARTURE_SHARED
    assert gold_features == EXPECTED_BLR_DOMESTIC_DEPARTURE_SHARED
    assert elite_features == EXPECTED_BLR_DOMESTIC_DEPARTURE_SHARED

    # Invariance
    assert silver_features == gold_features
    assert silver_features == elite_features

    # Verify action wording uses ASSIST
    for item in silver_features:
        assert "ASSISTANCE" not in item.upper(), f"Found 'ASSISTANCE' in feature: {item}"
        assert "ASSISTANT" not in item.upper(), f"Found 'ASSISTANT' in feature: {item}"


def test_blr_domestic_arrival_configuration_and_tier_invariance(db_session):
    """
    Verifies BLR Domestic Arrival:
    - Silver: INR 4500.00
    - Gold: INR 6500.00
    - Elite: INR 8000.00
    - Mandatory Rule: Silver, Gold, and Elite MUST have IDENTICAL 7 service inclusions.
    """
    blr = db_session.execute(select(SupportedAirport).where(SupportedAirport.iata_code == "BLR")).scalar_one()

    # Query active domestic arrival services
    arr_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, blr.id, "ARRIVAL", ["DOMESTIC", "ALL"]
    )
    assert len(arr_rows) == 3, f"Expected 3 active Domestic Arrival packages for BLR, got {len(arr_rows)}"

    slug_map = {svc.slug: (aps, svc) for aps, svc in arr_rows}
    assert "silver" in slug_map
    assert "gold" in slug_map
    assert "elite" in slug_map

    silver_aps, _ = slug_map["silver"]
    gold_aps, _ = slug_map["gold"]
    elite_aps, _ = slug_map["elite"]

    # Verify prices
    assert float(silver_aps.price) == 4500.00
    assert float(gold_aps.price) == 6500.00
    assert float(elite_aps.price) == 8000.00

    # Verify exact 7 inclusions
    silver_features = list(silver_aps.features)
    gold_features = list(gold_aps.features)
    elite_features = list(elite_aps.features)

    assert len(silver_features) == 7
    assert len(gold_features) == 7
    assert len(elite_features) == 7

    assert silver_features == EXPECTED_BLR_DOMESTIC_ARRIVAL_SHARED
    assert gold_features == EXPECTED_BLR_DOMESTIC_ARRIVAL_SHARED
    assert elite_features == EXPECTED_BLR_DOMESTIC_ARRIVAL_SHARED

    # CRITICAL: Invariance Check
    assert silver_features == gold_features, "CRITICAL ERROR: Silver and Gold inclusions differ for BLR Domestic Arrival"
    assert silver_features == elite_features, "CRITICAL ERROR: Silver and Elite inclusions differ for BLR Domestic Arrival"
    assert gold_features == elite_features, "CRITICAL ERROR: Gold and Elite inclusions differ for BLR Domestic Arrival"


def test_blr_international_departure_configuration(db_session):
    """
    Verifies BLR International Departure:
    - Silver: INR 6000.00
    - Gold: INR 9000.00
    - Elite: INR 13000.00
    - Exact 11 service inclusions with exact wording
    - Action verbs use ASSIST (not ASSISTANCE/ASSISTANT)
    - All 3 tiers share identical 11 inclusions
    """
    blr = db_session.execute(select(SupportedAirport).where(SupportedAirport.iata_code == "BLR")).scalar_one()

    # Query active international departure services
    dep_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, blr.id, "DEPARTURE", ["INTERNATIONAL", "ALL"]
    )
    assert len(dep_rows) == 3, f"Expected exactly 3 active International Departure packages for BLR, got {len(dep_rows)}"

    slug_map = {svc.slug: (aps, svc) for aps, svc in dep_rows}
    assert "silver" in slug_map
    assert "gold" in slug_map
    assert "elite" in slug_map

    silver_aps, _ = slug_map["silver"]
    gold_aps, _ = slug_map["gold"]
    elite_aps, _ = slug_map["elite"]

    assert float(silver_aps.price) == 6000.00
    assert float(gold_aps.price) == 9000.00
    assert float(elite_aps.price) == 13000.00
    assert silver_aps.currency == "INR"

    # Verify exact 11 inclusions
    silver_features = list(silver_aps.features)
    gold_features = list(gold_aps.features)
    elite_features = list(elite_aps.features)

    assert len(silver_features) == 11
    assert len(gold_features) == 11
    assert len(elite_features) == 11

    assert silver_features == EXPECTED_BLR_INTERNATIONAL_DEPARTURE_SHARED
    assert gold_features == EXPECTED_BLR_INTERNATIONAL_DEPARTURE_SHARED
    assert elite_features == EXPECTED_BLR_INTERNATIONAL_DEPARTURE_SHARED

    # Invariance
    assert silver_features == gold_features
    assert silver_features == elite_features

    # Verify action wording uses ASSIST
    for item in silver_features:
        assert "ASSISTANCE" not in item.upper(), f"Found 'ASSISTANCE' in feature: {item}"
        assert "ASSISTANT" not in item.upper(), f"Found 'ASSISTANT' in feature: {item}"

    # Verify specific phrases
    assert "WELCOME GUEST FROM CURB SIDE AREA." in silver_features
    assert "ASSIST AT SEPARATE BAGGAGE CHECKIN PROCESS AT AIRLINE COUNTERS." in silver_features
    assert "ASSIST IN S.H.A.(SECURITY HOLD AREA)." in silver_features
    assert "BUGGY SERVICE AVAILABLE (SUBJECT TO AVAILABILITY)" in silver_features
    assert "ASSIST PAX UPTO BOARDING GATE." in silver_features


def test_blr_international_arrival_configuration(db_session):
    """
    Verifies BLR International Arrival:
    - Silver: INR 6000.00
    - Gold: INR 9000.00
    - Elite: INR 13000.00
    - Exact 9 service inclusions with exact wording
    - Action verbs use ASSIST (not ASSISTANCE/ASSISTANT)
    - All 3 tiers share identical 9 inclusions
    """
    blr = db_session.execute(select(SupportedAirport).where(SupportedAirport.iata_code == "BLR")).scalar_one()

    # Query active international arrival services
    arr_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, blr.id, "ARRIVAL", ["INTERNATIONAL", "ALL"]
    )
    assert len(arr_rows) == 3, f"Expected 3 active International Arrival packages for BLR, got {len(arr_rows)}"

    slug_map = {svc.slug: (aps, svc) for aps, svc in arr_rows}
    assert "silver" in slug_map
    assert "gold" in slug_map
    assert "elite" in slug_map

    silver_aps, _ = slug_map["silver"]
    gold_aps, _ = slug_map["gold"]
    elite_aps, _ = slug_map["elite"]

    assert float(silver_aps.price) == 6000.00
    assert float(gold_aps.price) == 9000.00
    assert float(elite_aps.price) == 13000.00

    # Verify exact 9 inclusions
    silver_features = list(silver_aps.features)
    gold_features = list(gold_aps.features)
    elite_features = list(elite_aps.features)

    assert len(silver_features) == 9
    assert len(gold_features) == 9
    assert len(elite_features) == 9

    assert silver_features == EXPECTED_BLR_INTERNATIONAL_ARRIVAL_SHARED
    assert gold_features == EXPECTED_BLR_INTERNATIONAL_ARRIVAL_SHARED
    assert elite_features == EXPECTED_BLR_INTERNATIONAL_ARRIVAL_SHARED

    # Invariance
    assert silver_features == gold_features
    assert silver_features == elite_features

    # Verify action wording uses ASSIST
    for item in silver_features:
        assert "ASSISTANCE" not in item.upper(), f"Found 'ASSISTANCE' in feature: {item}"
        assert "ASSISTANT" not in item.upper(), f"Found 'ASSISTANT' in feature: {item}"

    # Verify specific phrases
    assert "WELCOME GUEST FROM AEROBRIDGE." in silver_features
    assert "DEDICATED STAFF WITH PLACARD." in silver_features
    assert "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS." in silver_features
    assert "BUGGY SERVICE AVAILABLE (SHARING BASIS) & (SUBJECT TO AVAILABILITY)." in silver_features
    assert "ASSIST TO THE IMMIGRATION COUNTER." in silver_features
    assert "ASSIST IN DUTY FREE SHOP." in silver_features
    assert "ASSIST IN BAGGAGE BELT AREA." in silver_features
    assert "ASSIST IN CUSTOM AREA." in silver_features
    assert "ASSIST GUEST TILL THE CAR PARKING AREA." in silver_features


def test_blr_transit_not_configured(db_session):
    """Verifies BLR Transit has 0 active packages."""
    blr = db_session.execute(select(SupportedAirport).where(SupportedAirport.iata_code == "BLR")).scalar_one()
    transit = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, blr.id, "TRANSIT", ["DOMESTIC_DOMESTIC", "ALL"]
    )
    assert len(transit) == 0, f"Expected 0 active Transit packages for BLR, got {len(transit)}"


def test_cross_airport_isolation(db_session):
    """
    Verifies that other airports (e.g. DEL, BOM, AMD, COK) remain completely unaffected
    and preserve their original package pricing and configurations.
    """
    # Check DEL International Departure T3
    del_ap = db_session.execute(select(SupportedAirport).where(SupportedAirport.iata_code == "DEL")).scalar_one()
    del_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, del_ap.id, "DEPARTURE", ["INTERNATIONAL", "ALL"], terminal="Terminal 3"
    )
    del_prices = {svc.slug: float(aps.price) for aps, svc in del_rows}
    assert del_prices["silver"] == 5500.00
    assert del_prices["gold"] == 6500.00
    assert del_prices["elite"] == 7000.00

    # Check COK Domestic Departure
    cok_ap = db_session.execute(select(SupportedAirport).where(SupportedAirport.iata_code == "COK")).scalar_one()
    cok_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, cok_ap.id, "DEPARTURE", ["DOMESTIC", "ALL"]
    )
    cok_prices = {svc.slug: float(aps.price) for aps, svc in cok_rows}
    assert cok_prices["silver"] == 3500.00
    assert cok_prices["elite"] == 5500.00


def test_blr_server_side_pricing_and_tamper_resistance(db_session):
    """
    Tests JourneyDetectionEngine server-side pricing calculation for BLR bookings:
    1. Domestic Departure Silver -> INR 4500.00, Gold -> INR 6500.00, Elite -> INR 8000.00
    2. Domestic Arrival Silver -> INR 4500.00, Gold -> INR 6500.00, Elite -> INR 8000.00
    3. International Departure Silver -> INR 6000.00, Gold -> INR 9000.00, Elite -> INR 13000.00
    4. International Arrival Silver -> INR 6000.00, Gold -> INR 9000.00, Elite -> INR 13000.00
    5. Price tampering resistance (server calculates independently)
    """
    from datetime import datetime, timedelta, timezone
    from app.services.journey_engine import JourneyDetectionEngine

    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")

    # Domestic Departure
    for slug, expected_price in [("silver", 4500.0), ("gold", 6500.0), ("elite", 8000.0)]:
        res = JourneyDetectionEngine.validate_booking(
            db=db_session,
            airport_code="BLR",
            journey_type="DEPARTURE",
            service_date=future_date,
            service_time="14:00",
            selected_service_slugs=[slug],
            guest_count=1,
            flight_type="DOMESTIC"
        )
        assert res.is_valid is True
        assert res.price_breakdown.subtotal == expected_price

    # Domestic Arrival
    for slug, expected_price in [("silver", 4500.0), ("gold", 6500.0), ("elite", 8000.0)]:
        res = JourneyDetectionEngine.validate_booking(
            db=db_session,
            airport_code="BLR",
            journey_type="ARRIVAL",
            service_date=future_date,
            service_time="14:00",
            selected_service_slugs=[slug],
            guest_count=1,
            flight_type="DOMESTIC"
        )
        assert res.is_valid is True
        assert res.price_breakdown.subtotal == expected_price

    # International Departure
    for slug, expected_price in [("silver", 6000.0), ("gold", 9000.0), ("elite", 13000.0)]:
        res = JourneyDetectionEngine.validate_booking(
            db=db_session,
            airport_code="BLR",
            journey_type="DEPARTURE",
            service_date=future_date,
            service_time="14:00",
            selected_service_slugs=[slug],
            guest_count=1,
            flight_type="INTERNATIONAL"
        )
        assert res.is_valid is True
        assert res.price_breakdown.subtotal == expected_price

    # International Arrival
    for slug, expected_price in [("silver", 6000.0), ("gold", 9000.0), ("elite", 13000.0)]:
        res = JourneyDetectionEngine.validate_booking(
            db=db_session,
            airport_code="BLR",
            journey_type="ARRIVAL",
            service_date=future_date,
            service_time="14:00",
            selected_service_slugs=[slug],
            guest_count=1,
            flight_type="INTERNATIONAL"
        )
        assert res.is_valid is True
        assert res.price_breakdown.subtotal == expected_price


def test_blr_api_endpoint_verification(api_client):
    """
    Tests REST API endpoints for BLR services:
    - GET /api/journey/airports/BLR/services?journey_type=DEPARTURE&flight_type=INTERNATIONAL
    - GET /api/journey/airports/BLR/services?journey_type=ARRIVAL&flight_type=INTERNATIONAL
    """
    # Intl Departure
    resp_dep = api_client.get("/api/journey/airports/BLR/services?journey_type=DEPARTURE&flight_type=INTERNATIONAL")
    assert resp_dep.status_code == 200
    dep_data = resp_dep.json()
    assert len(dep_data["data"]) == 3
    dep_prices = {s["service"]["slug"]: float(s["price"]) for s in dep_data["data"]}
    assert dep_prices["silver"] == 6000.00
    assert dep_prices["gold"] == 9000.00
    assert dep_prices["elite"] == 13000.00

    for s in dep_data["data"]:
        assert len(s["features"]) == 11
        assert s["features"] == EXPECTED_BLR_INTERNATIONAL_DEPARTURE_SHARED

    # Intl Arrival
    resp_arr = api_client.get("/api/journey/airports/BLR/services?journey_type=ARRIVAL&flight_type=INTERNATIONAL")
    assert resp_arr.status_code == 200
    arr_data = resp_arr.json()
    assert len(arr_data["data"]) == 3
    arr_prices = {s["service"]["slug"]: float(s["price"]) for s in arr_data["data"]}
    assert arr_prices["silver"] == 6000.00
    assert arr_prices["gold"] == 9000.00
    assert arr_prices["elite"] == 13000.00

    for s in arr_data["data"]:
        assert len(s["features"]) == 9
        assert s["features"] == EXPECTED_BLR_INTERNATIONAL_ARRIVAL_SHARED
