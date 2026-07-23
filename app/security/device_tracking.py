import hashlib
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
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            ip = forwarded_for.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "127.0.0.1"

        client_device_id = request.headers.get("X-Device-ID")
        if not client_device_id:
            # Deterministic fingerprint if header missing
            fp_str = f"{ip}:{user_agent}"
            client_device_id = f"dev_{hashlib.md5(fp_str.encode()).hexdigest()[:12]}"

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
