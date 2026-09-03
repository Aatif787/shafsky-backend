import pytest
from app.database import SessionLocal
from app.services.admin_service import AdminService
from fastapi import HTTPException

@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def test_admin_list_airport_services(db_session):
    # List services for LKO
    services = AdminService.list_airport_services(db_session, airport_code="LKO")
    assert len(services) > 0
    assert all(s["airport_code"] == "LKO" for s in services)
    slugs = [s["service_slug"] for s in services]
    assert "platinum" in slugs or "elite" in slugs

def test_admin_update_airport_service_price(db_session):
    # Retrieve a service mapping for LKO
    services = AdminService.list_airport_services(db_session, airport_code="LKO")
    assert len(services) > 0
    first_service = services[0]
    original_price = first_service["price"]
    mapping_id = first_service["id"]

    try:
        # Update price to a test amount
        test_price = original_price + 100.0
        updated = AdminService.update_airport_service(
            db=db_session,
            mapping_id=mapping_id,
            updates={"price": test_price},
            admin_email="testadmin@shafskyaviation.com"
        )
        assert updated["price"] == test_price

        # Verify audit log was recorded
        audit_logs = AdminService.get_audit_logs(db_session, limit=5)
        price_audit = [l for l in audit_logs if l["action"] == "UPDATE_AIRPORT_SERVICE_PRICE"]
        assert len(price_audit) > 0

        # Validate that <= 0 is rejected
        with pytest.raises(HTTPException) as exc:
            AdminService.update_airport_service(
                db=db_session,
                mapping_id=mapping_id,
                updates={"price": -50.0},
                admin_email="testadmin@shafskyaviation.com"
            )
        assert exc.value.status_code == 400

    finally:
        # Revert back to original price
        AdminService.update_airport_service(
            db=db_session,
            mapping_id=mapping_id,
            updates={"price": original_price},
            admin_email="testadmin@shafskyaviation.com"
        )
