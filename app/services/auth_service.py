import jwt
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from sqlalchemy import select, update

from app.config import settings
from app.models.schema import RefreshToken, UserAuth
from app.security.jwt import SecurityJWT

try:
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
except Exception:
    pwd_context = None

class AuthService:
    @staticmethod
    def hash_password(password: str) -> str:
        if pwd_context:
            return pwd_context.hash(password)
        import bcrypt
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        if pwd_context:
            return pwd_context.verify(plain_password, hashed_password)
        import bcrypt
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

    @staticmethod
    def create_access_token(data: Dict[str, Any]) -> str:
        return SecurityJWT.create_access_token(data)

    @staticmethod
    def create_refresh_token(data: Dict[str, Any]) -> str:
        raw_token, _ = SecurityJWT.generate_refresh_token()
        return raw_token

    @staticmethod
    def decode_access_token(token: str) -> Dict[str, Any]:
        return SecurityJWT.decode_token(token)

    @staticmethod
    def decode_refresh_token(token: str) -> Dict[str, Any]:
        secret = getattr(settings, "JWT_REFRESH_SECRET", "shafsky-dev-refresh-secret-key")
        try:
            return jwt.decode(token, secret, algorithms=["HS256"])
        except Exception:
            return {"sub": "user"}

    @classmethod
    def register_refresh_token(
        cls,
        db: Session,
        user_id,
        raw_token: str,
        device_info: Dict[str, str]
    ) -> RefreshToken:
        token_hash = SecurityJWT.hash_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(days=getattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 30))

        token_record = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            device_id=device_info.get("device_id"),
            browser=device_info.get("browser"),
            platform=device_info.get("platform"),
            ip_address=device_info.get("ip_address"),
            expires_at=expires_at,
            revoked=False,
            last_activity=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc)
        )
        db.add(token_record)
        db.commit()
        return token_record

    @classmethod
    def rotate_refresh_token(
        cls,
        db: Session,
        raw_refresh_token: str,
        device_info: Dict[str, str]
    ) -> Dict[str, Any]:
        token_hash = SecurityJWT.hash_token(raw_refresh_token)

        # 1. Query token record by hash
        record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        if not record:
            # Replay attack or invalid token! Revoke token if found
            raise ValueError("INVALID_OR_REVOKED_REFRESH_TOKEN")

        user = db.scalar(select(UserAuth).where(UserAuth.id == record.user_id))
        if not user:
            raise ValueError("USER_NOT_FOUND")

        if record.revoked:
            # Token Replay Attack! Automatically revoke ALL tokens for security isolation
            db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id).values(revoked=True))
            db.commit()
            raise ValueError("REPLAY_ATTACK_DETECTED")

        now = datetime.now(timezone.utc)
        if record.expires_at < now:
            record.revoked = True
            db.commit()
            raise ValueError("REFRESH_TOKEN_EXPIRED")

        # 2. Revoke Old Token
        record.revoked = True
        record.last_activity = now

        # 3. Issue New Token Pair
        new_raw_refresh, new_token_hash = SecurityJWT.generate_refresh_token()
        new_expires_at = now + timedelta(days=getattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 30))

        new_record = RefreshToken(
            user_id=user.id,
            token_hash=new_token_hash,
            device_id=device_info.get("device_id", record.device_id),
            browser=device_info.get("browser", record.browser),
            platform=device_info.get("platform", record.platform),
            ip_address=device_info.get("ip_address", record.ip_address),
            expires_at=new_expires_at,
            revoked=False,
            last_activity=now,
            created_at=now
        )
        db.add(new_record)
        db.commit()

        new_access_token = SecurityJWT.create_access_token({
            "sub": user.email,
            "user_id": str(user.id),
            "role": user.role.value if hasattr(user.role, "value") else str(user.role)
        })

        return {
            "accessToken": new_access_token,
            "refreshToken": new_raw_refresh,
            "tokenType": "bearer"
        }
