from typing import List, Dict, Any
from app.monitoring.health import HealthCheckSuite

class AlertRuleEngine:
    @classmethod
    def evaluate_system_alerts(cls) -> List[Dict[str, Any]]:
        alerts = []
        health = HealthCheckSuite.run_deep_health()
        sub = health["subsystems"]
        res = sub.get("systemResources", {})

        if sub.get("database", {}).get("status") not in ("HEALTHY", "SLOW"):
            alerts.append({
                "alert": "DatabaseConnectivityFailure",
                "severity": "CRITICAL",
                "message": "PostgreSQL database connection check failed!"
            })

        rss = float(res.get("processRssMB") or 0)
        available = float(res.get("memoryAvailableMB") or 0)
        if rss >= 1024.0 or (available > 0 and available < 256.0):
            alerts.append({
                "alert": "HighMemoryUsage",
                "severity": "WARNING",
                "message": (
                    f"Process RSS {rss} MB; host available {available} MB "
                    f"(host usage {res.get('memoryUsagePercent')}%)"
                ),
            })

        disk = res.get("diskFreeGB")
        if disk is not None and float(disk) < 5.0:
            alerts.append({
                "alert": "LowDiskSpace",
                "severity": "CRITICAL",
                "message": f"Disk space critically low: {res.get('diskFreeGB')} GB remaining."
            })

        return alerts
