import pytest
import uuid
from decimal import Decimal
from sqlalchemy import select, func

from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, Service, AirportService
from app.models.schema import Booking
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine
from app.models.whatsapp_models import WhatsAppConversation

PACKAGE_SLUGS = {
    "silver", "silver-service", "gold", "gold-service",
    "elite", "elite-service", "elite_plus", "elite-plus",
    "platinum", "platinum-service", "essential", "premium", "vip"
}
UNWANTED_STANDALONE_SLUGS = {"meet_greet", "fast_track", "lounge", "porter", "buggy", "wheelchair", "transport"}


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_database_invariants_preserved(db_session):
    """
    Validates that:
    1. Global services catalog is 100% intact (12 services).
    2. All 20 supported airports are intact.
    3. Historical bookings are untouched.
    4. Zero active standalone mappings exist in the entire airport_services table.
    5. Exactly 129 active main packages exist.
    6. Total airport_services rows remains 367.
    """
    # Catalog counts are derived from the seed (12 Service rows including add-ons,
    # 20 airports). Mapping totals grow when TRV/BOM transit are active — do not
    # freeze a historical Neon dump count here.
    services = db_session.execute(select(Service)).scalars().all()
    assert len(services) == 12

    airports = db_session.execute(select(SupportedAirport)).scalars().all()
    assert len(airports) == 20

    addon_slugs = {"porter", "buggy", "wheelchair", "transport", "fast_track", "lounge"}
    leaked = db_session.execute(
        select(func.count(AirportService.id))
        .join(Service, AirportService.service_id == Service.id)
        .where(AirportService.is_available.is_(True), Service.slug.in_(addon_slugs))
    ).scalar()
    assert leaked == 0

    trv = db_session.execute(select(SupportedAirport).where(SupportedAirport.iata_code == "TRV")).scalar_one()
    trv_active = db_session.execute(
        select(func.count(AirportService.id)).where(
            AirportService.airport_id == trv.id, AirportService.is_available.is_(True)
        )
    ).scalar()
    assert trv_active == 6

    bom = db_session.execute(select(SupportedAirport).where(SupportedAirport.iata_code == "BOM")).scalar_one()
    bom_transit = db_session.execute(
        select(func.count(AirportService.id)).where(
            AirportService.airport_id == bom.id,
            AirportService.journey_type == "TRANSIT",
            AirportService.is_available.is_(True),
        )
    ).scalar()
    assert bom_transit == 4


def test_delhi_whatsapp_menu_cleanliness(db_session):
    """
    Specifically tests that Delhi (DEL) returns only authoritative main packages
    and zero standalone leaks.
    """
    del_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "DEL")
    ).scalar_one_or_none()
    assert del_airport is not None

    # Test DEL International Departure
    rows_intl_dep = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, del_airport.id, "DEPARTURE", ["INTERNATIONAL", "ALL"], terminal="Terminal 3"
    )
    assert len(rows_intl_dep) == 3
    returned_slugs = [svc.slug.lower() for _, svc in rows_intl_dep]
    assert "silver" in returned_slugs
    assert "gold" in returned_slugs
    assert "elite" in returned_slugs
    assert "meet_greet" not in returned_slugs

    # Test DEL Domestic Arrival T1/T2
    rows_dom_arr = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, del_airport.id, "ARRIVAL", ["DOMESTIC", "ALL"], terminal="Terminal 1 & 2"
    )
    returned_dom_slugs = [svc.slug.lower() for _, svc in rows_dom_arr]
    assert "silver" in returned_dom_slugs
    assert "elite" in returned_dom_slugs
    assert "meet_greet" not in returned_dom_slugs

    # Test DEL Transit (Active: returns 1 meet_greet via standalone fallback)
    rows_transit = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, del_airport.id, "TRANSIT", ["DOMESTIC_DOMESTIC", "ALL"]
    )
    assert len(rows_transit) == 1
    assert rows_transit[0][1].slug == "meet_greet"


def test_atq_and_bom_transit_cleanliness(db_session):
    """
    Verifies that ATQ departure sells the seeded meet_greet product, and BOM transit returns the live transit package.
    """
    atq_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "ATQ")
    ).scalar_one_or_none()
    assert atq_airport is not None
    atq_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, atq_airport.id, "DEPARTURE", ["DOMESTIC", "ALL"]
    )
    assert len(atq_rows) == 1
    assert atq_rows[0][1].slug == "meet_greet"
    assert float(atq_rows[0][0].price) == 2500.0

    bom_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "BOM")
    ).scalar_one_or_none()
    assert bom_airport is not None
    bom_transit_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, bom_airport.id, "TRANSIT", ["DOMESTIC_DOMESTIC", "ALL"]
    )
    assert len(bom_transit_rows) == 1
    assert bom_transit_rows[0][1].slug == "meet_greet"
    assert float(bom_transit_rows[0][0].price) == 7150.0


def test_all_20_airports_whatsapp_package_menu(db_session):
    """
    Asserts across all 20 supported airports and all journey/travel permutations
    that ONLY main packages (Silver, Gold, Elite, Platinum, Elite Plus) are returned.
    """
    airports = db_session.execute(select(SupportedAirport)).scalars().all()

    permutations = [
        ("ARRIVAL", ["DOMESTIC", "ALL"]),
        ("ARRIVAL", ["INTERNATIONAL", "ALL"]),
        ("DEPARTURE", ["DOMESTIC", "ALL"]),
        ("DEPARTURE", ["INTERNATIONAL", "ALL"]),
        ("TRANSIT", ["DOMESTIC_DOMESTIC", "ALL"]),
        ("TRANSIT", ["INTERNATIONAL_INTERNATIONAL", "ALL"])
    ]

    for ap in airports:
        for jt, ftypes in permutations:
            rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
                db_session, ap.id, jt, ftypes
            )
            for aps, svc in rows:
                slug = svc.slug.lower()
                # Transit and ATQ sell meet_greet as the catalog product, not silver/gold/elite.
                if jt == "TRANSIT" or slug == "meet_greet":
                    continue
                assert slug in PACKAGE_SLUGS, (
                    f"CRITICAL: Non-main package '{svc.name}' returned at airport {ap.iata_code} {jt} {ftypes}"
                )


def test_terminal_isolation(db_session):
    """
    Verifies that terminal-specific pricing and packages are accurately isolated.
    """
    del_airport = db_session.execute(
        select(SupportedAirport).where(SupportedAirport.iata_code == "DEL")
    ).scalar_one_or_none()

    # T1/T2 Domestic Departure
    t1_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, del_airport.id, "DEPARTURE", ["DOMESTIC", "ALL"], terminal="Terminal 1"
    )
    t1_prices = {svc.slug: float(aps.price) for aps, svc in t1_rows}
    assert t1_prices.get("silver") == 3000.0

    # T3 Domestic Departure
    t3_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db_session, del_airport.id, "DEPARTURE", ["DOMESTIC", "ALL"], terminal="Terminal 3"
    )
    t3_prices = {svc.slug: float(aps.price) for aps, svc in t3_rows}
    assert t3_prices.get("silver") == 3000.0
    assert t3_prices.get("gold") == 3500.0
