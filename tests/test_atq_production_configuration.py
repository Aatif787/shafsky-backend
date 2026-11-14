import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal, get_db
from app.models.journey_models import SupportedAirport, AirportService, Service
from app.seeds.airports.atq import (
    ATQ_DOMESTIC_ARRIVAL_DESCRIPTION,
    ATQ_DOMESTIC_ARRIVAL_FEATURES,
    ATQ_DOMESTIC_DEPARTURE_DESCRIPTION,
    ATQ_DOMESTIC_DEPARTURE_FEATURES,
    ATQ_INTERNATIONAL_ARRIVAL_DESCRIPTION,
    ATQ_INTERNATIONAL_ARRIVAL_FEATURES,
    ATQ_INTERNATIONAL_DEPARTURE_DESCRIPTION,
    ATQ_INTERNATIONAL_DEPARTURE_FEATURES,
    ATQ_TRANSIT_DOMESTIC_DOMESTIC_DESCRIPTION,
    ATQ_TRANSIT_DOMESTIC_DOMESTIC_FEATURES,
    ATQ_TRANSIT_DOMESTIC_INTERNATIONAL_DESCRIPTION,
    ATQ_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES,
    ATQ_TRANSIT_INTERNATIONAL_DOMESTIC_DESCRIPTION,
    ATQ_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES,
    seed_atq_production_packages,
)


@pytest.fixture(name="db")
def _db_fixture():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(name="api_client")
def _api_client_fixture(db):
    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _get_active_atq_rows(session):
    atq = session.query(SupportedAirport).filter_by(iata_code="ATQ").first()
    assert atq is not None, "ATQ airport must exist"
    rows = (
        session.query(AirportService, Service)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == atq.id,
            AirportService.is_available.is_(True),
        )
        .order_by(
            AirportService.journey_type,
            AirportService.flight_type,
            AirportService.display_priority,
        )
        .all()
    )
    return atq, rows


def test_atq_pricing_and_package_mappings(db):
    """Verify all 7 ATQ pricing configurations (11 active rows) and package mappings."""
    atq, rows = _get_active_atq_rows(db)
    assert atq.airport_name == "Sri Guru Ram Dass Jee International Airport"
    assert len(rows) == 11, f"Expected 11 active ATQ rows, found {len(rows)}"

    matrix = {
        (aps.journey_type, aps.flight_type, svc.slug): (aps, svc)
        for aps, svc in rows
    }

    expected_prices = {
        ("DEPARTURE", "DOMESTIC", "silver"): 2500.00,
        ("DEPARTURE", "DOMESTIC", "elite"): 4000.00,
        ("ARRIVAL", "DOMESTIC", "silver"): 2500.00,
        ("ARRIVAL", "DOMESTIC", "elite"): 4000.00,
        ("DEPARTURE", "INTERNATIONAL", "silver"): 2500.00,
        ("DEPARTURE", "INTERNATIONAL", "elite"): 4500.00,
        ("ARRIVAL", "INTERNATIONAL", "silver"): 2500.00,
        ("ARRIVAL", "INTERNATIONAL", "elite"): 4500.00,
        ("TRANSIT", "DOMESTIC_DOMESTIC", "meet_greet"): 5000.00,
        ("TRANSIT", "INTERNATIONAL_DOMESTIC", "meet_greet"): 5000.00,
        ("TRANSIT", "DOMESTIC_INTERNATIONAL", "meet_greet"): 5000.00,
    }
    assert set(matrix.keys()) == set(expected_prices.keys())

    for key, expected_price in expected_prices.items():
        aps, _ = matrix[key]
        assert float(aps.price) == expected_price, f"{key} expected {expected_price}, got {aps.price}"
        assert aps.currency == "INR"

    # Legacy Dep/Arr meet_greet rows (if present) must remain preserved (not deleted) and inactive
    inactive_mg_dep_arr = (
        db.query(AirportService)
        .join(Service, AirportService.service_id == Service.id)
        .filter(
            AirportService.airport_id == atq.id,
            Service.slug == "meet_greet",
            AirportService.journey_type.in_(["DEPARTURE", "ARRIVAL"]),
            AirportService.is_available.is_(True),
        )
        .count()
    )
    assert inactive_mg_dep_arr == 0

    # Unsupported Intl -> Intl transit must not be active for ATQ
    intl_intl = (
        db.query(AirportService)
        .filter_by(
            airport_id=atq.id,
            journey_type="TRANSIT",
            flight_type="INTERNATIONAL_INTERNATIONAL",
            is_available=True,
        )
        .first()
    )
    assert intl_intl is None


def test_atq_preserved_descriptions_and_inclusions(db):
    """Verify ATQ service descriptions and inclusions remain preserved across Silver, Elite, and Transit."""
    _, rows = _get_active_atq_rows(db)
    by_key = {(aps.journey_type, aps.flight_type, svc.slug): aps for aps, svc in rows}

    for pkg_slug in ("silver", "elite"):
        assert by_key[("DEPARTURE", "DOMESTIC", pkg_slug)].short_description == ATQ_DOMESTIC_DEPARTURE_DESCRIPTION
        assert by_key[("DEPARTURE", "DOMESTIC", pkg_slug)].features == ATQ_DOMESTIC_DEPARTURE_FEATURES

        assert by_key[("ARRIVAL", "DOMESTIC", pkg_slug)].short_description == ATQ_DOMESTIC_ARRIVAL_DESCRIPTION
        assert by_key[("ARRIVAL", "DOMESTIC", pkg_slug)].features == ATQ_DOMESTIC_ARRIVAL_FEATURES

        assert by_key[("DEPARTURE", "INTERNATIONAL", pkg_slug)].short_description == ATQ_INTERNATIONAL_DEPARTURE_DESCRIPTION
        assert by_key[("DEPARTURE", "INTERNATIONAL", pkg_slug)].features == ATQ_INTERNATIONAL_DEPARTURE_FEATURES

        assert by_key[("ARRIVAL", "INTERNATIONAL", pkg_slug)].short_description == ATQ_INTERNATIONAL_ARRIVAL_DESCRIPTION
        assert by_key[("ARRIVAL", "INTERNATIONAL", pkg_slug)].features == ATQ_INTERNATIONAL_ARRIVAL_FEATURES

    assert by_key[("TRANSIT", "DOMESTIC_DOMESTIC", "meet_greet")].short_description == ATQ_TRANSIT_DOMESTIC_DOMESTIC_DESCRIPTION
    assert by_key[("TRANSIT", "DOMESTIC_DOMESTIC", "meet_greet")].features == ATQ_TRANSIT_DOMESTIC_DOMESTIC_FEATURES

    assert by_key[("TRANSIT", "INTERNATIONAL_DOMESTIC", "meet_greet")].short_description == ATQ_TRANSIT_INTERNATIONAL_DOMESTIC_DESCRIPTION
    assert by_key[("TRANSIT", "INTERNATIONAL_DOMESTIC", "meet_greet")].features == ATQ_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES

    assert by_key[("TRANSIT", "DOMESTIC_INTERNATIONAL", "meet_greet")].short_description == ATQ_TRANSIT_DOMESTIC_INTERNATIONAL_DESCRIPTION
    assert by_key[("TRANSIT", "DOMESTIC_INTERNATIONAL", "meet_greet")].features == ATQ_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES


def test_atq_services_api_all_configs_and_route_derived_transit(api_client):
    """Verify all 7 ATQ pricing configs and 3 route-derived transit types via Airport Services API."""
    # 1. Domestic Departure & Arrival: Silver 2500, Elite 4000
    for jt, origin, dest in [("DEPARTURE", "ATQ", "DEL"), ("ARRIVAL", "DEL", "ATQ")]:
        resp = api_client.get(
            "/api/journey/airports/ATQ/services",
            params={"journey_type": jt, "origin": origin, "destination": dest, "flight_type": "INTERNATIONAL"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["flight_type"] == "DOMESTIC"
        items = body["data"]
        assert [(i["service"]["slug"], i["price"]) for i in items] == [
            ("silver", 2500.0),
            ("elite", 4000.0),
        ]

    # 2. International Departure & Arrival: Silver 2500, Elite 4500
    for jt, origin, dest in [("DEPARTURE", "ATQ", "DXB"), ("ARRIVAL", "DXB", "ATQ")]:
        resp = api_client.get(
            "/api/journey/airports/ATQ/services",
            params={"journey_type": jt, "origin": origin, "destination": dest, "flight_type": "DOMESTIC"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["flight_type"] == "INTERNATIONAL"
        items = body["data"]
        assert [(i["service"]["slug"], i["price"]) for i in items] == [
            ("silver", 2500.0),
            ("elite", 4500.0),
        ]

    # 3. Transit routes: derived strictly from route endpoints (even if conflicting flight_type sent)
    for origin, dest, expected_ft, conflicting_ft in [
        ("DEL", "BOM", "DOMESTIC_DOMESTIC", "INTERNATIONAL_DOMESTIC"),
        ("DXB", "DEL", "INTERNATIONAL_DOMESTIC", "DOMESTIC_DOMESTIC"),
        ("BOM", "DXB", "DOMESTIC_INTERNATIONAL", "DOMESTIC_DOMESTIC"),
    ]:
        resp = api_client.get(
            "/api/journey/airports/ATQ/services",
            params={
                "journey_type": "TRANSIT",
                "origin": origin,
                "destination": dest,
                "flight_type": conflicting_ft,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["flight_type"] == expected_ft
        items = body["data"]
        assert len(items) == 1
        assert items[0]["service"]["slug"] == "meet_greet"
        assert items[0]["flight_type"] == expected_ft
        assert items[0]["price"] == 5000.0

    # 4. Route-derived Transit via /api/journey/detect overrides manual client flight_type
    detect_resp = api_client.post(
        "/api/journey/detect",
        json={
            "journey_type": "TRANSIT",
            "departure_code": "DXB",
            "arrival_code": "DEL",
            "transit_code": "ATQ",
            "service_date": "2026-12-20",
            "flight_type": "DOMESTIC_DOMESTIC",
        },
    )
    assert detect_resp.status_code == 200
    detect_data = detect_resp.json()
    assert detect_data["is_supported"] is True
    assert len(detect_data["available_services"]) == 1
    assert detect_data["available_services"][0]["flight_type"] == "INTERNATIONAL_DOMESTIC"
    assert detect_data["available_services"][0]["price"] == 5000.0

    # 5. Unsupported I -> I transit returns empty list for ATQ
    ii_resp = api_client.get(
        "/api/journey/airports/ATQ/services",
        params={"journey_type": "TRANSIT", "origin": "DXB", "destination": "LHR"},
    )
    assert ii_resp.status_code == 200
    ii_body = ii_resp.json()
    assert ii_body["flight_type"] == "INTERNATIONAL_INTERNATIONAL"
    assert ii_body["data"] == []

    # 6. Master catalog /api/airport/services also returns Silver & Elite for ATQ
    cat_dom = api_client.get(
        "/api/airport/services",
        params={"airport": "ATQ", "journey_type": "departure", "origin": "ATQ", "destination": "DEL"},
    )
    assert cat_dom.status_code == 200
    assert [(p["id"], p["basePrice"]) for p in cat_dom.json()["packages"]] == [
        ("silver", 2500.0),
        ("elite", 4000.0),
    ]

    cat_intl = api_client.get(
        "/api/airport/services",
        params={"airport": "ATQ", "journey_type": "arrival", "origin": "DXB", "destination": "ATQ"},
    )
    assert cat_intl.status_code == 200
    assert [(p["id"], p["basePrice"]) for p in cat_intl.json()["packages"]] == [
        ("silver", 2500.0),
        ("elite", 4500.0),
    ]


def test_atq_seed_idempotency_and_preservation(db):
    """
    Verify repeated ATQ seed execution:
    - Does not create duplicate rows
    - Preserves row IDs across repeated runs
    - Does not delete existing rows or touch unrelated ATQ service records
    """
    try:
        atq, _ = _get_active_atq_rows(db)
        all_before = db.query(AirportService).filter_by(airport_id=atq.id).all()
        before_ids = {r.id for r in all_before}

        # Add an unrelated ATQ add-on record in the uncommitted transaction to ensure it is preserved
        lounge_svc = db.query(Service).filter_by(slug="lounge").first()
        assert lounge_svc is not None
        unrelated = AirportService(
            id=uuid.uuid4(),
            airport_id=atq.id,
            service_id=lounge_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Test unrelated ATQ record",
            features=[],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=99,
            price=1800.00,
            currency="INR",
        )
        db.add(unrelated)
        db.flush()

        service_map = {s.slug: s for s in db.query(Service).all()}
        seed_atq_production_packages(db, atq, service_map)
        seed_atq_production_packages(db, atq, service_map)
        db.flush()

        after_rows = db.query(AirportService).filter_by(airport_id=atq.id).all()
        after_ids = {r.id for r in after_rows}

        # Every previously existing row ID must still exist (no destructive deletes)
        assert before_ids.issubset(after_ids)
        assert unrelated.id in after_ids
        assert unrelated.is_available is True

        active_pkg_rows = [r for r in after_rows if r.is_available and r.id != unrelated.id]
        assert len(active_pkg_rows) == 11
    finally:
        db.rollback()
