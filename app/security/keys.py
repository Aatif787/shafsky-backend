"""
Centralized Enterprise RSA Key Management Infrastructure for RS256 JWT Signing & Rotation.

Provides loading, caching, fingerprinting (Key ID / kid), and validation of RSA
Private and Public Keys. Enforces strict production key policies and multi-key
verification registries for zero-downtime key rotation.
"""

import os
import base64
import hashlib
import logging
from typing import Optional, Tuple, Dict
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from app.config import settings

logger = logging.getLogger("shafsky.security.keys")

_CACHE_PRIVATE_KEY_PEM: Optional[str] = None
_CACHE_PUBLIC_KEY_PEM: Optional[str] = None
_CACHE_PUBLIC_KEY_REGISTRY: Optional[Dict[str, str]] = None


def _clean_key_input(key_input: str) -> str:
    """Normalize formatted key strings, unescaping newline characters if needed."""
    if not key_input:
        return ""
    key_str = key_input.strip()
    if "\\n" in key_str and "\n" not in key_str:
        key_str = key_str.replace("\\n", "\n")
    return key_str


def _load_pem_from_source(key_source: str) -> Optional[str]:
    """
    Attempt to load a PEM key string from:
    1. Direct PEM string (starts with -----BEGIN...)
    2. File path
    3. Base64-encoded string
    """
    if not key_source:
        return None

    cleaned = _clean_key_input(key_source)

    # 1. Direct PEM string
    if "BEGIN" in cleaned and "KEY" in cleaned:
        return cleaned

    # 2. File path
    if os.path.exists(cleaned):
        try:
            with open(cleaned, "r", encoding="utf-8") as f:
                content = f.read()
                return _clean_key_input(content)
        except Exception as err:
            logger.error(f"Failed to read key file from '{cleaned}': {err}")

    # 3. Base64 decode attempt
    try:
        decoded = base64.b64decode(cleaned).decode("utf-8")
        if "BEGIN" in decoded and "KEY" in decoded:
            return _clean_key_input(decoded)
    except Exception:
        pass

    return None


def compute_key_id(pub_pem: str) -> str:
    """
    Computes a deterministic Key ID (kid) hash for an RSA public key PEM string.
    Returns SHA-256 fingerprint digest prefix.
    """
    if not pub_pem:
        return "kid_unknown"
    normalized = _clean_key_input(pub_pem).strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"kid_{digest}"


def generate_rsa_key_pair() -> Tuple[str, str]:
    """Generate a new 2048-bit RSA key pair returning (private_key_pem, public_key_pem)."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode("utf-8")

    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")

    return private_pem, public_pem


def _ensure_rsa_keys() -> Tuple[str, str]:
    """
    Retrieve or initialize cached active RSA key pair.
    Fails fast in production mode if explicit keys are missing.
    """
    global _CACHE_PRIVATE_KEY_PEM, _CACHE_PUBLIC_KEY_PEM

    if _CACHE_PRIVATE_KEY_PEM and _CACHE_PUBLIC_KEY_PEM:
        return _CACHE_PRIVATE_KEY_PEM, _CACHE_PUBLIC_KEY_PEM

    priv_source = getattr(settings, "JWT_PRIVATE_KEY", "")
    pub_source = getattr(settings, "JWT_PUBLIC_KEY", "")

    priv_pem = _load_pem_from_source(priv_source)
    pub_pem = _load_pem_from_source(pub_source)

    if priv_pem and not pub_pem:
        try:
            loaded_priv = serialization.load_pem_private_key(
                priv_pem.encode("utf-8"),
                password=None,
                backend=default_backend()
            )
            pub_pem = loaded_priv.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode("utf-8")
        except Exception as err:
            logger.error(f"Failed to derive public key from private key: {err}")

    env = getattr(settings, "ENVIRONMENT", "development").lower()

    if not priv_pem or not pub_pem:
        if env not in ["development", "dev", "test", "testing"]:
            err_msg = (
                f"CRITICAL PRODUCTION SECURITY ERROR: RSA Private and Public Keys "
                f"(JWT_PRIVATE_KEY, JWT_PUBLIC_KEY) are mandatory when ENVIRONMENT is set to '{env}'. "
                f"Ephemeral auto-generated keys are strictly forbidden in non-development environments."
            )
            logger.critical(err_msg)
            raise ValueError(err_msg)

        logger.warning(
            f"JWT RSA keys not configured via environment in '{env}' mode. Generating ephemeral RSA 2048-bit key pair."
        )
        priv_pem, pub_pem = generate_rsa_key_pair()

    _CACHE_PRIVATE_KEY_PEM = priv_pem
    _CACHE_PUBLIC_KEY_PEM = pub_pem

    active_kid = compute_key_id(pub_pem)
    logger.info(f"Loaded active RSA key pair with Key ID (kid): {active_kid}")

    return priv_pem, pub_pem


def get_jwt_private_key() -> str:
    """Returns the active RSA private key in PEM format."""
    priv_pem, _ = _ensure_rsa_keys()
    return priv_pem


def get_jwt_public_key() -> str:
    """Returns the active RSA public key in PEM format."""
    _, pub_pem = _ensure_rsa_keys()
    return pub_pem


def get_jwt_key_id() -> str:
    """Returns the Key ID (kid) fingerprint of the active RSA public key."""
    return compute_key_id(get_jwt_public_key())


def get_all_verification_public_keys() -> Dict[str, str]:
    """
    Returns a dictionary mapping Key IDs (kid) to RSA Public Key PEM strings.
    Includes the active public key and any rotated previous public keys
    configured in JWT_PREVIOUS_PUBLIC_KEYS for seamless zero-downtime key rotation.
    """
    global _CACHE_PUBLIC_KEY_REGISTRY

    active_pub = get_jwt_public_key()
    active_kid = compute_key_id(active_pub)

    registry = {active_kid: active_pub}

    raw_prev = getattr(settings, "JWT_PREVIOUS_PUBLIC_KEYS", "")
    if raw_prev and raw_prev.strip():
        candidates = [c.strip() for c in raw_prev.replace("|", ",").split(",") if c.strip()]
        for candidate in candidates:
            prev_pem = _load_pem_from_source(candidate)
            if prev_pem:
                pkid = compute_key_id(prev_pem)
                registry[pkid] = prev_pem

    _CACHE_PUBLIC_KEY_REGISTRY = registry
    return registry


def reset_key_cache() -> None:
    """Reset cached keys and key registry (primarily for unit test isolation)."""
    global _CACHE_PRIVATE_KEY_PEM, _CACHE_PUBLIC_KEY_PEM, _CACHE_PUBLIC_KEY_REGISTRY
    _CACHE_PRIVATE_KEY_PEM = None
    _CACHE_PUBLIC_KEY_PEM = None
    _CACHE_PUBLIC_KEY_REGISTRY = None
