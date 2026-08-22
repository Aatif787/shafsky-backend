import sys
import os
import time
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models.schema import RefreshToken, UserAuth, Role
from app.services.auth_service import AuthService
from sqlalchemy import select

client = TestClient(app)


def _ensure_admin_user():
    db = SessionLocal()
    try:
        admin = db.scalar(select(UserAuth).where(UserAuth.email == "admin@shafskyaviation.com"))
        if not admin:
            admin = UserAuth(
                email="admin@shafskyaviation.com",
                password_hash=AuthService.hash_password("ShafskyAdmin2026!"),
                role=Role.SUPER_ADMIN,
                is_verified=True
            )
            db.add(admin)
        else:
            admin.password_hash = AuthService.hash_password("ShafskyAdmin2026!")
            admin.role = Role.SUPER_ADMIN
            admin.is_verified = True
        db.commit()
    finally:
        db.close()


def test_01_authentication_and_hashed_tokens():
    _ensure_admin_user()
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"},
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0)", "X-Device-ID": "master_test_dev_1"}
    )
    assert res.status_code == 200, res.text
    body = res.json()["data"]
    access_token = body["accessToken"]
    raw_refresh = body["refreshToken"]
    assert access_token is not None
    assert raw_refresh is not None

    # Verify raw token is NOT in database
    db = SessionLocal()
    records = list(db.scalars(select(RefreshToken)).all())
    assert len(records) > 0
    for r in records:
        assert r.token_hash != raw_refresh
    db.close()


def test_02_health_and_observability():
    # Dedicated Backend Connectivity Endpoint
    res_api_health = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert res_api_health.status_code == 200
    data = res_api_health.json()
    assert data["status"] == "ok"
    assert data["backend"] == "connected"
    assert data["service"] == "Shafsky Aviation Backend"
    assert "timestamp" in data
    assert res_api_health.headers.get("access-control-allow-origin") == "http://localhost:5173"

    # Health Check
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] in ["UP", "DEGRADED"]

    # Readiness Check
    res_ready = client.get("/ready")
    assert res_ready.status_code == 200
    assert res_ready.json()["ready"] is True

    # Liveness Check
    res_live = client.get("/live")
    assert res_live.status_code == 200
    assert res_live.json()["status"] == "ALIVE"

    # Metrics Exporter
    _ensure_admin_user()
    login_res = client.post("/api/auth/login", json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"})
    admin_token = login_res.json()["data"]["accessToken"]
    res_metrics = client.get("/metrics", headers={"Authorization": f"Bearer {admin_token}"})
    assert res_metrics.status_code == 200
    assert "http_requests_total" in res_metrics.text


def test_03_disaster_recovery_backup_and_restore():
    _ensure_admin_user()
    # Login to get admin token
    login_res = client.post("/api/auth/login", json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"})
    token = login_res.json()["data"]["accessToken"]
    headers = {"Authorization": f"Bearer {token}"}

    # Query DR Status
    st_res = client.get("/api/admin/dr/status", headers=headers)
    assert st_res.status_code == 200
    assert st_res.json()["data"]["drStatus"] == "HEALTHY_READY"

    # Trigger Backup
    bak_res = client.post("/api/admin/dr/backup", headers=headers)
    assert bak_res.status_code == 200
    backup_id = bak_res.json()["data"]["backupMetadata"]["backupId"]

    # Verify Restore
    ver_res = client.post(f"/api/admin/dr/restore-verify?backup_id={backup_id}", headers=headers)
    assert ver_res.status_code == 200
    assert ver_res.json()["data"]["verified"] is True


if __name__ == "__main__":
    test_01_authentication_and_hashed_tokens()
    test_02_health_and_observability()
    test_03_disaster_recovery_backup_and_restore()
    print("ALL MASTER SUITE TESTS PASSED 100%!")
