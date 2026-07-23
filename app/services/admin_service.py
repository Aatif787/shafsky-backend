import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, func, or_, desc
from fastapi import HTTPException

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
from app.schemas.admin import (
    RoleUpdateRequest,
    StaffAssignRequest,
    ShiftCreateRequest,
    AirportCreateRequest
)

class AdminService:
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
        audit_entry = AuditLog(
            id=uuid.uuid4(),
            actor_id=actor_id,
            actor_email=actor_email,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
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
    def ensure_user_exists(cls, db: Session, user_id: uuid.UUID, email: str = "admin@shafskyaviation.com", role: Role = Role.SUPER_ADMIN) -> UserAuth:
        user = db.scalar(select(UserAuth).where(UserAuth.id == user_id))
        if not user:
            # Seed user_auth record for system admin
            user = UserAuth(
                id=user_id,
                email=email,
                password_hash="ShafskyAdminHashedPassword2026",
                role=role,
                is_verified=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(user)
            db.commit()
            db.refresh(user)
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
            resource_id=str(booking.booking_ref),
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
        
        total_bookings = db.scalar(select(func.count(Booking.id)).where(Booking.created_at >= today_start)) or 0
        confirmed = db.scalar(select(func.count(Booking.id)).where(Booking.created_at >= today_start, Booking.status == BookingStatus.CONFIRMED)) or 0
        completed = db.scalar(select(func.count(Booking.id)).where(Booking.created_at >= today_start, Booking.status == BookingStatus.COMPLETED)) or 0
        revenue = db.scalar(select(func.sum(Booking.total_amount)).where(Booking.created_at >= today_start, Booking.status != BookingStatus.CANCELLED)) or 0.0

        return {
            "reportType": "DAILY",
            "date": today_start.strftime("%Y-%m-%d"),
            "totalBookings": total_bookings,
            "confirmedBookings": confirmed,
            "completedBookings": completed,
            "dailyRevenueINR": float(revenue)
        }

    @classmethod
    def generate_weekly_report(cls, db: Session) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        week_start = now - timedelta(days=7)

        total_bookings = db.scalar(select(func.count(Booking.id)).where(Booking.created_at >= week_start)) or 0
        revenue = db.scalar(select(func.sum(Booking.total_amount)).where(Booking.created_at >= week_start, Booking.status != BookingStatus.CANCELLED)) or 0.0

        return {
            "reportType": "WEEKLY",
            "period": f"{week_start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}",
            "totalBookings": total_bookings,
            "weeklyRevenueINR": float(revenue)
        }

    @classmethod
    def generate_monthly_report(cls, db: Session) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        total_bookings = db.scalar(select(func.count(Booking.id)).where(Booking.created_at >= month_start)) or 0
        revenue = db.scalar(select(func.sum(Booking.total_amount)).where(Booking.created_at >= month_start, Booking.status != BookingStatus.CANCELLED)) or 0.0

        return {
            "reportType": "MONTHLY",
            "month": month_start.strftime("%B %Y"),
            "totalBookings": total_bookings,
            "monthlyRevenueINR": float(revenue)
        }

    @classmethod
    def generate_revenue_report(cls, db: Session) -> Dict[str, Any]:
        total_revenue = db.scalar(select(func.sum(Booking.total_amount)).where(Booking.status != BookingStatus.CANCELLED)) or 0.0
        currency_breakdown = {"INR": float(total_revenue)}
        return {
            "reportType": "REVENUE_SUMMARY",
            "grossRevenueINR": float(total_revenue),
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
    def get_audit_logs(cls, db: Session, limit: int = 100) -> List[Dict[str, Any]]:
        logs = list(db.scalars(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)).all())
        return [
            {
                "id": str(l.id),
                "actorEmail": l.actor_email,
                "action": l.action,
                "resourceType": l.resource_type,
                "resourceId": l.resource_id,
                "details": l.details,
                "timestamp": l.created_at.isoformat()
            }
            for l in logs
        ]
