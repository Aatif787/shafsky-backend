"""
Security JWT Module for Enterprise RS256 Token Signing, Decoding, and Verification.

Supports RS256 asymmetric signing with kid (Key ID) header inclusion, multi-key
public verification registries for zero-downtime key rotation, and backward-compatible
legacy token decoding.
"""

import jwt
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple
from fastapi import HTTPException
from app.config import settings
from app.security.keys import (
    get_jwt_private_key,
    get_jwt_public_key,
    get_jwt_key_id,
    get_all_verification_public_keys
)


class SecurityJWT:
    @staticmethod
    def hash_token(raw_token: str) -> str:
        """Computes SHA-256 hash of raw token string."""
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @classmethod
    def create_access_token(cls, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create RS256 signed access token using loaded RSA Private Key
        and attaching the active Key ID (kid) in the JWT header.
        """
        to_encode = data.copy()
        now = datetime.now(timezone.utc)
        if expires_delta:
            expire = now + expires_delta
        else:
            expire = now + timedelta(minutes=getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 15))

        to_encode.update({"exp": expire, "iat": now, "type": "access"})
        private_key = get_jwt_private_key()
        key_id = get_jwt_key_id()
        algorithm = getattr(settings, "JWT_ALGORITHM", "RS256")

        headers = {"kid": key_id}
        return jwt.encode(to_encode, private_key, algorithm=algorithm, headers=headers)

    @classmethod
    def generate_refresh_token(cls) -> Tuple[str, str]:
        """
        Generates raw refresh token and its SHA-256 hash.
        Returns (raw_token, token_hash)
        """
        raw_token = f"rt_{secrets.token_urlsafe(48)}"
        token_hash = cls.hash_token(raw_token)
        return raw_token, token_hash

    @classmethod
    def decode_token(cls, token: str) -> Dict[str, Any]:
        """
        Decode and verify JWT token.
        Inspects 'kid' header if present and verifies token against active public key
        and all rotated previous public keys in the verification registry.
        """
        payload = None

        # Extract 'kid' header if present
        token_kid = None
        try:
            unverified_header = jwt.get_unverified_header(token)
            token_kid = unverified_header.get("kid")
        except Exception:
            token_kid = None

        # Build prioritized list of verification public keys
        all_public_keys = get_all_verification_public_keys()
        keys_to_try = []

        if token_kid and token_kid in all_public_keys:
            keys_to_try.append(all_public_keys[token_kid])

        for pkid, pub_pem in all_public_keys.items():
            if pub_pem not in keys_to_try:
                keys_to_try.append(pub_pem)

        # 1. Verification with RSA Public Keys
        for public_key in keys_to_try:
            try:
                payload = jwt.decode(
                    token,
                    public_key,
                    algorithms=["RS256"],
                    options={"verify_aud": False}
                )
                if payload:
                    return payload
            except jwt.ExpiredSignatureError:
                raise HTTPException(status_code=401, detail="Token has expired.")
            except (jwt.InvalidSignatureError, jwt.DecodeError, jwt.InvalidAlgorithmError):
                continue
            except Exception:
                continue

        # 2. Backward Compatibility: Legacy HS256 key candidate fallback
        if payload is None:
            legacy_secret = getattr(settings, "JWT_SECRET", None)
            if legacy_secret:
                try:
                    payload = jwt.decode(
                        token,
                        legacy_secret,
                        algorithms=["HS256", "RS256"],
                        options={"verify_aud": False}
                    )
                    if payload:
                        return payload
                except jwt.ExpiredSignatureError:
                    raise HTTPException(status_code=401, detail="Token has expired.")
                except Exception:
                    pass

        # 3. Expiration Check if signature verification failed
        if payload is None:
            try:
                unverified_payload = jwt.decode(token, options={"verify_signature": False, "verify_aud": False})
                exp = unverified_payload.get("exp")
                if exp and datetime.now(timezone.utc).timestamp() > exp:
                    raise HTTPException(status_code=401, detail="Token has expired.")
            except Exception:
                pass
            raise HTTPException(status_code=401, detail="Could not validate credentials.")

        return payload
