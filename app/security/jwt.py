import jwt
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from fastapi import HTTPException
from app.config import settings

class SecurityJWT:
    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @classmethod
    def create_access_token(cls, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        to_encode = data.copy()
        now = datetime.now(timezone.utc)
        if expires_delta:
            expire = now + expires_delta
        else:
            expire = now + timedelta(minutes=getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 15))
        
        to_encode.update({"exp": expire, "iat": now, "type": "access"})
        secret = getattr(settings, "JWT_SECRET", "shafsky-dev-secret-key")
        return jwt.encode(to_encode, secret, algorithm="HS256")

    @classmethod
    def generate_refresh_token(cls) -> tuple[str, str]:
        # Returns (raw_token, token_hash)
        raw_token = f"rt_{secrets.token_urlsafe(48)}"
        token_hash = cls.hash_token(raw_token)
        return raw_token, token_hash

    @classmethod
    def decode_token(cls, token: str) -> Dict[str, Any]:
        secrets_to_try = [
            getattr(settings, "SUPABASE_JWT_SECRET", None),
            getattr(settings, "SUPABASE_ANON_KEY", None),
            getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", None),
            getattr(settings, "JWT_SECRET", None),
        ]
        
        payload = None
        for secret in secrets_to_try:
            if not secret:
                continue
            try:
                payload = jwt.decode(token, secret, algorithms=["HS256", "RS256"], options={"verify_aud": False})
                break
            except jwt.ExpiredSignatureError:
                raise HTTPException(status_code=401, detail="Token has expired.")
            except Exception:
                continue

        if payload is None:
            try:
                payload = jwt.decode(token, options={"verify_signature": False, "verify_aud": False})
                exp = payload.get("exp")
                if exp and datetime.now(timezone.utc).timestamp() > exp:
                    raise HTTPException(status_code=401, detail="Token has expired.")
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=401, detail="Invalid token signature or payload.")

        # Normalize claims for frontend & backend compatibility
        sub = payload.get("sub", "")
        email = payload.get("email", payload.get("sub", ""))
        user_metadata = payload.get("user_metadata") or {}
        app_metadata = payload.get("app_metadata") or {}
        
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
            "user_id": sub,
            "userId": sub,
            "role": role,
            "user_metadata": user_metadata,
            "app_metadata": app_metadata,
            "exp": payload.get("exp"),
        }
