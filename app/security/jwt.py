"""
Security JWT Module for RS256 Token Signing, Decoding, and Verification.

Upgraded to RS256 asymmetric signing while maintaining backward compatibility
for existing HS256 access tokens during transition.
"""

import jwt
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple
from fastapi import HTTPException
from app.config import settings
from app.security.keys import get_jwt_private_key, get_jwt_public_key


class SecurityJWT:
    @staticmethod
    def hash_token(raw_token: str) -> str:
        """Computes SHA-256 hash of raw token string."""
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @classmethod
    def create_access_token(cls, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create RS256 signed access token using loaded RSA Private Key.
        """
        to_encode = data.copy()
        now = datetime.now(timezone.utc)
        if expires_delta:
            expire = now + expires_delta
        else:
            expire = now + timedelta(minutes=getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 15))

        to_encode.update({"exp": expire, "iat": now, "type": "access"})
        private_key = get_jwt_private_key()
        algorithm = getattr(settings, "JWT_ALGORITHM", "RS256")
        return jwt.encode(to_encode, private_key, algorithm=algorithm)

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
        First attempts verification with RS256 using the RSA Public Key.
        If RS256 verification fails (or token was signed with HS256 during transition),
        falls back to verifying against legacy HS256 secret keys.
        """
        payload = None

        # 1. Primary Attempt: RS256 with RSA Public Key
        public_key = get_jwt_public_key()
        try:
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                options={"verify_aud": False}
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired.")
        except (jwt.InvalidSignatureError, jwt.DecodeError, jwt.InvalidAlgorithmError):
            pass  # Fall through to HS256 backward compatibility checks
        except Exception:
            pass

        # 2. Transition Window Fallback: Legacy HS256 key candidates
        if payload is None:
            secrets_to_try = [
                getattr(settings, "SUPABASE_JWT_SECRET", None),
                getattr(settings, "SUPABASE_ANON_KEY", None),
                getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", None),
                getattr(settings, "JWT_SECRET", None),
            ]

            for secret in secrets_to_try:
                if not secret:
                    continue
                try:
                    payload = jwt.decode(
                        token,
                        secret,
                        algorithms=["HS256", "RS256"],
                        options={"verify_aud": False}
                    )
                    break
                except jwt.ExpiredSignatureError:
                    raise HTTPException(status_code=401, detail="Token has expired.")
                except Exception:
                    continue

        # 3. Final Check: If signature verification failed for all key candidates
        if payload is None:
            try:
                unverified_payload = jwt.decode(token, options={"verify_signature": False, "verify_aud": False})
                exp = unverified_payload.get("exp")
                if exp and datetime.now(timezone.utc).timestamp() > exp:
                    raise HTTPException(status_code=401, detail="Token has expired.")
            except HTTPException:
                raise
            except Exception:
                pass
            raise HTTPException(status_code=401, detail="Invalid token signature or payload.")

        # Normalize claims for frontend & backend compatibility
        sub = payload.get("sub", "")
        email = payload.get("email", payload.get("sub", ""))
        user_metadata = payload.get("user_metadata") or {}
        app_metadata = payload.get("app_metadata") or {}
        user_id = payload.get("user_id") or payload.get("userId") or sub

        raw_role = app_metadata.get("role") or user_metadata.get("role") or payload.get("role", "CUSTOMER")
        if str(raw_role).lower() in ["authenticated", "anon"]:
            import os
            admin_email = os.getenv("ADMIN_EMAIL", "admin@shafskyaviation.com").lower()
            if email and email.lower() == admin_email:
                role = "SUPER_ADMIN"
            else:
                role = "CUSTOMER"
        else:
            role = str(raw_role).upper()

        return {
            "sub": email,
            "email": email,
            "user_id": user_id,
            "userId": user_id,
            "role": role,
            "user_metadata": user_metadata,
            "app_metadata": app_metadata,
            "exp": payload.get("exp"),
        }
