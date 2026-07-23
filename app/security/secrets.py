import os
import sys
import logging
from app.config import settings

logger = logging.getLogger("shafsky.security.secrets")

def validate_secrets_on_startup():
    critical_secrets = [
        ("JWT_SECRET", settings.JWT_SECRET),
        ("DATABASE_URL", str(settings.DATABASE_URL)),
    ]

    missing = []
    for name, value in critical_secrets:
        if not value or value.strip() == "" or "change-this" in value.lower() or "secret" == value.lower():
            missing.append(name)

    if missing:
        error_msg = f"CRITICAL SECURITY FAIL-FAST: Missing or insecure configuration for: {', '.join(missing)}"
        logger.critical(error_msg)
        # Fail fast in production
        if os.getenv("ENVIRONMENT", "development").lower() == "production":
            raise ValueError(error_msg)
        else:
            logger.warning(f"Development mode warning: {error_msg}")
    else:
        logger.info("Startup secrets validation passed successfully.")
