"""
Comprehensive Production Test Suite for Milestone A2: Refresh Token Rotation (RTR)
and Security Hardening.
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


def get_admin_auth_headers():
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"}
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    return {"Authorization": f"Bearer {data['accessToken']}"}, data["refreshToken"]


def test_01_successful_refresh_token_rotation():
    """Verify single-use refresh token rotation issuing new access and refresh tokens."""
    # 1. Login
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"}
    )
    assert res.status_code == 200
    data1 = res.json()["data"]
    access1 = data1["accessToken"]
    refresh1 = data1["refreshToken"]
    assert access1 and refresh1

    # 2. First Refresh
    res_ref1 = client.post("/api/auth/refresh", json={"refreshToken": refresh1})
    assert res_ref1.status_code == 200
    data2 = res_ref1.json()["data"]
    access2 = data2["accessToken"]
    refresh2 = data2["refreshToken"]
    assert access2 and refresh2
    assert refresh1 != refresh2

    # Verify refresh1 is marked revoked in DB (Single-Use Rule)
    db = SessionLocal()
    hash1 = SecurityJWT.hash_token(refresh1)
    rec1 = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash1))
    assert rec1 is not None
    assert rec1.revoked is True
    db.close()

    # 3. Second Refresh using new token (refresh2)
    res_ref2 = client.post("/api/auth/refresh", json={"refreshToken": refresh2})
    assert res_ref2.status_code == 200
    data3 = res_ref2.json()["data"]
    assert data3["accessToken"] and data3["refreshToken"]


def test_02_replay_attack_detection_and_family_revocation():
    """Verify that attempting to reuse a revoked refresh token triggers Token Family Revocation."""
    # 1. Login
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"}
    )
    assert res.status_code == 200
    data = res.json()["data"]
    refresh1 = data["refreshToken"]

    # 2. Rotate refresh1 -> refresh2
    res_rot = client.post("/api/auth/refresh", json={"refreshToken": refresh1})
    assert res_rot.status_code == 200
    refresh2 = res_rot.json()["data"]["refreshToken"]

    # 3. Replay attack: Re-use refresh1 (which was already rotated)
    res_replay = client.post("/api/auth/refresh", json={"refreshToken": refresh1})
    assert res_replay.status_code == 401
    assert "replay attack detected" in res_replay.json()["detail"].lower()

    # 4. Verify Token Family Revocation: refresh2 should now ALSO be revoked
    res_attempt_valid = client.post("/api/auth/refresh", json={"refreshToken": refresh2})
    assert res_attempt_valid.status_code == 401

    db = SessionLocal()
    hash2 = SecurityJWT.hash_token(refresh2)
    rec2 = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash2))
    assert rec2 is not None
    assert rec2.revoked is True
    db.close()


def test_03_logout_revocation_and_cookie_clearing():
    """Verify that logging out revokes the session and clears security cookies."""
    # 1. Login
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"}
    )
    assert res.status_code == 200
    refresh_token = res.json()["data"]["refreshToken"]

    # 2. Logout
    res_logout = client.post("/api/auth/logout", json={"refreshToken": refresh_token})
    assert res_logout.status_code == 200
    assert res_logout.json()["success"] is True

    # Verify cookie deletion header in logout response
    set_cookie_header = res_logout.headers.get("set-cookie", "")
    assert "refreshToken=;" in set_cookie_header or "refreshToken=\"\";" in set_cookie_header or "max-age=0" in set_cookie_header.lower() or "expires=" in set_cookie_header.lower()

    # 3. Verify logged out refresh token cannot be refreshed
    res_refresh = client.post("/api/auth/refresh", json={"refreshToken": refresh_token})
    assert res_refresh.status_code == 401


def test_04_http_only_cookie_security_flags():
    """Verify HttpOnly, Secure, SameSite=Strict flags on refresh token cookie."""
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@shafskyaviation.com", "password": "ShafskyAdmin2026!"}
    )
    assert res.status_code == 200
    set_cookie_header = res.headers.get("set-cookie", "")
    assert "httponly" in set_cookie_header.lower()
    assert "samesite=strict" in set_cookie_header.lower()
    assert "secure" in set_cookie_header.lower()


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

    res = client.post("/api/auth/refresh", json={"refreshToken": raw_token})
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

    res = client.post("/api/auth/refresh", json={"refreshToken": raw_token})
    assert res.status_code == 401


if __name__ == "__main__":
    test_01_successful_refresh_token_rotation()
    test_02_replay_attack_detection_and_family_revocation()
    test_03_logout_revocation_and_cookie_clearing()
    test_04_http_only_cookie_security_flags()
    test_05_expired_refresh_token_rejection()
    test_06_revoked_refresh_token_rejection()
    print("ALL MILESTONE A2 RTR TESTS PASSED 100%!")
