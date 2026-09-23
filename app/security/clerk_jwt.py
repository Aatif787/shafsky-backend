"""
Verify Clerk session JWTs with JWKS.

Identity claims come only from the verified token (or, when email is absent,
from Clerk's Backend API looked up by the verified subject). Callers must not
trust email, role, or user id supplied by the client.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import jwt
from fastapi import HTTPException
from jwt import PyJWKClient

from app.config import settings

logger = logging.getLogger("shafsky.security.clerk_jwt")

_jwks_client: Optional[PyJWKClient] = None
_jwks_client_url: Optional[str] = None


@dataclass(frozen=True)
class ClerkIdentity:
    sub: str
    email: Optional[str]
    email_verified: bool
    full_name: Optional[str]


def clerk_jwks_url() -> str:
    explicit = (settings.CLERK_JWKS_URL or "").strip()
    if explicit:
        return explicit
    issuer = (settings.CLERK_ISSUER or "").strip().rstrip("/")
    if not issuer:
        return ""
    return f"{issuer}/.well-known/jwks.json"


def _jwks() -> PyJWKClient:
    """Reuse one PyJWKClient so signing keys are cached across requests."""
    global _jwks_client, _jwks_client_url
    url = clerk_jwks_url()
    if not url:
        raise HTTPException(status_code=500, detail="Clerk authentication is not configured.")
    if _jwks_client is None or _jwks_client_url != url:
        _jwks_client = PyJWKClient(url, cache_keys=True, lifespan=3600)
        _jwks_client_url = url
    return _jwks_client


def _signing_key_for_token(token: str):
    return _jwks().get_signing_key_from_jwt(token).key


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "verified")
    return False


def _email_from_claims(payload: dict) -> tuple[Optional[str], bool, Optional[str]]:
    email = payload.get("email") or payload.get("primary_email_address")
    if isinstance(email, str):
        email = email.strip().lower() or None
    else:
        email = None

    if "email_verified" in payload:
        verified = _as_bool(payload.get("email_verified"))
    else:
        verified = False

    name = payload.get("name") or payload.get("full_name")
    if not isinstance(name, str) or not name.strip():
        given = payload.get("given_name") or payload.get("first_name") or ""
        family = payload.get("family_name") or payload.get("last_name") or ""
        combined = f"{given} {family}".strip()
        name = combined or None
    else:
        name = name.strip()
    return email, verified, name


def _email_from_clerk_api(clerk_user_id: str) -> tuple[Optional[str], bool, Optional[str]]:
    secret = (settings.CLERK_SECRET_KEY or "").strip()
    if not secret:
        return None, False, None
    url = f"https://api.clerk.com/v1/users/{clerk_user_id}"
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {secret}"},
            timeout=5.0,
        )
    except httpx.HTTPError:
        logger.warning("Clerk user lookup failed for subject %s", clerk_user_id)
        raise HTTPException(status_code=401, detail="Invalid or expired Clerk token.")

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired Clerk token.")

    body = response.json()
    if not isinstance(body, dict) or body.get("id") != clerk_user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired Clerk token.")

    primary_id = body.get("primary_email_address_id")
    addresses = body.get("email_addresses") or []
    chosen = None
    for item in addresses:
        if isinstance(item, dict) and item.get("id") == primary_id:
            chosen = item
            break
    if chosen is None and addresses and isinstance(addresses[0], dict):
        chosen = addresses[0]

    email = None
    verified = False
    if isinstance(chosen, dict):
        raw = chosen.get("email_address")
        if isinstance(raw, str) and raw.strip():
            email = raw.strip().lower()
        verification = chosen.get("verification") or {}
        status = verification.get("status") if isinstance(verification, dict) else None
        verified = status == "verified"

    first = body.get("first_name") or ""
    last = body.get("last_name") or ""
    full_name = f"{first} {last}".strip() or None
    return email, verified, full_name


def verify_clerk_token(token: str) -> ClerkIdentity:
    """Verify signature, expiry, issuer, and audience/azp when configured."""
    raw = (token or "").strip()
    if not raw or raw.count(".") != 2:
        raise HTTPException(status_code=401, detail="Invalid or expired Clerk token.")

    issuer = (settings.CLERK_ISSUER or "").strip().rstrip("/")
    if not issuer:
        raise HTTPException(status_code=500, detail="Clerk authentication is not configured.")

    audience = (settings.CLERK_AUDIENCE or "").strip()
    decode_kwargs: dict[str, Any] = {
        "algorithms": ["RS256"],
        "issuer": issuer,
        "options": {
            "verify_signature": True,
            "verify_exp": True,
            "verify_iss": True,
            "verify_aud": bool(audience),
        },
    }
    if audience:
        decode_kwargs["audience"] = audience

    try:
        signing_key = _signing_key_for_token(raw)
        payload = jwt.decode(raw, signing_key, **decode_kwargs)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Invalid or expired Clerk token.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired Clerk token.")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Clerk JWKS verification failed")
        raise HTTPException(status_code=401, detail="Invalid or expired Clerk token.")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Invalid or expired Clerk token.")

    parties = [
        item.strip()
        for item in (settings.CLERK_AUTHORIZED_PARTIES or "").split(",")
        if item.strip()
    ]
    if parties:
        azp = payload.get("azp")
        if not isinstance(azp, str) or azp not in parties:
            raise HTTPException(status_code=401, detail="Invalid or expired Clerk token.")

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise HTTPException(status_code=401, detail="Invalid or expired Clerk token.")
    subject = subject.strip()

    email, verified, full_name = _email_from_claims(payload)
    if not email:
        api_email, api_verified, api_name = _email_from_clerk_api(subject)
        email = api_email
        verified = api_verified
        full_name = full_name or api_name

    return ClerkIdentity(
        sub=subject,
        email=email,
        email_verified=verified,
        full_name=full_name,
    )
