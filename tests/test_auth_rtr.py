"""
Comprehensive Production Test Suite for Milestone A2: Refresh Token Rotation (RTR)
and Security Hardening.

Refresh tokens are HttpOnly-cookie only (never returned in JSON body).
"""

import sys
import os
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.main import app
from app.database import SessionLocal
from app.models.schema import UserAuth, RefreshToken, Role
from app.services.auth_service import AuthService
from app.security.jwt import SecurityJWT
from sqlalchemy import select

client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def setup_test_admin_user():
    db = SessionLocal()
    try:
        user = db.scalar(select(UserAuth).where(UserAuth.email == "admin@shafskyaviation.com"))
        if not user:
            user = UserAuth(
                email="admin@shafskyaviation.com",
                password_hash=AuthService.hash_password("ShafskyAdmin2026!"),
                role=Role.SUPER_ADMIN,
                is_verified=True,
                is_active=True,
            )
            db.add(user)
            db.commit()
        else:
            user.password_hash = AuthService.hash_password("ShafskyAdmin2026!")
            user.is_active = True
            db.commit()
    finally:
        db.close()



def _cookie_refresh(response) -> str:
    """Extract refreshToken from Set-Cookie / TestClient cookie jar."""
    token = response.cookies.get("refreshToken") or response.cookies.get("refresh_token")
    if token:
        return token
    # Fallback parse Set-Cookie header
    header = response.headers.get("set-cookie") or ""
    for part in header.split(","):
        part = part.strip()
        if part.lower().startswith("refreshtoken="):
            return part.split(";", 1)[0].split("=", 1)[1]
        if part.lower().startswith("refresh_token="):
            return part.split(";", 1)[0].split("=", 1)[1]
    return ""


def get_admin_auth_headers():
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"}
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data.get("refreshToken") in (None, "")
    refresh = _cookie_refresh(res)
    assert refresh
    return {"Authorization": f"Bearer {data['accessToken']}"}, refresh


def test_01_successful_refresh_token_rotation():
    """Verify single-use refresh token rotation issuing new access and refresh cookies."""
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"}
    )
    assert res.status_code == 200
    data1 = res.json()["data"]
    access1 = data1["accessToken"]
    refresh1 = _cookie_refresh(res)
    assert access1 and refresh1
    assert data1.get("refreshToken") in (None, "")

    res_ref1 = client.post("/api/auth/refresh")
    assert res_ref1.status_code == 200
    data2 = res_ref1.json()["data"]
    access2 = data2["accessToken"]
    refresh2 = _cookie_refresh(res_ref1)
    assert access2 and refresh2
    assert refresh1 != refresh2
    assert data2.get("refreshToken") in (None, "")

    db = SessionLocal()
    hash1 = SecurityJWT.hash_token(refresh1)
    rec1 = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash1))
    assert rec1 is not None
    assert rec1.revoked is True
    db.close()

    res_ref2 = client.post("/api/auth/refresh")
    assert res_ref2.status_code == 200
    data3 = res_ref2.json()["data"]
    assert data3["accessToken"]
    assert _cookie_refresh(res_ref2)


def test_02_replay_attack_detection_and_family_revocation():
    """Verify that attempting to reuse a revoked refresh token triggers Token Family Revocation."""
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"}
    )
    assert res.status_code == 200
    refresh1 = _cookie_refresh(res)

    res_rot = client.post("/api/auth/refresh")
    assert res_rot.status_code == 200
    refresh2 = _cookie_refresh(res_rot)

    # Replay attack with old cookie value (bypass jar by explicit Cookie header)
    res_replay = client.post("/api/auth/refresh", headers={"Cookie": f"refreshToken={refresh1}"})
    assert res_replay.status_code == 401
    assert "replay attack detected" in res_replay.json()["detail"].lower()

    res_attempt_valid = client.post("/api/auth/refresh", headers={"Cookie": f"refreshToken={refresh2}"})
    assert res_attempt_valid.status_code == 401

    db = SessionLocal()
    hash2 = SecurityJWT.hash_token(refresh2)
    rec2 = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash2))
    assert rec2 is not None
    assert rec2.revoked is True
    db.close()


def test_03_logout_revocation_and_cookie_clearing():
    """Verify that logging out revokes the session and clears security cookies."""
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"}
    )
    assert res.status_code == 200
    refresh_token = _cookie_refresh(res)

    res_logout = client.post("/api/auth/logout")
    assert res_logout.status_code == 200
    assert res_logout.json()["success"] is True

    set_cookie_header = res_logout.headers.get("set-cookie", "")
    assert (
        "refreshToken=;" in set_cookie_header
        or 'refreshToken="";' in set_cookie_header
        or "max-age=0" in set_cookie_header.lower()
        or "expires=" in set_cookie_header.lower()
    )

    res_refresh = client.post("/api/auth/refresh", headers={"Cookie": f"refreshToken={refresh_token}"})
    assert res_refresh.status_code == 401


def test_04_http_only_cookie_security_flags():
    """Verify HttpOnly + SameSite flags on refresh token cookie."""
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"}
    )
    assert res.status_code == 200
    set_cookie_header = res.headers.get("set-cookie", "")
    assert "httponly" in set_cookie_header.lower()
    # Production uses Strict+Secure; local/dev uses Lax without Secure for HTTP localhost.
    assert "samesite=lax" in set_cookie_header.lower() or "samesite=strict" in set_cookie_header.lower()


def test_05_expired_refresh_token_rejection():
    """Verify that expired refresh tokens are rejected with HTTP 401."""
    db = SessionLocal()
    user = db.scalar(select(UserAuth).where(UserAuth.email == "admin@shafskyaviation.com"))
    raw_token, token_hash = SecurityJWT.generate_refresh_token()
    expired_time = datetime.now(timezone.utc) - timedelta(days=1)

    exp_rec = RefreshToken(
        user_id=user.id,
        family_id=uuid.uuid4(),
        token_hash=token_hash,
        device_id="test_exp_device",
        expires_at=expired_time,
        revoked=False
    )
    db.add(exp_rec)
    db.commit()
    db.close()

    res = client.post("/api/auth/refresh", headers={"Cookie": f"refreshToken={raw_token}"})
    assert res.status_code == 401
    assert "expired" in res.json()["detail"].lower()


def test_06_revoked_refresh_token_rejection():
    """Verify that explicitly revoked refresh tokens are rejected."""
    db = SessionLocal()
    user = db.scalar(select(UserAuth).where(UserAuth.email == "admin@shafskyaviation.com"))
    raw_token, token_hash = SecurityJWT.generate_refresh_token()

    rev_rec = RefreshToken(
        user_id=user.id,
        family_id=uuid.uuid4(),
        token_hash=token_hash,
        device_id="test_rev_device",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        revoked=True
    )
    db.add(rev_rec)
    db.commit()
    db.close()

    res = client.post("/api/auth/refresh", headers={"Cookie": f"refreshToken={raw_token}"})
    assert res.status_code == 401


def test_07_json_body_refresh_token_ignored():
    """C2: JSON body refresh tokens must not authenticate."""
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"}
    )
    assert res.status_code == 200
    refresh = _cookie_refresh(res)
    # Clear cookies so only JSON body would work if vulnerability existed
    client.cookies.clear()
    res_bad = client.post("/api/auth/refresh", json={"refreshToken": refresh})
    assert res_bad.status_code == 401
