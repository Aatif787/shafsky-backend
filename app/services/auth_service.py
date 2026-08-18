"""
Authentication Service with Refresh Token Rotation (RTR) and Token Family Revocation.
"""

import uuid
import jwt
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from sqlalchemy import select, update

from app.config import settings
from app.models.schema import RefreshToken, UserAuth
from app.security.jwt import SecurityJWT
from fastapi import HTTPException
import logging

logger = logging.getLogger("shafsky.security.auth_service")

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
        logger = logging.getLogger("shafsky.security.auth_service")

        if not token:
            raise ValueError("EMPTY_REFRESH_TOKEN")

        # Refresh tokens in this system are opaque values prefixed with 'rt_'.
        # Prefer opaque token validation via DB lookup. Legacy JWT-based refresh tokens
        # are only accepted when explicitly enabled via ALLOW_HS256_LEGACY_FALLBACK.
        if token.startswith("rt_"):
            return {"token": token}

        # Legacy path: attempt HS256 decode only if explicitly allowed
        allow_legacy = getattr(settings, "ALLOW_HS256_LEGACY_FALLBACK", False)
        secret = getattr(settings, "JWT_REFRESH_SECRET", None)
        if not allow_legacy:
            logger.warning("Received non-opaque refresh token while legacy HS256 fallback is disabled.")
            raise ValueError("INVALID_REFRESH_TOKEN_FORMAT")

        if not secret:
            logger.error("Legacy HS256 refresh token received but JWT_REFRESH_SECRET is not configured.")
            raise ValueError("REFRESH_SECRET_MISSING")

        try:
            payload = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_aud": False})
            return payload
        except jwt.ExpiredSignatureError:
            raise ValueError("REFRESH_TOKEN_EXPIRED")
        except Exception as exc:
            logger.exception("Failed to decode legacy refresh token: %s", exc)
            raise ValueError("INVALID_REFRESH_TOKEN")

    @classmethod
    def register_refresh_token(
        cls,
        db: Session,
        user_id,
        raw_token: str,
        device_info: Dict[str, str],
        family_id: Optional[uuid.UUID] = None
    ) -> RefreshToken:
        token_hash = SecurityJWT.hash_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(days=getattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 30))
        if family_id is None:
            family_id = uuid.uuid4()

        token_record = RefreshToken(
            user_id=user_id,
            family_id=family_id,
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
        try:
            # Use a transaction to ensure atomicity and automatic rollback on failure
            with db.begin():
                db.add(token_record)
        except Exception:
            db.rollback()
            raise
        return token_record

    @classmethod
    def rotate_refresh_token(
        cls,
        db: Session,
        raw_refresh_token: str,
        device_info: Dict[str, str]
    ) -> Dict[str, Any]:
        if not raw_refresh_token:
            raise ValueError("INVALID_OR_REVOKED_REFRESH_TOKEN")

        token_hash = SecurityJWT.hash_token(raw_refresh_token)

        # 1. Query token record by hash
        record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        if not record:
            raise ValueError("INVALID_OR_REVOKED_REFRESH_TOKEN")

        user = db.scalar(select(UserAuth).where(UserAuth.id == record.user_id))
        if not user:
            raise ValueError("USER_NOT_FOUND")

        # 2. Replay Attack Detection & Token Family Revocation
        if record.revoked:
            logger.warning(
                f"REPLAY ATTACK DETECTED for user {user.email} (token_hash={token_hash[:8]}...). "
                f"Revoking token family {record.family_id} and all active user sessions."
            )
            # Token Family Revocation: Invalidate all tokens sharing the same family_id
            try:
                with db.begin():
                    if record.family_id:
                        db.execute(
                            update(RefreshToken)
                            .where(RefreshToken.family_id == record.family_id)
                            .values(revoked=True)
                        )
                    # Comprehensive fallback: Revoke all tokens for the user
                    db.execute(
                        update(RefreshToken)
                        .where(RefreshToken.user_id == user.id)
                        .values(revoked=True)
                    )
            except Exception:
                db.rollback()
                raise
            raise ValueError("REPLAY_ATTACK_DETECTED")

        # 3. Check Expiration
        now = datetime.now(timezone.utc)
        if record.expires_at < now:
            record.revoked = True
            try:
                with db.begin():
                    db.add(record)
            except Exception:
                db.rollback()
                raise
            raise ValueError("REFRESH_TOKEN_EXPIRED")

        # 4. Atomically Revoke Old Token (Single-Use Policy)
        # Atomically revoke old token and create new token record
        record.revoked = True
        record.last_activity = now

        # 5. Issue New Refresh Token in Same Token Family
        new_raw_refresh, new_token_hash = SecurityJWT.generate_refresh_token()
        new_expires_at = now + timedelta(days=getattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 30))
        family_id = record.family_id or uuid.uuid4()

        new_record = RefreshToken(
            user_id=user.id,
            family_id=family_id,
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
        try:
            with db.begin():
                db.add(record)
                db.add(new_record)
        except Exception:
            db.rollback()
            raise

        # 6. Issue New Access Token
        new_access_token = SecurityJWT.create_access_token({
            "sub": user.email,
            "user_id": str(user.id),
            "role": user.role.value if hasattr(user.role, "value") else str(user.role)
        })

        return {
            "accessToken": new_access_token,
            "refreshToken": new_raw_refresh,
            "tokenType": "bearer",
            "familyId": str(family_id)
        }

    @classmethod
    def revoke_refresh_token(cls, db: Session, raw_refresh_token: str) -> bool:
        """Revoke a single refresh token upon user logout."""
        if not raw_refresh_token:
            return False
        token_hash = SecurityJWT.hash_token(raw_refresh_token)
        record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        if record and not record.revoked:
            record.revoked = True
            record.last_activity = datetime.now(timezone.utc)
            try:
                with db.begin():
                    db.add(record)
            except Exception:
                db.rollback()
                raise
            return True
        return False
