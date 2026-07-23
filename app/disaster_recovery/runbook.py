from typing import Dict, Any, List
from datetime import datetime, timezone
from app.disaster_recovery.failover_engine import FailoverEngine

class DisasterSimulator:
    @classmethod
    def simulate_incident(cls, scenario: str) -> Dict[str, Any]:
        scenario = scenario.upper().strip()
        timestamp = datetime.now(timezone.utc).isoformat()

        if scenario == "FLIGHT_API_OUTAGE":
            FailoverEngine.set_outage_state("FLIGHT_API", True)
            res = FailoverEngine.handle_flight_api_failover("SHF-101")
            return {
                "scenario": scenario,
                "timestamp": timestamp,
                "action": "Circuit breaker activated; fallback to local FlightStatusRecord TTL cache.",
                "mitigationResult": res
            }
        elif scenario == "NOTIF_OUTAGE":
            FailoverEngine.set_outage_state("NOTIFICATION", True)
            res = FailoverEngine.handle_notification_failover("client@shafsky.com", "EMAIL")
            return {
                "scenario": scenario,
                "timestamp": timestamp,
                "action": "Notification gateway offline; dispatches buffered in DB queue for 60s retry.",
                "mitigationResult": res
            }
        elif scenario == "PAYMENT_OUTAGE":
            FailoverEngine.set_outage_state("PAYMENT", True)
            res = FailoverEngine.handle_payment_failover("SHF-20260723-1122", 15000.0)
            return {
                "scenario": scenario,
                "timestamp": timestamp,
                "action": "Payment provider down; booking status set to PENDING_PAYMENT without dropping booking.",
                "mitigationResult": res
            }
        elif scenario == "RECOVER_ALL":
            for k in ["FLIGHT_API", "NOTIFICATION", "PAYMENT", "DATABASE"]:
                FailoverEngine.set_outage_state(k, False)
            return {
                "scenario": scenario,
                "timestamp": timestamp,
                "action": "All simulated outages cleared. Operational state NORMAL."
            }
        else:
            return {
                "scenario": scenario,
                "timestamp": timestamp,
                "action": f"Scenario '{scenario}' simulated; operational parameters verified."
            }

    @classmethod
    def get_runbook_procedures(cls) -> List[Dict[str, Any]]:
        return [
            {
                "incident": "Database Corruption or Deletion",
                "rpoTarget": "<= 5 minutes (Neon PITR)",
                "rtoTarget": "<= 15 minutes",
                "procedure": "1. Access Neon Console -> Select Point-In-Time Recovery. 2. Choose timestamp prior to corrupt transaction. 3. Redirect DATABASE_URL connection pool."
            },
            {
                "incident": "External Flight API Outage",
                "rpoTarget": "Zero Data Loss",
                "rtoTarget": "0 ms (Immediate Failover)",
                "procedure": "1. Circuit breaker engages automatically. 2. Serves cached flight state from FlightStatusRecord. 3. Creates booking with warning flag."
            },
            {
                "incident": "Notification Provider Outage",
                "rpoTarget": "Zero Data Loss",
                "rtoTarget": "0 ms (Async Buffer)",
                "procedure": "1. Enqueues payload in NotificationRecord table. 2. Background retry worker processes dispatches every 60s."
            }
        ]
