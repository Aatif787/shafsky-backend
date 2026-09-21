"""
Admin portal production auth contract tests.

Covers:
- valid SUPER_ADMIN login
- invalid password
- missing admin user
- case-insensitive email login
- refresh + logout cookie flow
- CORS preflight for Vercel admin origin
- HttpOnly refresh cookie flags
- no production ADMIN_PASSWORD plaintext bypass
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, delete

from app.main import app
from app.database import SessionLocal
from app.models.schema import UserAuth, Role, RefreshToken
from app.services.auth_service import AuthService
from app.config import settings

client = TestClient(app)

ADMIN_PORTAL_ORIGIN = "https://shafsky-admin-portal.vercel.app"
PASSWORD = "ShafskyAdmin2026!"


def _unique_email(prefix: str) -> str:
    return f"{prefix}.{uuid.uuid4().hex[:10]}@shafskyaviation.com"


def _create_user(email: str, password: str, role: Role = Role.SUPER_ADMIN, *, mixed_case: bool = False) -> str:
    stored = email if mixed_case else email.lower()
    db = SessionLocal()
    try:
        user = UserAuth(
            email=stored,
            password_hash=AuthService.hash_password(password),
            role=role,
            is_verified=True,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return stored
    finally:
        db.close()


def _delete_user(email: str) -> None:
    db = SessionLocal()
    try:
        from sqlalchemy import func
        from app.models.schema import Profile

        user = db.scalar(
            select(UserAuth).where(func.lower(UserAuth.email) == email.lower())
        )
        if not user:
            return
        db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
        profile = db.scalar(select(Profile).where(Profile.auth_id == user.id))
        if profile:
            db.delete(profile)
        db.delete(user)
        db.commit()
    finally:
        db.close()


def _cookie_refresh(response) -> str:
    token = response.cookies.get("refreshToken") or response.cookies.get("refresh_token")
    if token:
        return token
    header = response.headers.get("set-cookie") or ""
    for part in header.split(","):
        part = part.strip()
        low = part.lower()
        if low.startswith("refreshtoken="):
            return part.split(";", 1)[0].split("=", 1)[1]
        if low.startswith("refresh_token="):
            return part.split(";", 1)[0].split("=", 1)[1]
    return ""


def test_valid_admin_login_returns_access_token_and_httponly_cookie():
    email = _unique_email("portal.admin")
    _create_user(email, PASSWORD)
    try:
        res = client.post(
            "/api/auth/login",
            json={"email": email, "password": PASSWORD},
            headers={"Origin": ADMIN_PORTAL_ORIGIN},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["success"] is True
        assert body["data"]["accessToken"]
        assert body["data"].get("refreshToken") in (None, "")
        assert body["data"]["user"]["role"] == "SUPER_ADMIN"
        assert body["data"]["user"]["email"].lower() == email.lower()

        assert res.headers.get("access-control-allow-origin") == ADMIN_PORTAL_ORIGIN
        set_cookie = (res.headers.get("set-cookie") or "").lower()
        assert "httponly" in set_cookie
        assert "refreshtoken=" in set_cookie or "refresh_token=" in set_cookie
        assert "samesite=" in set_cookie
        assert _cookie_refresh(res)
    finally:
        _delete_user(email)


def test_invalid_password_returns_401():
    email = _unique_email("portal.badpass")
    _create_user(email, PASSWORD)
    try:
        res = client.post(
            "/api/auth/login",
            json={"email": email, "password": "WrongPassword!!!"},
            headers={"Origin": ADMIN_PORTAL_ORIGIN},
        )
        assert res.status_code == 401
        detail = res.json().get("detail") or res.json().get("error") or ""
        assert "invalid" in str(detail).lower()
    finally:
        _delete_user(email)


def test_missing_admin_user_returns_401():
    missing = _unique_email("portal.missing")
    res = client.post(
        "/api/auth/login",
        json={"email": missing, "password": PASSWORD},
        headers={"Origin": ADMIN_PORTAL_ORIGIN},
    )
    assert res.status_code == 401
    detail = res.json().get("detail") or res.json().get("error") or ""
    assert "invalid" in str(detail).lower()


def test_case_insensitive_email_login():
    """Stored mixed-case email must authenticate when client sends lowercase."""
    local = f"MixedCase.{uuid.uuid4().hex[:8]}"
    stored = f"{local}@ShafskyAviation.com"
    password = PASSWORD
    _create_user(stored, password, mixed_case=True)
    try:
        res = client.post(
            "/api/auth/login",
            json={"email": stored.lower(), "password": password},
        )
        assert res.status_code == 200, res.text
        assert res.json()["data"]["user"]["role"] == "SUPER_ADMIN"
    finally:
        _delete_user(stored)


def test_refresh_and_logout_cookie_flow():
    email = _unique_email("portal.refresh")
    _create_user(email, PASSWORD)
    try:
        login = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert login.status_code == 200
        refresh1 = _cookie_refresh(login)
        assert refresh1

        refresh_res = client.post(
            "/api/auth/refresh",
            headers={"Origin": ADMIN_PORTAL_ORIGIN},
        )
        assert refresh_res.status_code == 200, refresh_res.text
        assert refresh_res.json()["data"]["accessToken"]
        assert refresh_res.json()["data"].get("refreshToken") in (None, "")
        assert refresh_res.headers.get("access-control-allow-origin") == ADMIN_PORTAL_ORIGIN
        refresh2 = _cookie_refresh(refresh_res)
        assert refresh2 and refresh2 != refresh1

        logout = client.post(
            "/api/auth/logout",
            headers={"Origin": ADMIN_PORTAL_ORIGIN},
        )
        assert logout.status_code == 200
        assert logout.json()["success"] is True

        after = client.post(
            "/api/auth/refresh",
            headers={"Origin": ADMIN_PORTAL_ORIGIN, "Cookie": f"refreshToken={refresh2}"},
        )
        assert after.status_code == 401
    finally:
        client.cookies.clear()
        _delete_user(email)


def test_cors_preflight_login_from_admin_portal():
    res = client.options(
        "/api/auth/login",
        headers={
            "Origin": ADMIN_PORTAL_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )
    assert res.status_code in (200, 204)
    assert res.headers.get("access-control-allow-origin") == ADMIN_PORTAL_ORIGIN
    allow_headers = (res.headers.get("access-control-allow-headers") or "").lower()
    assert "content-type" in allow_headers
    allow_methods = (res.headers.get("access-control-allow-methods") or "").upper()
    assert "POST" in allow_methods
    assert res.headers.get("access-control-allow-credentials") in ("true", "True", True)


def test_cors_preflight_refresh_from_admin_portal():
    res = client.options(
        "/api/auth/refresh",
        headers={
            "Origin": ADMIN_PORTAL_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert res.status_code in (200, 204)
    assert res.headers.get("access-control-allow-origin") == ADMIN_PORTAL_ORIGIN
    assert res.headers.get("access-control-allow-credentials") in ("true", "True", True)


def test_production_admin_password_env_is_not_login_bypass(monkeypatch):
    """ADMIN_PASSWORD must not authenticate when ENVIRONMENT=production."""
    email = _unique_email("portal.nobypass")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOW_ADMIN_BOOTSTRAP", "true")
    monkeypatch.setenv("ADMIN_EMAIL", email)
    monkeypatch.setenv("ADMIN_PASSWORD", "EnvPlaintextShouldNeverWork99!")
    # Force settings re-read is not trivial; login reads os.getenv + settings.is_production.
    # Patch is_production on the settings singleton used by auth_router.
    monkeypatch.setattr(settings, "ENVIRONMENT", "production", raising=False)

    res = client.post(
        "/api/auth/login",
        json={"email": email, "password": "EnvPlaintextShouldNeverWork99!"},
    )
    assert res.status_code == 401
    # Ensure user was NOT created via bootstrap
    db = SessionLocal()
    try:
        exists = db.scalar(select(UserAuth).where(UserAuth.email == email.lower()))
        assert exists is None
    finally:
        db.close()


def test_ensure_super_admin_script_hashes_with_auth_service(monkeypatch):
    from app.scripts.ensure_super_admin import ensure_super_admin

    email = _unique_email("portal.ensure")
    password = f"InitPass-{uuid.uuid4().hex[:8]}!"
    try:
        code = ensure_super_admin(email, password, create=True, reset_password=False)
        assert code == 0
        db = SessionLocal()
        try:
            user = db.scalar(select(UserAuth).where(UserAuth.email == email.lower()))
            assert user is not None
            assert user.role == Role.SUPER_ADMIN
            assert str(user.password_hash).startswith("$2")
            assert AuthService.verify_password(password, user.password_hash)
            assert not AuthService.verify_password("wrong", user.password_hash)
        finally:
            db.close()

        login = client.post("/api/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200
    finally:
        _delete_user(email)
