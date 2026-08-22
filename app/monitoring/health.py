import os
import time
import shutil
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import psutil
from sqlalchemy import text

from app.config import settings
from app.database import engine

# Host RAM on a busy Windows laptop is not the API process. Do not mark
# the platform DEGRADED just because Chrome/OneDrive filled the machine.
_PROCESS_RSS_DEGRADE_MB = 1536.0
_HOST_AVAILABLE_DEGRADE_MB = 128.0
_HEALTH_CACHE_TTL_SEC = 5.0

_health_lock = threading.Lock()
_health_cache: Optional[Dict[str, Any]] = None
_health_cache_at = 0.0


class HealthCheckSuite:
    @classmethod
    def check_database(cls) -> Dict[str, Any]:
        start = time.perf_counter()
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            status = "HEALTHY"
            if elapsed_ms >= 2500:
                status = "SLOW"
            return {"status": status, "latencyMs": elapsed_ms}
        except Exception as e:
            return {"status": "UNHEALTHY", "error": str(e)}

    @classmethod
    def check_system_resources(cls) -> Dict[str, Any]:
        memory = psutil.virtual_memory()
        try:
            disk = shutil.disk_usage(os.path.abspath(os.sep))
            disk_free_gb = round(disk.free / (1024 * 1024 * 1024), 2)
        except Exception:
            disk_free_gb = None
        proc = psutil.Process()
        rss_mb = round(proc.memory_info().rss / (1024 * 1024), 2)
        return {
            "memoryUsagePercent": memory.percent,
            "memoryAvailableMB": round(memory.available / (1024 * 1024), 2),
            "diskFreeGB": disk_free_gb,
            "processRssMB": rss_mb,
        }

    @classmethod
    def _notification_status(cls) -> Dict[str, Any]:
        email_configured = bool(settings.RESEND_API_KEY and (settings.EMAIL_FROM or settings.RESEND_FROM_EMAIL))
        wa_configured = bool(settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID)
        if email_configured:
            email_status = "CONFIGURED"
        else:
            email_status = "NOT_CONFIGURED"
        return {
            "status": email_status,
            "provider": "Resend",
            "emailConfigured": email_configured,
            "whatsappConfigured": wa_configured,
        }

    @classmethod
    def _platform_status(cls, db_health: Dict[str, Any], resources: Dict[str, Any]) -> str:
        db_status = db_health.get("status")
        if db_status == "UNHEALTHY":
            return "DEGRADED"
        rss = float(resources.get("processRssMB") or 0)
        available = float(resources.get("memoryAvailableMB") or 0)
        if rss >= _PROCESS_RSS_DEGRADE_MB:
            return "DEGRADED"
        if available > 0 and available < _HOST_AVAILABLE_DEGRADE_MB:
            return "DEGRADED"
        if db_status == "SLOW":
            return "DEGRADED"
        return "UP"

    @classmethod
    def run_deep_health(cls, *, use_cache: bool = True) -> Dict[str, Any]:
        global _health_cache, _health_cache_at
        now = time.monotonic()
        if use_cache:
            with _health_lock:
                if _health_cache and (now - _health_cache_at) < _HEALTH_CACHE_TTL_SEC:
                    return _health_cache

        start = time.perf_counter()
        db_health = cls.check_database()
        resources = cls.check_system_resources()
        notify = cls._notification_status()
        wa_configured = bool(settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        payload = {
            "status": cls._platform_status(db_health, resources),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "totalCheckLatencyMs": elapsed_ms,
            "subsystems": {
                "database": db_health,
                "notificationService": notify,
                "whatsapp": {
                    "status": "CONFIGURED" if wa_configured else "NOT_CONFIGURED",
                    "configured": wa_configured,
                    "api_version": settings.WHATSAPP_API_VERSION,
                },
                "systemResources": resources,
            },
        }
        with _health_lock:
            _health_cache = payload
            _health_cache_at = time.monotonic()
        return payload

    @classmethod
    def run_readiness(cls) -> Dict[str, Any]:
        db_health = cls.check_database()
        ready = db_health.get("status") in ("HEALTHY", "SLOW")
        return {
            "ready": ready,
            "status": "READY" if ready else "NOT_READY",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def run_liveness(cls) -> Dict[str, Any]:
        return {
            "status": "ALIVE",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
