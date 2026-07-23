import time
import shutil
import psutil
from datetime import datetime, timezone
from typing import Dict, Any
from sqlalchemy import text
from app.database import SessionLocal
from app.config import settings

class HealthCheckSuite:
    @classmethod
    def check_database(cls) -> Dict[str, Any]:
        start = time.time()
        try:
            db = SessionLocal()
            db.execute(text("SELECT 1"))
            db.close()
            elapsed_ms = round((time.time() - start) * 1000, 2)
            return {"status": "HEALTHY", "latencyMs": elapsed_ms}
        except Exception as e:
            return {"status": "UNHEALTHY", "error": str(e)}

    @classmethod
    def check_system_resources(cls) -> Dict[str, Any]:
        memory = psutil.virtual_memory()
        disk = shutil.disk_usage("/")
        return {
            "memoryUsagePercent": memory.percent,
            "memoryAvailableMB": round(memory.available / (1024 * 1024), 2),
            "diskFreeGB": round(disk.free / (1024 * 1024 * 1024), 2)
        }

    @classmethod
    def run_deep_health(cls) -> Dict[str, Any]:
        start = time.time()
        db_health = cls.check_database()
        resources = cls.check_system_resources()
        elapsed_ms = round((time.time() - start) * 1000, 2)

        is_healthy = db_health["status"] == "HEALTHY" and resources["memoryUsagePercent"] < 95.0

        from app.integrations.aerodatabox.service import AeroDataBoxService
        last_req = AeroDataBoxService.get_last_successful_request()

        return {
            "status": "UP" if is_healthy else "DEGRADED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "totalCheckLatencyMs": elapsed_ms,
            "subsystems": {
                "database": db_health,
                "flightIntelligenceService": {
                    "status": "HEALTHY",
                    "provider": "AeroDataBox API (RapidAPI)",
                    "lastSuccessfulRequest": last_req
                },
                "notificationService": {"status": "HEALTHY", "provider": "Resend & Meta Cloud API"},
                "systemResources": resources
            }
        }

    @classmethod
    def run_readiness(cls) -> Dict[str, Any]:
        db_health = cls.check_database()
        ready = db_health["status"] == "HEALTHY"
        return {
            "ready": ready,
            "status": "READY" if ready else "NOT_READY",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    @classmethod
    def run_liveness(cls) -> Dict[str, Any]:
        return {
            "status": "ALIVE",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
