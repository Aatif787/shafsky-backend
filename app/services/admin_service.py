import uuid
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select, func, or_, desc
from fastapi import HTTPException

logger = logging.getLogger("shafsky.admin")

from app.services.auth_service import AuthService
from app.models.schema import (
    UserAuth,
    Profile,
    Booking,
    BookingStatus,
    Role,
    AuditLog,
    StaffAssignment,
    ShiftRecord,
    AirportManagement
)
from app.models.payment import PaymentTransaction, PaymentStatus
from app.schemas.admin import (
    RoleUpdateRequest,
    StaffAssignRequest,
    ShiftCreateRequest,
    AirportCreateRequest
)
from app.services.admin_service_ext import AdminAirportPricingMixin


class AdminService(AdminAirportPricingMixin):
    @staticmethod
    def _to_json_safe(obj: Any) -> Any:
        if isinstance(obj, (int, float, str, bool)) or obj is None:
            return obj
        from decimal import Decimal
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, dict):
            return {str(k): AdminService._to_json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [AdminService._to_json_safe(v) for v in obj]
        return str(obj)

    @staticmethod
    def log_audit_action(
        db: Session,
        actor_email: str,
        action: str,
        resource_type: str,
        actor_id: Optional[uuid.UUID] = None,
        resource_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None
    ) -> AuditLog:
        safe_details = AdminService._to_json_safe(details) if details else {}
        audit_entry = AuditLog(
            id=uuid.uuid4(),
            actor_id=actor_id,
            actor_email=actor_email,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=safe_details,
            ip_address=ip_address,
            created_at=datetime.now(timezone.utc)
        )
        db.add(audit_entry)
        db.commit()
        return audit_entry

    @classmethod
    def update_user_role(cls, db: Session, target_user_id: str, new_role_str: str, admin_email: str) -> Dict[str, Any]:
        try:
            val_uuid = uuid.UUID(target_user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID format.")

        user = db.scalar(select(UserAuth).where(UserAuth.id == val_uuid))
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        try:
            new_role = Role(new_role_str.upper())
        except ValueError:
            valid_roles = [r.value for r in Role]
            raise HTTPException(status_code=400, detail=f"Invalid role '{new_role_str}'. Valid roles: {valid_roles}")

        old_role = user.role.value if isinstance(user.role, Role) else str(user.role)
        user.role = new_role
        user.updated_at = datetime.now(timezone.utc)

        # Update profile role if profile exists
        profile = db.scalar(select(Profile).where(Profile.auth_id == val_uuid))
        if profile:
            profile.role = new_role
            profile.updated_at = datetime.now(timezone.utc)

        db.commit()

        cls.log_audit_action(
            db,
            actor_email=admin_email,
            action="ROLE_CHANGE",
            resource_type="USER",
            actor_id=val_uuid,
            resource_id=target_user_id,
            details={"oldRole": old_role, "newRole": new_role.value}
        )

        return {
            "userId": str(user.id),
            "email": user.email,
            "role": user.role.value
        }

    @classmethod
    def ensure_user_exists(cls, db: Session, user_id: uuid.UUID) -> UserAuth:
        user = db.scalar(select(UserAuth).where(UserAuth.id == user_id))
        if not user:
            raise HTTPException(status_code=404, detail="Staff user does not exist.")
        if not getattr(user, "is_active", True):
            raise HTTPException(status_code=400, detail="Staff user account is deactivated.")
        return user

    @classmethod
    def assign_staff(cls, db: Session, payload: StaffAssignRequest, admin_email: str) -> Dict[str, Any]:
        try:
            b_uuid = uuid.UUID(payload.booking_id)
            s_uuid = uuid.UUID(payload.staff_user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid UUID format for booking_id or staff_user_id.")

        booking = db.scalar(select(Booking).where(Booking.id == b_uuid))
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found.")

        staff_user = cls.ensure_user_exists(db, s_uuid)

        assignment = StaffAssignment(
            id=uuid.uuid4(),
            booking_id=b_uuid,
            staff_user_id=s_uuid,
            role_type=payload.role_type.upper(),
            status="ASSIGNED",
            notes=payload.notes,
            created_at=datetime.now(timezone.utc)
        )

        # Update booking status to ASSIGNED if currently PENDING
        if booking.status == BookingStatus.PENDING:
            booking.status = BookingStatus.ASSIGNED
            booking.updated_at = datetime.now(timezone.utc)

        db.add(assignment)
        db.commit()

        cls.log_audit_action(
            db,
            actor_email=admin_email,
            action="STAFF_ASSIGNMENT",
            resource_type="BOOKING",
            resource_id=booking.booking_ref,
            details={"staffUser": staff_user.email, "roleType": payload.role_type}
        )

        return {
            "assignmentId": str(assignment.id),
            "bookingRef": booking.booking_ref,
            "staffEmail": staff_user.email,
            "roleType": assignment.role_type,
            "status": assignment.status
        }

    @classmethod
    def get_booking_assignments(cls, db: Session, booking_id: str) -> List[Dict[str, Any]]:
        try:
            b_uuid = uuid.UUID(booking_id)
            stmt = select(StaffAssignment).where(StaffAssignment.booking_id == b_uuid)
        except ValueError:
            stmt = select(StaffAssignment).join(Booking).where(Booking.booking_ref == booking_id)

        assignments = list(db.scalars(stmt).all())
        results = []
        for a in assignments:
            staff = db.scalar(select(UserAuth).where(UserAuth.id == a.staff_user_id))
            results.append({
                "id": str(a.id),
                "roleType": a.role_type,
                "staffEmail": staff.email if staff else "Unknown",
                "status": a.status,
                "notes": a.notes,
                "createdAt": a.created_at.isoformat()
            })
        return results

    @classmethod
    def create_shift(cls, db: Session, payload: ShiftCreateRequest, admin_email: str) -> Dict[str, Any]:
        try:
            s_uuid = uuid.UUID(payload.staff_user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid staff_user_id format.")

        cls.ensure_user_exists(db, s_uuid)

        shift = ShiftRecord(
            id=uuid.uuid4(),
            staff_user_id=s_uuid,
            shift_name=payload.shift_name.upper(),
            shift_date=payload.shift_date,
            start_time=payload.start_time,
            end_time=payload.end_time,
            airport_code=payload.airport_code.upper(),
            status="SCHEDULED",
            created_at=datetime.now(timezone.utc)
        )
        db.add(shift)
        db.commit()

        cls.log_audit_action(
            db,
            actor_email=admin_email,
            action="SHIFT_SCHEDULED",
            resource_type="DUTY_ROSTER",
            resource_id=str(shift.id),
            details={"shiftName": shift.shift_name, "airport": shift.airport_code}
        )

        return {
            "id": str(shift.id),
            "shiftName": shift.shift_name,
            "airportCode": shift.airport_code,
            "status": shift.status,
            "shiftDate": shift.shift_date.isoformat()
        }

    @classmethod
    def get_shift_roster(cls, db: Session, airport_code: Optional[str] = None) -> List[Dict[str, Any]]:
        stmt = select(ShiftRecord)
        if airport_code:
            stmt = stmt.where(ShiftRecord.airport_code == airport_code.upper())
        stmt = stmt.order_by(desc(ShiftRecord.shift_date))

        shifts = list(db.scalars(stmt).all())
        results = []
        for s in shifts:
            staff = db.scalar(select(UserAuth).where(UserAuth.id == s.staff_user_id))
            results.append({
                "id": str(s.id),
                "staffEmail": staff.email if staff else "Unknown",
                "shiftName": s.shift_name,
                "shiftDate": s.shift_date.isoformat(),
                "startTime": s.start_time.isoformat(),
                "endTime": s.end_time.isoformat(),
                "airportCode": s.airport_code,
                "status": s.status
            })
        return results

    @classmethod
    def manage_airport(cls, db: Session, payload: AirportCreateRequest, admin_email: str) -> Dict[str, Any]:
        code = payload.code.upper()
        airport = db.scalar(select(AirportManagement).where(AirportManagement.code == code))

        if not airport:
            airport = AirportManagement(
                id=uuid.uuid4(),
                code=code,
                name=payload.name,
                city=payload.city,
                country=payload.country or "IND",
                is_active=True,
                operating_hours=payload.operating_hours or "24/7",
                services_config=payload.services_config or {},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(airport)
            action = "AIRPORT_CREATED"
        else:
            airport.name = payload.name
            airport.city = payload.city
            airport.country = payload.country or airport.country
            airport.operating_hours = payload.operating_hours or airport.operating_hours
            if payload.services_config:
                airport.services_config = payload.services_config
            airport.updated_at = datetime.now(timezone.utc)
            action = "AIRPORT_UPDATED"

        db.commit()

        cls.log_audit_action(
            db,
            actor_email=admin_email,
            action=action,
            resource_type="AIRPORT",
            resource_id=code,
            details={"name": airport.name, "city": airport.city}
        )

        return {
            "id": str(airport.id),
            "code": airport.code,
            "name": airport.name,
            "city": airport.city,
            "operatingHours": airport.operating_hours,
            "isActive": airport.is_active
        }

    @classmethod
    def list_airports(cls, db: Session) -> List[Dict[str, Any]]:
        airports = list(db.scalars(select(AirportManagement).order_by(AirportManagement.code)).all())
        return [
            {
                "id": str(a.id),
                "code": a.code,
                "name": a.name,
                "city": a.city,
                "country": a.country,
                "operatingHours": a.operating_hours,
                "isActive": a.is_active,
                "servicesConfig": a.services_config
            }
            for a in airports
        ]

    # Analytics & Reports Generator
    @classmethod
    def generate_daily_report(cls, db: Session) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        live = Booking.deleted_at.is_(None)

        total_bookings = db.scalar(
            select(func.count(Booking.id)).where(live, Booking.created_at >= today_start)
        ) or 0
        confirmed = db.scalar(
            select(func.count(Booking.id)).where(
                live, Booking.created_at >= today_start, Booking.status == BookingStatus.CONFIRMED
            )
        ) or 0
        completed = db.scalar(
            select(func.count(Booking.id)).where(
                live, Booking.created_at >= today_start, Booking.status == BookingStatus.COMPLETED
            )
        ) or 0
        pending_bookings = db.scalar(
            select(func.count(Booking.id)).where(live, Booking.status == BookingStatus.PENDING)
        ) or 0
        live_refs = select(Booking.booking_ref).where(live)
        pending_payments = db.scalar(
            select(func.count(PaymentTransaction.id)).where(
                PaymentTransaction.status.in_((PaymentStatus.PENDING, PaymentStatus.PROCESSING)),
                PaymentTransaction.entity_id.in_(live_refs),
            )
        ) or 0
        paid_revenue = db.scalar(
            select(func.coalesce(func.sum(PaymentTransaction.amount), 0)).where(
                PaymentTransaction.status == PaymentStatus.SUCCESSFUL,
                PaymentTransaction.created_at >= today_start,
                PaymentTransaction.entity_id.in_(live_refs),
            )
        ) or 0.0

        return {
            "reportType": "DAILY",
            "date": today_start.strftime("%Y-%m-%d"),
            "totalBookings": total_bookings,
            "confirmedBookings": confirmed,
            "completedBookings": completed,
            "pendingBookings": pending_bookings,
            "pendingPayments": pending_payments,
            "dailyRevenueINR": paid_revenue,
        }

    @classmethod
    def generate_weekly_report(cls, db: Session) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        week_start = now - timedelta(days=7)

        total_bookings = db.scalar(select(func.count(Booking.id)).where(Booking.deleted_at.is_(None), Booking.created_at >= week_start)) or 0
        live_refs = select(Booking.booking_ref).where(Booking.deleted_at.is_(None))
        revenue = db.scalar(
            select(func.coalesce(func.sum(PaymentTransaction.amount), 0)).where(
                PaymentTransaction.status == PaymentStatus.SUCCESSFUL,
                PaymentTransaction.created_at >= week_start,
                PaymentTransaction.entity_id.in_(live_refs),
            )
        ) or 0.0

        return {
            "reportType": "WEEKLY",
            "period": f"{week_start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}",
            "totalBookings": total_bookings,
            "weeklyRevenueINR": revenue
        }

    @classmethod
    def generate_monthly_report(cls, db: Session) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        total_bookings = db.scalar(select(func.count(Booking.id)).where(Booking.deleted_at.is_(None), Booking.created_at >= month_start)) or 0
        live_refs = select(Booking.booking_ref).where(Booking.deleted_at.is_(None))
        revenue = db.scalar(
            select(func.coalesce(func.sum(PaymentTransaction.amount), 0)).where(
                PaymentTransaction.status == PaymentStatus.SUCCESSFUL,
                PaymentTransaction.created_at >= month_start,
                PaymentTransaction.entity_id.in_(live_refs),
            )
        ) or 0.0

        return {
            "reportType": "MONTHLY",
            "month": month_start.strftime("%B %Y"),
            "totalBookings": total_bookings,
            "monthlyRevenueINR": revenue
        }

    @classmethod
    def generate_revenue_report(cls, db: Session) -> Dict[str, Any]:
        live_refs = select(Booking.booking_ref).where(Booking.deleted_at.is_(None))
        total_revenue = db.scalar(
            select(func.coalesce(func.sum(PaymentTransaction.amount), 0)).where(
                PaymentTransaction.status == PaymentStatus.SUCCESSFUL,
                PaymentTransaction.entity_id.in_(live_refs),
            )
        ) or 0.0
        currency_breakdown = {"INR": total_revenue}
        return {
            "reportType": "REVENUE_SUMMARY",
            "grossRevenueINR": total_revenue,
            "currencyBreakdown": currency_breakdown
        }

    @classmethod
    def generate_staff_performance(cls, db: Session) -> List[Dict[str, Any]]:
        assignments = list(db.scalars(select(StaffAssignment)).all())
        staff_counts: Dict[str, int] = {}
        for a in assignments:
            sid = str(a.staff_user_id)
            staff_counts[sid] = staff_counts.get(sid, 0) + 1

        results = []
        for sid, count in staff_counts.items():
            staff = db.scalar(select(UserAuth).where(UserAuth.id == uuid.UUID(sid)))
            results.append({
                "staffId": sid,
                "staffEmail": staff.email if staff else "Unknown",
                "assignedTasksCount": count,
                "performanceRating": "EXCELLENT" if count > 5 else "GOOD"
            })
        return results

    @classmethod
    def generate_airport_stats(cls, db: Session) -> List[Dict[str, Any]]:
        stats = db.execute(
            select(Booking.origin_code, func.count(Booking.id))
            .group_by(Booking.origin_code)
        ).all()

        return [
            {"airportCode": code, "departuresCount": count}
            for code, count in stats
        ]

    @classmethod
    def get_audit_logs(
        cls,
        db: Session,
        limit: int = 100,
        *,
        exclude_super_admin_actors: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return recent audit log rows for the admin portal.

        When exclude_super_admin_actors is True (non–SUPER_ADMIN viewers),
        actions performed by SUPER_ADMIN accounts are omitted so break-glass
        activity is not visible on the day-to-day Admin dashboard.
        """
        fetch_limit = min(max(limit * 5, limit), 500) if exclude_super_admin_actors else limit
        logs = list(
            db.scalars(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(fetch_limit)).all()
        )

        # Resolve actor roles in one query (by id and email).
        actor_ids = {l.actor_id for l in logs if l.actor_id}
        actor_emails = {(l.actor_email or "").lower().strip() for l in logs if l.actor_email}
        role_by_id: Dict[Any, str] = {}
        role_by_email: Dict[str, str] = {}
        clauses = []
        if actor_ids:
            clauses.append(UserAuth.id.in_(list(actor_ids)))
        if actor_emails:
            clauses.append(func.lower(UserAuth.email).in_(list(actor_emails)))
        if clauses:
            users = list(db.scalars(select(UserAuth).where(or_(*clauses))).all())
            for u in users:
                role_name = u.role.value if hasattr(u.role, "value") else str(u.role)
                role_by_id[u.id] = role_name
                role_by_email[(u.email or "").lower().strip()] = role_name

        result: List[Dict[str, Any]] = []
        for l in logs:
            email_key = (l.actor_email or "").lower().strip()
            actor_role = None
            if l.actor_id and l.actor_id in role_by_id:
                actor_role = role_by_id[l.actor_id]
            elif email_key in role_by_email:
                actor_role = role_by_email[email_key]

            if exclude_super_admin_actors and (actor_role or "").upper() == "SUPER_ADMIN":
                continue

            result.append(
                {
                    "id": str(l.id),
                    "actorEmail": l.actor_email,
                    "actorRole": actor_role,
                    "action": l.action,
                    "resourceType": l.resource_type,
                    "resourceId": l.resource_id,
                    "details": l.details,
                    "timestamp": l.created_at.isoformat(),
                }
            )
            if len(result) >= limit:
                break
        return result
