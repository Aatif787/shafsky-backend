"""
Comprehensive Tests for Admin Service Inclusions Update and Persistence.

Covers all 6 required test scenarios:
1. Inclusion Update: Changing inclusions persists to DB, returns from API, and appears in list.
2. Price Update Regression: Updating price does not unintentionally modify inclusions.
3. Simultaneous Update: Updating price and inclusions together persists both correctly without overwrite.
4. Reload Persistence: Saved inclusions persist across fresh sessions / page reload.
5. Record Isolation: Updating one airport service does not modify any other airport service.
6. Validation and Failure Handling: Invalid inputs are rejected safely without corrupting existing data.
"""

import uuid
import pytest
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.journey_models import SupportedAirport, AirportService, Service
from app.models.schema import UserAuth, Role
from app.services.admin_service import AdminService
from app.schemas.admin import AirportServiceUpdateRequest


@pytest.fixture
def db():
    """Isolated, fast in-memory SQLite database seeded with test airports and services."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()

    # Seed DEL Airport
    del_apt = SupportedAirport(
        id=uuid.uuid4(),
        iata_code="DEL",
        airport_name="Indira Gandhi International Airport",
        city="New Delhi",
        country="India",
        is_supported=True,
        is_active=True,
    )
    # Seed BOM Airport
    bom_apt = SupportedAirport(
        id=uuid.uuid4(),
        iata_code="BOM",
        airport_name="Chhatrapati Shivaji Maharaj International Airport",
        city="Mumbai",
        country="India",
        is_supported=True,
        is_active=True,
    )
    session.add_all([del_apt, bom_apt])
    session.flush()

    # Seed Service
    svc = Service(
        id=uuid.uuid4(),
        name="Meet & Greet Platinum",
        slug="platinum",
        display_order=1,
        is_active=True,
    )
    session.add(svc)
    session.flush()

    # Seed DEL Service Mapping
    del_service = AirportService(
        id=uuid.uuid4(),
        airport_id=del_apt.id,
        service_id=svc.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        terminal="Terminal 3",
        price=3000.0,
        currency="INR",
        is_available=True,
        features=["Old inclusion A", "Old inclusion B"],
        min_booking_notice_hours=6,
    )
    # Seed BOM Service Mapping
    bom_service = AirportService(
        id=uuid.uuid4(),
        airport_id=bom_apt.id,
        service_id=svc.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        terminal="Terminal 2",
        price=3500.0,
        currency="INR",
        is_available=True,
        features=["BOM inclusion 1", "BOM inclusion 2"],
        min_booking_notice_hours=6,
    )
    session.add_all([del_service, bom_service])
    session.commit()

    yield session

    session.close()


# ─── TEST 1: INCLUSION UPDATE ───
def test_inclusion_update_persists_and_returns(db: Session):
    """Test 1: Change a test service's inclusions from 'Old inclusion A' to 'Updated inclusion A'."""
    mapping = db.scalar(select(AirportService).where(AirportService.terminal == "Terminal 3"))
    mapping_id = str(mapping.id)

    # 1. Update using 'inclusions' alias in updates dictionary
    updated_inclusions = ["Updated inclusion A", "Old inclusion B"]
    result = AdminService.update_airport_service(
        db=db,
        mapping_id=mapping_id,
        updates={"inclusions": updated_inclusions},
        admin_email="admin@shafsky.com",
    )

    # Expected: The API returns the updated value under both 'features' and 'inclusions'
    assert result["features"] == updated_inclusions
    assert result["inclusions"] == updated_inclusions
    assert "Updated inclusion A" in result["features"]

    # Expected: The database stores the updated value
    db.expire_all()
    reloaded = db.scalar(select(AirportService).where(AirportService.id == mapping.id))
    assert reloaded.features == updated_inclusions

    # Expected: Admin listing displays the updated value
    services = AdminService.list_airport_services(db, airport_code="DEL")
    del_svc = next(s for s in services if s["id"] == mapping_id)
    assert del_svc["features"] == updated_inclusions
    assert del_svc["inclusions"] == updated_inclusions


# ─── TEST 2: PRICE UPDATE REGRESSION ───
def test_price_update_does_not_modify_inclusions(db: Session):
    """Test 2: Change a test service's price; ensure inclusions are not modified."""
    mapping = db.scalar(select(AirportService).where(AirportService.terminal == "Terminal 3"))
    mapping_id = str(mapping.id)
    original_inclusions = list(mapping.features)

    # Update only the price
    new_price = 4999.0
    result = AdminService.update_airport_service(
        db=db,
        mapping_id=mapping_id,
        updates={"price": new_price},
        admin_email="admin@shafsky.com",
    )

    # Expected: Price updates correctly
    assert result["price"] == new_price
    # Expected: Inclusions are not unintentionally modified
    assert result["features"] == original_inclusions
    assert result["inclusions"] == original_inclusions

    db.expire_all()
    fresh = db.scalar(select(AirportService).where(AirportService.id == mapping.id))
    assert float(fresh.price) == new_price
    assert fresh.features == original_inclusions


# ─── TEST 3: SIMULTANEOUS UPDATE ───
def test_simultaneous_price_and_inclusions_update(db: Session):
    """Test 3: Update price and inclusions together; neither field overwrites the other."""
    mapping = db.scalar(select(AirportService).where(AirportService.terminal == "Terminal 3"))
    mapping_id = str(mapping.id)

    combined_payload = {
        "price": 5500.0,
        "inclusions": ["Simultaneous Inclusion 1", "Simultaneous Inclusion 2"],
    }

    result = AdminService.update_airport_service(
        db=db,
        mapping_id=mapping_id,
        updates=combined_payload,
        admin_email="admin@shafsky.com",
    )

    assert result["price"] == 5500.0
    assert result["features"] == ["Simultaneous Inclusion 1", "Simultaneous Inclusion 2"]
    assert result["inclusions"] == ["Simultaneous Inclusion 1", "Simultaneous Inclusion 2"]

    db.expire_all()
    fresh = db.scalar(select(AirportService).where(AirportService.id == mapping.id))
    assert float(fresh.price) == 5500.0
    assert fresh.features == ["Simultaneous Inclusion 1", "Simultaneous Inclusion 2"]


# ─── TEST 4: RELOAD PERSISTENCE ───
def test_reload_persistence_across_sessions(db: Session):
    """Test 4: Save updated inclusions, simulate fresh session reload, and fetch again."""
    mapping = db.scalar(select(AirportService).where(AirportService.terminal == "Terminal 3"))
    mapping_id = str(mapping.id)
    persistent_inclusions = ["Per-Session Persistence A", "Per-Session Persistence B"]

    AdminService.update_airport_service(
        db=db,
        mapping_id=mapping_id,
        updates={"features": persistent_inclusions},
        admin_email="admin@shafsky.com",
    )

    # Close and clear session cache (simulating browser reload / separate request)
    db.commit()
    db.close()

    # Re-fetch in clean query
    fresh_list = AdminService.list_airport_services(db, airport_code="DEL")
    item = next(s for s in fresh_list if s["id"] == mapping_id)
    assert item["features"] == persistent_inclusions
    assert item["inclusions"] == persistent_inclusions


# ─── TEST 5: RECORD ISOLATION ───
def test_record_isolation_does_not_modify_other_airports(db: Session):
    """Test 5: Updating DEL inclusions must NOT modify BOM service."""
    del_mapping = db.scalar(select(AirportService).where(AirportService.terminal == "Terminal 3"))
    bom_mapping = db.scalar(select(AirportService).where(AirportService.terminal == "Terminal 2"))

    del_id = str(del_mapping.id)
    bom_id = str(bom_mapping.id)
    original_bom_features = list(bom_mapping.features)
    original_bom_price = float(bom_mapping.price)

    # Update DEL only
    AdminService.update_airport_service(
        db=db,
        mapping_id=del_id,
        updates={"inclusions": ["DEL EXCLUSIVE INCLUSION"]},
        admin_email="admin@shafsky.com",
    )

    # Verify BOM remains untouched
    db.expire_all()
    reloaded_bom = db.scalar(select(AirportService).where(AirportService.id == bom_mapping.id))
    assert reloaded_bom.features == original_bom_features
    assert float(reloaded_bom.price) == original_bom_price


# ─── TEST 6: VALIDATION AND FAILURE HANDLING ───
def test_validation_and_failure_handling(db: Session):
    """Test 6: Invalid payloads must fail safely without destroying existing data."""
    mapping = db.scalar(select(AirportService).where(AirportService.terminal == "Terminal 3"))
    mapping_id = str(mapping.id)
    original_features = list(mapping.features)
    original_price = float(mapping.price)

    # 1. Invalid price rejection
    with pytest.raises(HTTPException) as exc_info:
        AdminService.update_airport_service(
            db=db,
            mapping_id=mapping_id,
            updates={"price": -100.0},
            admin_email="admin@shafsky.com",
        )
    assert exc_info.value.status_code == 400

    # 2. Invalid mapping ID format rejection
    with pytest.raises(HTTPException) as exc_info2:
        AdminService.update_airport_service(
            db=db,
            mapping_id="not-a-valid-uuid",
            updates={"price": 2000.0},
            admin_email="admin@shafsky.com",
        )
    assert exc_info2.value.status_code == 400

    # 3. Nonexistent mapping ID rejection
    with pytest.raises(HTTPException) as exc_info3:
        AdminService.update_airport_service(
            db=db,
            mapping_id=str(uuid.uuid4()),
            updates={"price": 2000.0},
            admin_email="admin@shafsky.com",
        )
    assert exc_info3.value.status_code == 404

    # 4. Confirm original data was not modified or corrupted
    db.expire_all()
    intact = db.scalar(select(AirportService).where(AirportService.id == mapping.id))
    assert intact.features == original_features
    assert float(intact.price) == original_price

    # 5. Verify Pydantic schema AirportServiceUpdateRequest accepts both fields
    req1 = AirportServiceUpdateRequest(features=["feat1"], price=2000)
    assert req1.features == ["feat1"]

    req2 = AirportServiceUpdateRequest(inclusions=["inc1"], price=2500)
    assert req2.inclusions == ["inc1"]
