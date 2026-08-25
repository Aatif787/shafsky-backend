import pytest
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, AirportService, Service

@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

def get_atq_service(db, journey_type, flight_type):
    atq = db.query(SupportedAirport).filter_by(iata_code="ATQ").first()
    return db.query(AirportService).filter(
        AirportService.airport_id == atq.id,
        AirportService.journey_type == journey_type,
        AirportService.flight_type == flight_type,
        AirportService.is_available.is_(True)
    ).first()

def test_atq_pricing_and_availability(db):
    atq_airport = db.query(SupportedAirport).filter_by(iata_code="ATQ").first()
    assert atq_airport is not None, "ATQ airport should exist"

    journeys = [
        ("DEPARTURE", "DOMESTIC"),
        ("ARRIVAL", "DOMESTIC"),
        ("DEPARTURE", "INTERNATIONAL"),
        ("ARRIVAL", "INTERNATIONAL")
    ]

    for j_type, f_type in journeys:
        svc = get_atq_service(db, j_type, f_type)
        assert svc is not None, f"No service found for {j_type} {f_type}"
        assert float(svc.price) == 2500.00, f"Expected 2500 for ATQ {j_type} {f_type}, got {svc.price}"

def test_atq_exact_service_inclusions(db):
    # 1. Domestic Departure
    dom_dep = get_atq_service(db, "DEPARTURE", "DOMESTIC")
    assert "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS" in dom_dep.features
    assert "ASSIST IN S.H.A.(SECURITY HOLD AREA)" in dom_dep.features
    
    # 2. International Departure
    int_dep = get_atq_service(db, "DEPARTURE", "INTERNATIONAL")
    assert "ASSIST AT SEPARATE CHECK IN PROCESS AT COUNTERS" in int_dep.features
    assert "ASSIST IN S.H.A.(SECURITY HOLD AREA)" in int_dep.features

    # 3. Domestic Arrival
    dom_arr = get_atq_service(db, "ARRIVAL", "DOMESTIC")
    assert len(dom_arr.features) == 6
    assert "ASSIST GUEST TILL THE CAR PARKING AREA" in dom_arr.features

    # 4. International Arrival
    int_arr = get_atq_service(db, "ARRIVAL", "INTERNATIONAL")
    assert "WELCOME GUEST FROM POST IMMIGRATION." in int_arr.features
    assert "ASSIST GUEST TILL THE PARKING AREA." in int_arr.features

def test_atq_no_transit(db):
    atq = db.query(SupportedAirport).filter_by(iata_code="ATQ").first()
    svc = db.query(AirportService).filter(
        AirportService.airport_id == atq.id,
        AirportService.journey_type == "TRANSIT",
        AirportService.is_available.is_(True)
    ).first()
    assert svc is None, "ATQ should not have transit configured"
