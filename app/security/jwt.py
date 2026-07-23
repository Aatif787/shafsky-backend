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
        secret = getattr(settings, "JWT_SECRET", "shafsky-dev-secret-key")
        try:
            payload = jwt.decode(token, secret, algorithms=["HS256"])
            return payload
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired.")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token signature.")
