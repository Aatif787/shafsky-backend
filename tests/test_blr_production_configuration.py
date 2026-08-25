import pytest
from decimal import Decimal
from sqlalchemy import select, func

from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine

EXPECTED_BLR_DOMESTIC_DEPARTURE_SILVER = [
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


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
    - Silver price: INR 4500.00
    - Exact 8 service inclusions with exact wording
    - Action verbs use ASSIST (not ASSISTANCE/ASSISTANT)
    - Gold and Elite do not exist for Domestic Departure
    """
    blr = db_session.execute(select(SupportedAirport).where(SupportedAirport.iata_code == "BLR")).scalar_one()

    # Query active domestic departure services
    dep_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, blr.id, "DEPARTURE", ["DOMESTIC", "ALL"]
    )
    assert len(dep_rows) == 1, f"Expected exactly 1 active Domestic Departure package for BLR, got {len(dep_rows)}"

    aps, svc = dep_rows[0]
    assert svc.slug == "silver"
    assert float(aps.price) == 4500.00
    assert aps.currency == "INR"

    # Verify exact 8 inclusions
    features = list(aps.features)
    assert len(features) == 8
    assert features == EXPECTED_BLR_DOMESTIC_DEPARTURE_SILVER

    # Verify action wording uses ASSIST
    for item in features:
        assert "ASSISTANCE" not in item.upper(), f"Found 'ASSISTANCE' in feature: {item}"
        assert "ASSISTANT" not in item.upper(), f"Found 'ASSISTANT' in feature: {item}"

    # Verify specific items
    assert "ASSIST FROM SEPARATE ENTRY GATE." in features
    assert "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER." in features
    assert "ASSIST IN S.H.A.(SECURITY HOLD AREA)." in features
    assert "ASSIST GUEST UPTO BOARDING GATE." in features
    assert "BUGGY SERVICE AVAILABLE TILL THE BOARDING GATE (SHARING BASIS) & (SUBJECT TO AVAILABILITY)." in features
    assert "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)." in features


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

    # Verify action wording uses ASSIST
    for item in silver_features:
        assert "ASSISTANCE" not in item.upper(), f"Found 'ASSISTANCE' in feature: {item}"
        assert "ASSISTANT" not in item.upper(), f"Found 'ASSISTANT' in feature: {item}"

    assert "ASSIST IN BAGGAGE BELT AREA." in silver_features
    assert "ASSIST GUEST TILL THE CAR PARKING AREA." in silver_features
    assert "BUGGY SERVICE AVAILABLE FROM END OF THE AEROBRIDGE (SHARING BASIS) & (SUBJECT TO AVAILABILITY)." in silver_features
    assert "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)." in silver_features


def test_blr_unsupported_flows_not_configured(db_session):
    """
    Verifies that:
    - BLR International Departure: NOT CONFIGURED (0 active packages)
    - BLR International Arrival: NOT CONFIGURED (0 active packages)
    - BLR Transit: NOT CONFIGURED (0 active packages)
    """
    blr = db_session.execute(select(SupportedAirport).where(SupportedAirport.iata_code == "BLR")).scalar_one()

    # International Departure
    intl_dep = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, blr.id, "DEPARTURE", ["INTERNATIONAL", "ALL"]
    )
    assert len(intl_dep) == 0, f"Expected 0 active International Departure packages for BLR, got {len(intl_dep)}"

    # International Arrival
    intl_arr = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, blr.id, "ARRIVAL", ["INTERNATIONAL", "ALL"]
    )
    assert len(intl_arr) == 0, f"Expected 0 active International Arrival packages for BLR, got {len(intl_arr)}"

    # Transit
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
    1. Domestic Departure Silver -> INR 4500.00
    2. Domestic Arrival Silver -> INR 4500.00
    3. Domestic Arrival Gold -> INR 6500.00
    4. Domestic Arrival Elite -> INR 8000.00
    5. Unsupported combinations return is_valid = False
    """
    from datetime import datetime, timedelta, timezone
    from app.services.journey_engine import JourneyDetectionEngine

    future_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")

    # 1. Domestic Departure Silver (₹4500)
    res_dep_silver = JourneyDetectionEngine.validate_booking(
        db=db_session,
        airport_code="BLR",
        journey_type="DEPARTURE",
        service_date=future_date,
        service_time="14:00",
        selected_service_slugs=["silver"],
        guest_count=1,
        flight_type="DOMESTIC"
    )
    assert res_dep_silver.is_valid is True
    assert res_dep_silver.price_breakdown.subtotal == 4500.00

    # 2. Domestic Arrival Silver (₹4500)
    res_arr_silver = JourneyDetectionEngine.validate_booking(
        db=db_session,
        airport_code="BLR",
        journey_type="ARRIVAL",
        service_date=future_date,
        service_time="14:00",
        selected_service_slugs=["silver"],
        guest_count=1,
        flight_type="DOMESTIC"
    )
    assert res_arr_silver.is_valid is True
    assert res_arr_silver.price_breakdown.subtotal == 4500.00

    # 3. Domestic Arrival Gold (₹6500)
    res_arr_gold = JourneyDetectionEngine.validate_booking(
        db=db_session,
        airport_code="BLR",
        journey_type="ARRIVAL",
        service_date=future_date,
        service_time="14:00",
        selected_service_slugs=["gold"],
        guest_count=1,
        flight_type="DOMESTIC"
    )
    assert res_arr_gold.is_valid is True
    assert res_arr_gold.price_breakdown.subtotal == 6500.00

    # 4. Domestic Arrival Elite (₹8000)
    res_arr_elite = JourneyDetectionEngine.validate_booking(
        db=db_session,
        airport_code="BLR",
        journey_type="ARRIVAL",
        service_date=future_date,
        service_time="14:00",
        selected_service_slugs=["elite"],
        guest_count=1,
        flight_type="DOMESTIC"
    )
    assert res_arr_elite.is_valid is True
    assert res_arr_elite.price_breakdown.subtotal == 8000.00

    # 5. Unsupported combinations
    res_intl = JourneyDetectionEngine.validate_booking(
        db=db_session,
        airport_code="BLR",
        journey_type="DEPARTURE",
        service_date=future_date,
        service_time="14:00",
        selected_service_slugs=["silver"],
        guest_count=1,
        flight_type="INTERNATIONAL"
    )
    assert res_intl.is_valid is False

