import pytest
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, AirportService, Service
from app.services.journey_engine import JourneyDetectionEngine
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_vtz_airport_record(db: Session):
    """Verify Visakhapatnam airport record exists and is active/supported."""
    vtz = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "VTZ").first()
    assert vtz is not None, "VTZ airport must exist in supported_airports"
    assert vtz.is_active is True, "VTZ must be active"
    assert vtz.is_supported is True, "VTZ must be supported"
    assert vtz.city == "Visakhapatnam"


def test_vtz_production_pricing_matrix(db: Session):
    """Verify VTZ has exactly 2 active rows, both Silver Service at INR 2,500."""
    vtz = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "VTZ").first()
    assert vtz is not None

    active_services = (
        db.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == vtz.id,
            AirportService.is_available.is_(True)
        )
        .all()
    )

    assert len(active_services) == 2, f"VTZ must have exactly 2 active rows, found {len(active_services)}"

    for aps, svc in active_services:
        assert svc.slug == "silver", f"Expected slug=silver, got {svc.slug}"
        assert svc.name == "Silver Service", f"Expected name='Silver Service', got {svc.name}"
        assert float(aps.price) == 2500.00, f"Expected price 2500.00, got {aps.price}"
        assert aps.currency == "INR"
        assert aps.flight_type == "DOMESTIC"
        assert aps.journey_type in ("ARRIVAL", "DEPARTURE")


def test_vtz_verbatim_features_and_counts(db: Session):
    """Verify verbatim features for VTZ Domestic Departure (7) and Arrival (6)."""
    vtz = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "VTZ").first()
    assert vtz is not None

    dep_svc = (
        db.query(AirportService)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == vtz.id,
            AirportService.journey_type == "DEPARTURE",
            AirportService.flight_type == "DOMESTIC",
            AirportService.is_available.is_(True),
        )
        .first()
    )
    assert dep_svc is not None
    assert len(dep_svc.features) == 7
    assert "WELCOME GUEST FROM CURBSIDE AREA." in dep_svc.features

    arr_svc = (
        db.query(AirportService)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == vtz.id,
            AirportService.journey_type == "ARRIVAL",
            AirportService.flight_type == "DOMESTIC",
            AirportService.is_available.is_(True),
        )
        .first()
    )
    assert arr_svc is not None
    assert len(arr_svc.features) == 6
    assert "WELCOME GUEST FROM AEROBRIDGE" in arr_svc.features


def test_vtz_meet_greet_inactive(db: Session):
    """Verify meet_greet slug is completely inactive for VTZ."""
    vtz = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "VTZ").first()
    assert vtz is not None

    mg_active = (
        db.query(AirportService)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == vtz.id,
            Service.slug == "meet_greet",
            AirportService.is_available.is_(True)
        )
        .count()
    )
    assert mg_active == 0, f"Expected 0 active meet_greet rows for VTZ, found {mg_active}"


def test_whatsapp_vtz_menu_rendering(db: Session):
    """Verify WhatsApp renders 'Silver Service — ₹2,500' for Visakhapatnam Domestic Arrival."""
    vtz = db.query(SupportedAirport).filter(SupportedAirport.iata_code == "VTZ").first()
    assert vtz is not None

    package_rows = WhatsAppBookingStateMachine._get_authoritative_airport_packages(
        db=db,
        airport_id=vtz.id,
        journey_type="ARRIVAL",
        flight_types=["DOMESTIC", "ALL"],
        terminal=None
    )

    assert len(package_rows) == 1, f"Expected 1 package row for VTZ Arrival, got {len(package_rows)}"
    aps, svc = package_rows[0]
    assert svc.name == "Silver Service"
    assert svc.slug == "silver"
    assert float(aps.price) == 2500.00
