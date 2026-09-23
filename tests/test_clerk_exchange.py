"""Clerk token exchange issues the existing FastAPI session without replacing login."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from app.config import settings
from app.database import SessionLocal, engine, get_db
from app.main import app
from app.models.schema import Profile, Role, UserAuth
from app.security.clerk_jwt import verify_clerk_token
from app.security.jwt import SecurityJWT
from app.services.auth_service import AuthService
from app.services.clerk_exchange_service import access_token_claims, link_decision

client = TestClient(app)

ISSUER = "https://clerk.test"
KID = "clerk-test-key"


def _postgres_available() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _ensure_clerk_column():
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("profiles")}
    if "clerk_id" in columns:
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE profiles ADD COLUMN clerk_id VARCHAR"))
        connection.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_profiles_clerk_id ON profiles (clerk_id)")
        )


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="Isolated Postgres on TEST_DATABASE_URL is not running",
)

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_pem = _private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)
_private_pem = _private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
_other_private = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)


@pytest.fixture(autouse=True)
def clerk_verifier(monkeypatch):
    monkeypatch.setattr(settings, "CLERK_ISSUER", ISSUER)
    monkeypatch.setattr(settings, "CLERK_JWKS_URL", "")
    monkeypatch.setattr(settings, "CLERK_AUDIENCE", "")
    monkeypatch.setattr(settings, "CLERK_AUTHORIZED_PARTIES", "")
    monkeypatch.setattr(settings, "CLERK_SECRET_KEY", "")
    monkeypatch.setattr(
        "app.security.clerk_jwt._signing_key_for_token",
        lambda token: _public_pem,
    )


def _token(sub, email, *, verified=True, role="SUPER_ADMIN", exp_delta=timedelta(minutes=5), key=None, issuer=ISSUER):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "email": email,
        "email_verified": verified,
        "role": role,
        "iss": issuer,
        "iat": now,
        "exp": now + exp_delta,
    }
    return jwt.encode(payload, key or _private_pem, algorithm="RS256", headers={"kid": KID})


def _cleanup(emails):
    db = SessionLocal()
    try:
        for email in emails:
            user = db.scalar(select(UserAuth).where(UserAuth.email == email))
            if not user:
                continue
            db.query(Profile).filter(Profile.auth_id == user.id).delete()
            db.delete(user)
        db.commit()
    finally:
        db.close()


def test_link_decision_matrix():
    assert link_decision(
        clerk_profile_found=True, email="a@example.com", email_verified=False,
        account_found=True, existing_clerk_id="user_a", incoming_clerk_id="user_a",
    ) == "reuse"
    assert link_decision(
        clerk_profile_found=False, email="a@example.com", email_verified=True,
        account_found=True, existing_clerk_id=None, incoming_clerk_id="user_b",
    ) == "link"
    assert link_decision(
        clerk_profile_found=False, email="a@example.com", email_verified=False,
        account_found=True, existing_clerk_id=None, incoming_clerk_id="user_b",
    ) == "refuse_unverified"
    assert link_decision(
        clerk_profile_found=False, email="a@example.com", email_verified=True,
        account_found=True, existing_clerk_id="user_other", incoming_clerk_id="user_b",
    ) == "conflict"
    assert link_decision(
        clerk_profile_found=False, email="new@example.com", email_verified=True,
        account_found=False, existing_clerk_id=None, incoming_clerk_id="user_new",
    ) == "create"
    assert link_decision(
        clerk_profile_found=False, email=None, email_verified=False,
        account_found=False, existing_clerk_id=None, incoming_clerk_id="user_new",
    ) == "missing_email"


def test_application_jwt_uses_internal_uuid_and_database_role():
    user = UserAuth(
        id=uuid.uuid4(),
        email="customer-clerk@example.com",
        password_hash="unused",
        role=Role.CUSTOMER,
    )
    token = AuthService.create_access_token(access_token_claims(user))
    decoded = SecurityJWT.decode_token(token)
    assert decoded["user_id"] == str(user.id)
    assert decoded["sub"] == user.email
    assert decoded["role"] == Role.CUSTOMER.value
    assert decoded["user_id"] != "user_clerk_frontend"

    admin = UserAuth(id=uuid.uuid4(), email="admin-clerk@example.com", password_hash="unused", role=Role.ADMIN)
    admin_token = AuthService.create_access_token(access_token_claims(admin))
    assert SecurityJWT.decode_token(admin_token)["role"] == Role.ADMIN.value

    owner = UserAuth(id=uuid.uuid4(), email="owner-clerk@example.com", password_hash="unused", role=Role.SUPER_ADMIN)
    owner_token = AuthService.create_access_token(access_token_claims(owner))
    assert SecurityJWT.decode_token(owner_token)["role"] == Role.SUPER_ADMIN.value


def test_verified_clerk_token_exposes_subject_not_request_role():
    token = _token("user_verified", "verified-clerk@example.com", verified=True, role="SUPER_ADMIN")
    identity = verify_clerk_token(token)
    assert identity.sub == "user_verified"
    assert identity.email == "verified-clerk@example.com"
    assert identity.email_verified is True
    assert not hasattr(identity, "role")


@pytest.fixture
def disconnected_database():
    """401 paths must not require a database connection."""
    def _gen():
        yield None

    app.dependency_overrides[get_db] = _gen
    yield
    app.dependency_overrides.pop(get_db, None)


def test_invalid_token_is_rejected(disconnected_database):
    response = client.post(
        "/api/auth/clerk-exchange",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401


def test_expired_token_is_rejected(disconnected_database):
    token = _token("user_expired", "expired-clerk@example.com", exp_delta=timedelta(minutes=-5))
    response = client.post(
        "/api/auth/clerk-exchange",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_invalid_signature_is_rejected(disconnected_database):
    token = _token("user_bad_sig", "badsig-clerk@example.com", key=_other_private)
    response = client.post(
        "/api/auth/clerk-exchange",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


@requires_postgres
def test_new_user_gets_customer_role_and_internal_uuid():
    _ensure_clerk_column()
    email = f"new-clerk-{uuid.uuid4().hex[:8]}@example.com"
    clerk_id = f"user_{uuid.uuid4().hex}"
    try:
        token = _token(clerk_id, email, verified=True, role="SUPER_ADMIN")
        response = client.post(
            "/api/auth/clerk-exchange",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "SUPER_ADMIN", "email": "attacker@example.com", "userId": str(uuid.uuid4())},
        )
        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["accessToken"]
        assert body.get("refreshToken") in (None, "")
        assert body["user"]["email"] == email
        assert body["user"]["role"] == Role.CUSTOMER.value
        assert body["user"]["id"] != clerk_id

        decoded = SecurityJWT.decode_token(body["accessToken"])
        assert decoded["user_id"] == body["user"]["id"]
        assert decoded["role"] == Role.CUSTOMER.value
        assert decoded["sub"] == email

        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['accessToken']}"})
        assert me.status_code == 200
        assert me.json()["data"]["user"]["id"] == body["user"]["id"]

        db = SessionLocal()
        profile = db.scalar(select(Profile).where(Profile.clerk_id == clerk_id))
        assert profile is not None
        assert str(profile.auth_id) == body["user"]["id"]
        assert str(profile.id) != clerk_id
        db.close()
    finally:
        _cleanup([email])


@requires_postgres
def test_existing_clerk_id_reuses_internal_user_and_database_role():
    _ensure_clerk_column()
    email = f"admin-clerk-{uuid.uuid4().hex[:8]}@example.com"
    clerk_id = f"user_{uuid.uuid4().hex}"
    db = SessionLocal()
    user = UserAuth(
        email=email,
        password_hash=AuthService.hash_password("not-used-for-clerk"),
        role=Role.ADMIN,
        is_verified=True,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(Profile(auth_id=user.id, email=email, role=Role.ADMIN, clerk_id=clerk_id, full_name="Admin"))
    db.commit()
    user_id = str(user.id)
    db.close()
    try:
        token = _token(clerk_id, "ignored-frontend@example.com", verified=False, role="CUSTOMER")
        response = client.post(
            "/api/auth/clerk-exchange",
            headers={"Authorization": f"Bearer {token}"},
            json={"role": "SUPER_ADMIN"},
        )
        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["user"]["id"] == user_id
        assert body["user"]["role"] == Role.ADMIN.value
        decoded = SecurityJWT.decode_token(body["accessToken"])
        assert decoded["user_id"] == user_id
        assert decoded["role"] == Role.ADMIN.value
    finally:
        _cleanup([email])


@requires_postgres
def test_verified_email_links_existing_profile():
    _ensure_clerk_column()
    email = f"link-clerk-{uuid.uuid4().hex[:8]}@example.com"
    clerk_id = f"user_{uuid.uuid4().hex}"
    db = SessionLocal()
    user = UserAuth(
        email=email,
        password_hash=AuthService.hash_password("existing-password"),
        role=Role.SUPER_ADMIN,
        is_verified=True,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(Profile(auth_id=user.id, email=email, role=Role.SUPER_ADMIN, full_name="Owner"))
    db.commit()
    user_id = str(user.id)
    db.close()
    try:
        token = _token(clerk_id, email, verified=True, role="CUSTOMER")
        response = client.post("/api/auth/clerk-exchange", json={"token": token, "role": "CUSTOMER"})
        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["user"]["id"] == user_id
        assert body["user"]["role"] == Role.SUPER_ADMIN.value
        db = SessionLocal()
        profile = db.scalar(select(Profile).where(Profile.auth_id == uuid.UUID(user_id)))
        assert profile.clerk_id == clerk_id
        db.close()
    finally:
        _cleanup([email])


@requires_postgres
def test_unverified_email_does_not_link():
    _ensure_clerk_column()
    email = f"unverified-clerk-{uuid.uuid4().hex[:8]}@example.com"
    clerk_id = f"user_{uuid.uuid4().hex}"
    db = SessionLocal()
    user = UserAuth(
        email=email,
        password_hash=AuthService.hash_password("existing-password"),
        role=Role.CUSTOMER,
        is_verified=True,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(Profile(auth_id=user.id, email=email, role=Role.CUSTOMER))
    db.commit()
    db.close()
    try:
        token = _token(clerk_id, email, verified=False)
        response = client.post(
            "/api/auth/clerk-exchange",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        db = SessionLocal()
        profile = db.scalar(select(Profile).where(Profile.email == email))
        assert profile.clerk_id is None
        db.close()
    finally:
        _cleanup([email])


@requires_postgres
def test_duplicate_clerk_identity_conflict():
    _ensure_clerk_column()
    email = f"conflict-clerk-{uuid.uuid4().hex[:8]}@example.com"
    first_id = f"user_{uuid.uuid4().hex}"
    second_id = f"user_{uuid.uuid4().hex}"
    db = SessionLocal()
    user = UserAuth(
        email=email,
        password_hash=AuthService.hash_password("existing-password"),
        role=Role.CUSTOMER,
        is_verified=True,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(Profile(auth_id=user.id, email=email, role=Role.CUSTOMER, clerk_id=first_id))
    db.commit()
    db.close()
    try:
        token = _token(second_id, email, verified=True)
        response = client.post(
            "/api/auth/clerk-exchange",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 409
    finally:
        _cleanup([email])


@requires_postgres
def test_inactive_account_is_forbidden():
    _ensure_clerk_column()
    email = f"inactive-clerk-{uuid.uuid4().hex[:8]}@example.com"
    clerk_id = f"user_{uuid.uuid4().hex}"
    db = SessionLocal()
    user = UserAuth(
        email=email,
        password_hash=AuthService.hash_password("existing-password"),
        role=Role.CUSTOMER,
        is_verified=True,
        is_active=False,
    )
    db.add(user)
    db.flush()
    db.add(Profile(auth_id=user.id, email=email, role=Role.CUSTOMER, clerk_id=clerk_id))
    db.commit()
    db.close()
    try:
        token = _token(clerk_id, email, verified=True)
        response = client.post(
            "/api/auth/clerk-exchange",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
    finally:
        _cleanup([email])


@requires_postgres
def test_password_login_refresh_logout_and_me_still_work():
    email = f"password-login-{uuid.uuid4().hex[:8]}@example.com"
    password = "StillWorks2026!"
    db = SessionLocal()
    db.add(
        UserAuth(
            email=email,
            password_hash=AuthService.hash_password(password),
            role=Role.CUSTOMER,
            is_verified=True,
            is_active=True,
        )
    )
    db.commit()
    db.close()
    try:
        login = client.post("/api/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        access = login.json()["data"]["accessToken"]
        assert login.json()["data"].get("refreshToken") in (None, "")

        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert me.status_code == 200
        assert me.json()["data"]["user"]["email"] == email

        refreshed = client.post("/api/auth/refresh")
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["data"]["accessToken"]

        logout = client.post("/api/auth/logout")
        assert logout.status_code == 200
        again = client.post("/api/auth/refresh")
        assert again.status_code == 401
    finally:
        _cleanup([email])
