"""
Unit Tests for Missing Endpoints Integration Suite.
Tests Profile, Notifications, Airports, Feature Flags, Roles/Permissions, and Coupons.
"""

import pytest
import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db, Base, engine, SessionLocal
from app.models.schema import UserAuth, Profile, FeatureFlag, AirportManagement, Coupon, UserNotification, Role
from app.services.auth_service import AuthService
from sqlalchemy import select

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


def _ensure_user(email: str, role: Role = Role.CUSTOMER) -> UserAuth:
    db = SessionLocal()
    try:
        user = db.scalar(select(UserAuth).where(UserAuth.email == email.lower()))
        if not user:
            user = UserAuth(
                email=email.lower(),
                password_hash=AuthService.hash_password("TestPass2026!"),
                role=role,
                is_verified=True,
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.add(Profile(auth_id=user.id, email=email.lower(), role=role, full_name=email.split("@")[0]))
            db.commit()
            db.refresh(user)
        return user
    finally:
        db.close()


def get_auth_token(email: str = "testuser@shafsky.com", role: str = "SUPER_ADMIN") -> str:
    role_enum = Role[role] if role in Role.__members__ else Role.CUSTOMER
    user = _ensure_user(email, role_enum)
    user_data = {"sub": email, "user_id": str(user.id), "role": role}
    return AuthService.create_access_token(user_data)


def test_auth_profile_endpoints():
    token = get_auth_token("profileuser@shafsky.com", "CUSTOMER")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. GET Profile
    res = client.get("/api/auth/profile", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["data"]["email"] == "profileuser@shafsky.com"

    # 2. PATCH Profile
    update_payload = {
        "full_name": "Jane Doe",
        "phone_number": "+19876543210",
        "company": "Shafsky Aviation"
    }
    res_patch = client.patch("/api/auth/profile", json=update_payload, headers=headers)
    assert res_patch.status_code == 200
    patch_data = res_patch.json()
    assert patch_data["success"] is True
    assert patch_data["data"]["full_name"] == "Jane Doe"
    assert patch_data["data"]["phone_number"] == "+19876543210"


def test_notifications_endpoints():
    token = get_auth_token("notifuser@shafsky.com", "CUSTOMER")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. GET Notifications
    res = client.get("/api/notifications", headers=headers)
    assert res.status_code == 200
    assert res.json()["success"] is True

    # 2. Read-All Notifications
    res_read_all = client.post("/api/notifications/read-all", headers=headers)
    assert res_read_all.status_code == 200
    assert res_read_all.json()["success"] is True

    # 3. Read Single Notification
    fake_id = str(uuid.uuid4())
    res_read = client.post(f"/api/notifications/{fake_id}/read", headers=headers)
    assert res_read.status_code == 200

    # 4. Delete Notification
    res_del = client.delete(f"/api/notifications/{fake_id}", headers=headers)
    assert res_del.status_code == 200


def test_feature_flags_endpoints():
    token = get_auth_token("adminff@shafsky.com", "SUPER_ADMIN")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. GET Feature Flags
    res = client.get("/api/config/feature-flags", headers=headers)
    assert res.status_code == 200
    assert res.json()["success"] is True

    # 2. PATCH Feature Flags
    patch_data = {"AUTO_CONFIRM": True, "MOCK_DATA": False}
    res_patch = client.patch("/api/config/feature-flags", json=patch_data, headers=headers)
    assert res_patch.status_code == 200
    assert res_patch.json()["success"] is True


def test_roles_and_permissions_endpoints():
    token = get_auth_token("adminroles@shafsky.com", "SUPER_ADMIN")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. GET Roles
    res_roles = client.get("/api/admin/roles", headers=headers)
    assert res_roles.status_code == 200
    assert res_roles.json()["success"] is True
    assert len(res_roles.json()["data"]) >= 3

    # 2. GET Permissions
    res_perm = client.get("/api/admin/permissions", headers=headers)
    assert res_perm.status_code == 200
    assert res_perm.json()["success"] is True
    assert "matrix" in res_perm.json()["data"]


def test_airports_and_coupons_endpoints():
    token = get_auth_token("adminac@shafsky.com", "SUPER_ADMIN")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. GET Public Airports
    res_air = client.get("/api/airports")
    assert res_air.status_code == 200
    assert res_air.json()["success"] is True

    # 2. GET Coupons
    res_coup = client.get("/api/coupons", headers=headers)
    assert res_coup.status_code == 200
    assert res_coup.json()["success"] is True
