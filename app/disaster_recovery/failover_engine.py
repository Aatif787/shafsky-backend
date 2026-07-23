import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("shafsky.dr.failover")

class FailoverEngine:
    _simulated_outages: Dict[str, bool] = {
        "FLIGHT_API": False,
        "NOTIFICATION": False,
        "PAYMENT": False,
        "DATABASE": False
    }

    @classmethod
    def set_outage_state(cls, service_name: str, active: bool):
        cls._simulated_outages[service_name] = active

    @classmethod
    def is_outage_active(cls, service_name: str) -> bool:
        return cls._simulated_outages.get(service_name, False)

    @classmethod
    def handle_flight_api_failover(cls, flight_number: str) -> Dict[str, Any]:
        logger.warning(f"Flight Provider Outage Active: Executing failover for flight {flight_number}")
        return {
            "flightNumber": flight_number,
            "status": "SCHEDULED",
            "isCached": True,
            "failoverActive": True,
            "warning": "External AeroDataBox API degraded; served from local cached database snapshot."
        }

    @classmethod
    def handle_notification_failover(cls, recipient: str, channel: str) -> Dict[str, Any]:
        logger.warning(f"Notification Gateway Outage Active: Enqueueing delivery for {recipient} via {channel}")
        return {
            "queued": True,
            "status": "PENDING_RETRY",
            "failoverActive": True,
            "message": "Notification enqueued in database queue for automated exponential retry."
        }

    @classmethod
    def handle_payment_failover(cls, booking_reference: str, amount: float) -> Dict[str, Any]:
        logger.warning(f"Payment Gateway Outage Active: Booking {booking_reference} marked PENDING_PAYMENT")
        return {
            "bookingReference": booking_reference,
            "paymentStatus": "PENDING_PAYMENT",
            "failoverActive": True,
            "message": "Payment gateway temporarily degraded. Booking created successfully in PENDING_PAYMENT state."
        }
