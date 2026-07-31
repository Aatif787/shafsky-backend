"""
Unit and Integration Tests for Milestone A1: RS256 JWT Infrastructure Upgrade.
"""

import sys
import os
import jwt
import pytest
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.security.keys import (
    get_jwt_private_key,
    get_jwt_public_key,
    generate_rsa_key_pair,
    reset_key_cache
)
from app.security.jwt import SecurityJWT
from app.security.secrets import validate_secrets_on_startup
from app.config import settings


def test_01_rsa_key_generation_and_loading():
    """Verify that RSA key infrastructure loads valid PEM keys."""
    reset_key_cache()
    priv_key = get_jwt_private_key()
    pub_key = get_jwt_public_key()

    assert priv_key is not None
    assert pub_key is not None
    assert "BEGIN RSA PRIVATE KEY" in priv_key or "BEGIN PRIVATE KEY" in priv_key
    assert "BEGIN PUBLIC KEY" in pub_key


def test_02_create_and_decode_rs256_token():
    """Verify RS256 token creation and decoding."""
    data = {
        "sub": "pilot@shafskyaviation.com",
        "role": "SUPER_ADMIN",
        "user_id": "user_12345"
    }
    token = SecurityJWT.create_access_token(data)
    assert token is not None

    # Inspect header to confirm RS256 algorithm
    unverified_header = jwt.get_unverified_header(token)
    assert unverified_header["alg"] == "RS256"

    # Decode and verify payload
    decoded = SecurityJWT.decode_token(token)
    assert decoded["email"] == "pilot@shafskyaviation.com"
    assert decoded["role"] == "SUPER_ADMIN"
    assert decoded["user_id"] == "user_12345"


def test_03_tampered_rs256_token_rejected():
    """Verify that a tampered RS256 token is rejected with HTTP 401."""
    data = {"sub": "pilot@shafskyaviation.com", "role": "CUSTOMER"}
    token = SecurityJWT.create_access_token(data)

    # Tamper with token signature
    parts = token.split(".")
    tampered_signature = parts[2][:-4] + "ABCD"
    tampered_token = f"{parts[0]}.{parts[1]}.{tampered_signature}"

    with pytest.raises(HTTPException) as exc_info:
        SecurityJWT.decode_token(tampered_token)
    assert exc_info.value.status_code == 401


def test_04_expired_rs256_token_rejected():
    """Verify that an expired RS256 token is rejected with HTTP 401."""
    data = {"sub": "pilot@shafskyaviation.com", "role": "CUSTOMER"}
    # Token expired 10 minutes ago
    expired_token = SecurityJWT.create_access_token(data, expires_delta=timedelta(minutes=-10))

    with pytest.raises(HTTPException) as exc_info:
        SecurityJWT.decode_token(expired_token)
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


def test_05_hs256_backward_compatibility():
    """Verify transition window backward compatibility for legacy HS256 tokens."""
    now = datetime.now(timezone.utc)
    legacy_payload = {
        "sub": "legacy_user@shafskyaviation.com",
        "role": "CUSTOMER",
        "exp": now + timedelta(minutes=15),
        "iat": now,
        "type": "access"
    }
    legacy_secret = getattr(settings, "JWT_SECRET", "shafsky-dev-secret-key-change-in-prod")
    legacy_token = jwt.encode(legacy_payload, legacy_secret, algorithm="HS256")

    # Inspect header
    unverified_header = jwt.get_unverified_header(legacy_token)
    assert unverified_header["alg"] == "HS256"

    # Decode using SecurityJWT.decode_token
    decoded = SecurityJWT.decode_token(legacy_token)
    assert decoded["email"] == "legacy_user@shafskyaviation.com"
    assert decoded["role"] == "CUSTOMER"


def test_06_startup_secrets_validation():
    """Verify that startup secrets validation passes cleanly with RSA keys initialized."""
    validate_secrets_on_startup()


if __name__ == "__main__":
    test_01_rsa_key_generation_and_loading()
    test_02_create_and_decode_rs256_token()
    test_03_tampered_rs256_token_rejected()
    test_04_expired_rs256_token_rejected()
    test_05_hs256_backward_compatibility()
    test_06_startup_secrets_validation()
    print("ALL MILESTONE A1 RS256 TESTS PASSED 100%!")
