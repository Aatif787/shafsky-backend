"""
Startup Secrets and Key Infrastructure Validator.

Performs fail-fast validation of critical database credentials, secrets, and
RSA JWT signing keys on application launch.
"""

import logging
from app.config import settings
from app.security.keys import get_jwt_private_key, get_jwt_public_key, get_jwt_key_id

logger = logging.getLogger("shafsky.security.secrets")


def validate_secrets_on_startup():
    """
    Validate critical secrets and RSA key infrastructure on startup.
    In non-development modes (e.g. production, staging), raises ValueError on missing or insecure credentials.
    """
    env = getattr(settings, "ENVIRONMENT", "development").lower()
    critical_secrets = [
        ("DATABASE_URL", str(settings.DATABASE_URL)),
    ]

    # Add known critical secrets to the list
    critical_secrets.extend([
        ("JWT_PRIVATE_KEY", str(settings.JWT_PRIVATE_KEY)),
        ("JWT_PUBLIC_KEY", str(settings.JWT_PUBLIC_KEY)),
        ("JWT_REFRESH_SECRET", str(settings.JWT_REFRESH_SECRET)),
    ])
    if getattr(settings, "ALLOW_HS256_LEGACY_FALLBACK", False) or getattr(settings, "JWT_ALGORITHM", "RS256").upper() == "HS256":
        critical_secrets.append(("JWT_SECRET", str(settings.JWT_SECRET)))

    missing = []
    for name, value in critical_secrets:
        if not value or value.strip() == "" or "change-this" in value.lower() or "secret" == value.lower():
            missing.append(name)

    # Validate RSA Key loading and key ID calculation
    rsa_keys_ok = False
    active_kid = "unknown"
    try:
        priv = get_jwt_private_key()
        pub = get_jwt_public_key()
        if priv and pub and "BEGIN" in priv and "BEGIN" in pub:
            rsa_keys_ok = True
            active_kid = get_jwt_key_id()
        else:
            missing.append("JWT_RSA_KEYS")
    except Exception as err:
        logger.error(f"Failed to load RSA key pair: {err}")
        missing.append(f"JWT_RSA_KEYS ({err})")

    if missing:
        error_msg = f"CRITICAL SECURITY FAIL-FAST: Missing or insecure configuration for: {', '.join(missing)}"
        logger.critical(error_msg)
        if env not in ["development", "dev", "test", "testing"]:
            raise ValueError(error_msg)
        else:
            logger.warning(f"Development mode warning: {error_msg}")
    else:
        # Additional checks: Disallow legacy HS256 fallback in production
        allow_legacy = getattr(settings, "ALLOW_HS256_LEGACY_FALLBACK", False)
        if allow_legacy and env not in ["development", "dev", "test", "testing"]:
            raise ValueError("ALLOW_HS256_LEGACY_FALLBACK must be disabled in non-development environments.")

        # Ensure algorithm defaults to RS256 unless explicit override for testing
        alg = getattr(settings, "JWT_ALGORITHM", "RS256")
        if alg.upper() != "RS256" and env not in ["development", "dev", "test", "testing"]:
            raise ValueError("JWT_ALGORITHM must be RS256 in production environments.")

        logger.info(f"Startup secrets and RSA key infrastructure (Key ID: {active_kid}) validation passed successfully.")
