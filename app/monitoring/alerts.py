from typing import List, Dict, Any
from app.monitoring.health import HealthCheckSuite

class AlertRuleEngine:
    @classmethod
    def evaluate_system_alerts(cls) -> List[Dict[str, Any]]:
        alerts = []
        health = HealthCheckSuite.run_deep_health()
        sub = health["subsystems"]
        res = sub.get("systemResources", {})

        if sub.get("database", {}).get("status") != "HEALTHY":
            alerts.append({
                "alert": "DatabaseConnectivityFailure",
                "severity": "CRITICAL",
                "message": "PostgreSQL database connection check failed!"
            })

        if res.get("memoryUsagePercent", 0) > 90.0:
            alerts.append({
                "alert": "HighMemoryUsage",
                "severity": "WARNING",
                "message": f"Memory usage exceeded 90%: {res.get('memoryUsagePercent')}%"
            })

        if res.get("diskFreeGB", 100) < 5.0:
            alerts.append({
                "alert": "LowDiskSpace",
                "severity": "CRITICAL",
                "message": f"Disk space critically low: {res.get('diskFreeGB')} GB remaining."
            })

        return alerts
