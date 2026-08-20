import hashlib
import hmac
from app.config import settings
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from fastapi import Request
from sqlalchemy.orm import Session
from sqlalchemy import select, update

from app.models.schema import RefreshToken

class DeviceTracking:
    @staticmethod
    def parse_user_agent(user_agent: str) -> Dict[str, str]:
        ua = user_agent.lower()
        browser = "Unknown Browser"
        if "chrome" in ua and "edg" not in ua:
            browser = "Chrome"
        elif "safari" in ua and "chrome" not in ua:
            browser = "Safari"
        elif "firefox" in ua:
            browser = "Firefox"
        elif "edg" in ua:
            browser = "Edge"

        platform = "Unknown Platform"
        if "windows" in ua:
            platform = "Windows"
        elif "macintosh" in ua or "mac os" in ua:
            platform = "macOS"
        elif "iphone" in ua or "ipad" in ua:
            platform = "iOS"
        elif "android" in ua:
            platform = "Android"
        elif "linux" in ua:
            platform = "Linux"

        return {"browser": browser, "platform": platform}

    @classmethod
    def get_client_device(cls, request: Request) -> Dict[str, str]:
        user_agent = request.headers.get("User-Agent", "Unknown Client")
        from app.security.client_ip import get_client_ip
        ip = get_client_ip(request)

        client_device_id = request.headers.get("X-Device-ID")
        if not client_device_id:
            # Deterministic, HMAC-protected fingerprint if header missing
            fp_str = f"{ip}:{user_agent}"
            # Use a server-side secret to HMAC the fingerprint for deterministic but cryptographically strong IDs
            secret = getattr(settings, "DEVICE_ID_HMAC_SECRET", None) or getattr(settings, "JWT_REFRESH_SECRET", None) or ""
            try:
                digest = hmac.new(secret.encode("utf-8"), fp_str.encode("utf-8"), hashlib.sha256).hexdigest()
                client_device_id = f"dev_{digest[:24]}"
            except Exception:
                # Fallback: non-cryptographic but unique identifier
                client_device_id = f"dev_{hashlib.sha256(fp_str.encode()).hexdigest()[:24]}"

        parsed = cls.parse_user_agent(user_agent)
        return {
            "device_id": client_device_id,
            "browser": parsed["browser"],
            "platform": parsed["platform"],
            "ip_address": ip
        }

    @classmethod
    def revoke_device_session(cls, db: Session, user_id, device_id: str) -> int:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.device_id == device_id, RefreshToken.revoked.is_(False))
            .values(revoked=True)
        )
        res = db.execute(stmt)
        db.commit()
        return res.rowcount

    @classmethod
    def revoke_all_user_sessions(cls, db: Session, user_id) -> int:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
            .values(revoked=True)
        )
        res = db.execute(stmt)
        db.commit()
        return res.rowcount
