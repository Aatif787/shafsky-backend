from typing import Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from app.models.schema import Booking, BookingStatus, NotificationRecord, Profile
from app.monitoring.health import HealthCheckSuite
from app.monitoring.alerts import AlertRuleEngine

class ObservabilityDashboard:
    @classmethod
    def get_dashboard_metrics(cls, db: Session) -> Dict[str, Any]:
        total_bookings = db.scalar(select(func.count(Booking.id))) or 0
        total_revenue = db.scalar(select(func.sum(Booking.total_amount))) or 0.0
        total_customers = db.scalar(select(func.count(Profile.id)).where(Profile.deleted_at.is_(None))) or 0
        total_notifications = db.scalar(select(func.count(NotificationRecord.id))) or 0

        health = HealthCheckSuite.run_deep_health()
        alerts = AlertRuleEngine.evaluate_system_alerts()

        return {
            "platformHealth": health["status"],
            "totalBookings": total_bookings,
            "totalRevenueINR": float(total_revenue),
            "totalActiveCustomers": total_customers,
            "totalNotificationsDispatched": total_notifications,
            "systemAlertsCount": len(alerts),
            "systemAlerts": alerts,
            "subsystemHealth": health["subsystems"]
        }
