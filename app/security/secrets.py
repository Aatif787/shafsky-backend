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
        logger.info(f"Startup secrets and RSA key infrastructure (Key ID: {active_kid}) validation passed successfully.")
