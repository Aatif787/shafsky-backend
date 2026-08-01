"""
Unit & Integration Test Suite for Enterprise JWT Key Management & Rotation Infrastructure.
"""

import pytest
import jwt
from unittest import mock
from app.config import settings
from app.security.keys import (
    get_jwt_private_key,
    get_jwt_public_key,
    get_jwt_key_id,
    get_all_verification_public_keys,
    generate_rsa_key_pair,
    compute_key_id,
    reset_key_cache
)
from app.security.jwt import SecurityJWT
from app.security.secrets import validate_secrets_on_startup


@pytest.fixture(autouse=True)
def cleanup_keys():
    reset_key_cache()
    yield
    reset_key_cache()


def test_01_development_mode_ephemeral_key_generation():
    """Verify that development mode allows generating ephemeral RSA keys with logging warning."""
    reset_key_cache()
    with mock.patch.object(settings, "ENVIRONMENT", "development"), \
         mock.patch.object(settings, "JWT_PRIVATE_KEY", ""), \
         mock.patch.object(settings, "JWT_PUBLIC_KEY", ""):
        priv = get_jwt_private_key()
        pub = get_jwt_public_key()
        assert priv is not None and ("BEGIN RSA PRIVATE KEY" in priv or "BEGIN PRIVATE KEY" in priv)
        assert pub is not None and "BEGIN PUBLIC KEY" in pub
        kid = get_jwt_key_id()
        assert kid.startswith("kid_")


def test_02_production_mode_missing_keys_rejection():
    """Verify that production mode raises ValueError on startup if explicit RSA keys are missing."""
    reset_key_cache()
    with mock.patch.object(settings, "ENVIRONMENT", "production"), \
         mock.patch.object(settings, "JWT_PRIVATE_KEY", ""), \
         mock.patch.object(settings, "JWT_PUBLIC_KEY", ""):
        with pytest.raises(ValueError) as exc_info:
            get_jwt_private_key()
        assert "CRITICAL PRODUCTION SECURITY ERROR" in str(exc_info.value)


def test_03_jwt_kid_header_injection():
    """Verify that issued access tokens include the kid (Key ID) parameter in their JWT header."""
    reset_key_cache()
    with mock.patch.object(settings, "ENVIRONMENT", "development"):
        token = SecurityJWT.create_access_token({"sub": "user_123", "role": "ADMIN"})
        header = jwt.get_unverified_header(token)
        assert "kid" in header
        assert header["kid"] == get_jwt_key_id()
        assert header["alg"] == "RS256"

        payload = SecurityJWT.decode_token(token)
        assert payload["sub"] == "user_123"
        assert payload["role"] == "ADMIN"


def test_04_multi_key_rotation_and_verification():
    """Verify zero-downtime key rotation: tokens signed with a previous RSA private key verify cleanly."""
    reset_key_cache()
    # 1. Generate old/previous RSA key pair
    old_priv, old_pub = generate_rsa_key_pair()
    old_kid = compute_key_id(old_pub)

    # 2. Generate active RSA key pair
    active_priv, active_pub = generate_rsa_key_pair()
    active_kid = compute_key_id(active_pub)

    # Sign a token using the old private key with old_kid in header
    old_token = jwt.encode(
        {"sub": "rotated_user", "role": "USER", "type": "access"},
        old_priv,
        algorithm="RS256",
        headers={"kid": old_kid}
    )

    # Configure active keys and previous public keys in settings
    with mock.patch.object(settings, "ENVIRONMENT", "development"), \
         mock.patch.object(settings, "JWT_PRIVATE_KEY", active_priv), \
         mock.patch.object(settings, "JWT_PUBLIC_KEY", active_pub), \
         mock.patch.object(settings, "JWT_PREVIOUS_PUBLIC_KEYS", old_pub):
        reset_key_cache()
        registry = get_all_verification_public_keys()
        assert active_kid in registry
        assert old_kid in registry

        # Decode token signed with old key - must pass seamlessly!
        decoded = SecurityJWT.decode_token(old_token)
        assert decoded["sub"] == "rotated_user"
        assert decoded["role"] == "USER"


def test_05_startup_secrets_validation():
    """Verify startup fail-fast secret validation in dev vs production environments."""
    reset_key_cache()
    with mock.patch.object(settings, "ENVIRONMENT", "development"):
        validate_secrets_on_startup()  # Dev mode completes without error

    reset_key_cache()
    with mock.patch.object(settings, "ENVIRONMENT", "production"), \
         mock.patch.object(settings, "JWT_PRIVATE_KEY", ""), \
         mock.patch.object(settings, "JWT_PUBLIC_KEY", ""):
        with pytest.raises(ValueError) as exc_info:
            validate_secrets_on_startup()
        assert "CRITICAL" in str(exc_info.value)
