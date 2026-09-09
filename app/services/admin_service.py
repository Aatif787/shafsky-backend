import uuid
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
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
            "totalBookings": int(total_bookings),
            "confirmedBookings": int(confirmed),
            "completedBookings": int(completed),
            "pendingBookings": int(pending_bookings),
            "pendingPayments": int(pending_payments),
            "dailyRevenueINR": float(paid_revenue),
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
            "weeklyRevenueINR": float(revenue)
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
            "monthlyRevenueINR": float(revenue)
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

    # ─── Airport Services & Pricing Matrix Management ───
    @classmethod
    def list_airport_services(
        cls,
        db: Session,
        airport_code: Optional[str] = None,
        journey_type: Optional[str] = None,
        flight_type: Optional[str] = None,
        is_available: Optional[bool] = None,
        limit: int = 300,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        from app.models.journey_models import SupportedAirport, Service, AirportService
        from app.services.service_airport_rules import normalize_iata, normalize_journey_type, normalize_flight_type

        stmt = (
            select(AirportService, SupportedAirport, Service)
            .join(SupportedAirport, AirportService.airport_id == SupportedAirport.id)
            .join(Service, AirportService.service_id == Service.id)
        )

        if airport_code:
            clean_code = normalize_iata(airport_code)
            stmt = stmt.where(SupportedAirport.iata_code == clean_code)

        if journey_type:
            clean_jt = normalize_journey_type(journey_type)
            stmt = stmt.where(AirportService.journey_type == clean_jt)

        if flight_type:
            clean_ft = normalize_flight_type(flight_type) or flight_type.strip().upper()
            stmt = stmt.where(AirportService.flight_type.in_([clean_ft, "ALL"]))

        if is_available is not None:
            stmt = stmt.where(AirportService.is_available == is_available)

        stmt = stmt.order_by(
            SupportedAirport.iata_code,
            AirportService.journey_type,
            AirportService.flight_type,
            Service.display_order,
            AirportService.price
        ).limit(limit).offset(offset)

        rows = db.execute(stmt).all()
        results = []
        for aps, apt, svc in rows:
            results.append({
                "id": str(aps.id),
                "airport_id": str(apt.id),
                "airport_code": apt.iata_code,
                "airport_name": apt.airport_name,
                "city": apt.city,
                "service_id": str(svc.id),
                "service_name": svc.name,
                "service_slug": svc.slug,
                "price": float(aps.price),
                "currency": aps.currency or "INR",
                "journey_type": aps.journey_type,
                "flight_type": aps.flight_type,
                "terminal": aps.terminal or "All",
                "is_available": aps.is_available,
                "features": aps.features if isinstance(aps.features, list) else [],
                "short_description": aps.short_description or "",
                "min_booking_notice_hours": aps.min_booking_notice_hours or 0,
                "updated_at": aps.updated_at.isoformat() if aps.updated_at else None,
            })
        return results

    @classmethod
    def update_airport_service(
        cls,
        db: Session,
        mapping_id: str,
        updates: Dict[str, Any],
        admin_email: str
    ) -> Dict[str, Any]:
        from app.models.journey_models import SupportedAirport, Service, AirportService
        try:
            m_uuid = uuid.UUID(mapping_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid mapping UUID format.")

        mapping = db.scalar(select(AirportService).where(AirportService.id == m_uuid))
        if not mapping:
            raise HTTPException(status_code=404, detail="Airport service mapping not found.")

        old_details = {
            "price": float(mapping.price),
            "currency": mapping.currency,
            "is_available": mapping.is_available,
            "terminal": mapping.terminal,
            "journey_type": mapping.journey_type,
            "flight_type": mapping.flight_type,
        }

        if "price" in updates and updates["price"] is not None:
            try:
                new_price = float(updates["price"])
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Price must be a valid number.")
            if new_price <= 0:
                raise HTTPException(status_code=400, detail="Price must be greater than 0.")
            if new_price > 50000000:
                raise HTTPException(status_code=400, detail="Price exceeds maximum allowed limit.")
            mapping.price = round(new_price, 2)

        if "currency" in updates and updates["currency"]:
            curr = str(updates["currency"]).strip().upper()
            if len(curr) != 3:
                raise HTTPException(status_code=400, detail="Currency must be a 3-letter code (e.g., INR).")
            mapping.currency = curr

        if "is_available" in updates and updates["is_available"] is not None:
            mapping.is_available = bool(updates["is_available"])

        if "terminal" in updates and updates["terminal"] is not None:
            mapping.terminal = str(updates["terminal"]).strip()

        if "features" in updates and updates["features"] is not None:
            if isinstance(updates["features"], list):
                setattr(mapping, "features", updates["features"])
            elif isinstance(updates["features"], str):
                setattr(mapping, "features", [f.strip() for f in updates["features"].split(",") if f.strip()])

        if "short_description" in updates and updates["short_description"] is not None:
            mapping.short_description = str(updates["short_description"]).strip()

        if "min_booking_notice_hours" in updates and updates["min_booking_notice_hours"] is not None:
            try:
                mapping.min_booking_notice_hours = max(0, int(updates["min_booking_notice_hours"]))
            except (ValueError, TypeError):
                pass

        try:
            mapping.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(mapping)
        except Exception as err:
            db.rollback()
            logger.error("[AdminService] Failed to commit airport service update: %s", err)
            raise HTTPException(status_code=500, detail="Database transaction failed during update.")

        # Audit log
        try:
            cls.log_audit_action(
                db=db,
                actor_email=admin_email,
                action="UPDATE_AIRPORT_SERVICE_PRICE",
                resource_type="AirportService",
                resource_id=str(mapping.id),
                details={
                    "old": old_details,
                    "new": {
                        "price": float(mapping.price),
                        "currency": mapping.currency,
                        "is_available": mapping.is_available,
                        "terminal": mapping.terminal,
                    }
                }
            )
        except Exception as audit_err:
            logger.warning("[AdminService] Audit log write failed: %s", audit_err)

        apt = db.scalar(select(SupportedAirport).where(SupportedAirport.id == mapping.airport_id))
        svc = db.scalar(select(Service).where(Service.id == mapping.service_id))

        return {
            "id": str(mapping.id),
            "airport_code": apt.iata_code if apt else "",
            "airport_name": apt.airport_name if apt else "",
            "service_name": svc.name if svc else "",
            "service_slug": svc.slug if svc else "",
            "price": float(mapping.price),
            "currency": mapping.currency,
            "journey_type": mapping.journey_type,
            "flight_type": mapping.flight_type,
            "terminal": mapping.terminal,
            "is_available": mapping.is_available,
            "features": mapping.features if isinstance(mapping.features, list) else [],
            "short_description": mapping.short_description or "",
            "min_booking_notice_hours": mapping.min_booking_notice_hours,
            "updated_at": mapping.updated_at.isoformat(),
        }

    @classmethod
    def create_airport_service(
        cls,
        db: Session,
        payload: Dict[str, Any],
        admin_email: str
    ) -> Dict[str, Any]:
        from app.models.journey_models import SupportedAirport, Service, AirportService
        from app.services.service_airport_rules import normalize_iata, normalize_journey_type, normalize_flight_type

        airport_code = normalize_iata(payload.get("airport_code") or payload.get("airport"))
        if not airport_code:
            raise HTTPException(status_code=400, detail="Airport code is required.")

        apt = db.scalar(select(SupportedAirport).where(SupportedAirport.iata_code == airport_code))
        if not apt:
            raise HTTPException(status_code=404, detail=f"Supported airport '{airport_code}' not found.")

        service_slug = (payload.get("service_slug") or payload.get("service") or "").strip().lower()
        if not service_slug:
            raise HTTPException(status_code=400, detail="Service slug is required.")

        svc = db.scalar(select(Service).where(Service.slug == service_slug))
        if not svc:
            raise HTTPException(status_code=404, detail=f"Service '{service_slug}' not found.")

        try:
            price = float(payload.get("price", 0))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Valid price is required.")
        if price <= 0:
            raise HTTPException(status_code=400, detail="Price must be greater than 0.")

        journey_type = normalize_journey_type(payload.get("journey_type"))
        flight_type = normalize_flight_type(payload.get("flight_type")) or "DOMESTIC"
        terminal = (payload.get("terminal") or "All").strip()
        currency = (payload.get("currency") or "INR").strip().upper()

        existing = db.scalar(
            select(AirportService).where(
                AirportService.airport_id == apt.id,
                AirportService.service_id == svc.id,
                AirportService.journey_type == journey_type,
                AirportService.flight_type == flight_type,
                AirportService.terminal == terminal,
            )
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Mapping already exists for {airport_code} - {service_slug} ({journey_type} {flight_type} {terminal})."
            )

        features = payload.get("features", [])
        if isinstance(features, str):
            features = [f.strip() for f in features.split(",") if f.strip()]

        new_mapping = AirportService(
            id=uuid.uuid4(),
            airport_id=apt.id,
            service_id=svc.id,
            journey_type=journey_type,
            flight_type=flight_type,
            terminal=terminal,
            price=round(price, 2),
            currency=currency,
            is_available=bool(payload.get("is_available", True)),
            features=features,
            short_description=payload.get("short_description") or svc.description or "",
            min_booking_notice_hours=int(payload.get("min_booking_notice_hours", 0)),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        try:
            db.add(new_mapping)
            db.commit()
            db.refresh(new_mapping)
        except Exception as err:
            db.rollback()
            logger.error("[AdminService] Failed to create airport service mapping: %s", err)
            raise HTTPException(status_code=500, detail="Database transaction failed during creation.")

        try:
            cls.log_audit_action(
                db=db,
                actor_email=admin_email,
                action="CREATE_AIRPORT_SERVICE",
                resource_type="AirportService",
                resource_id=str(new_mapping.id),
                details={"airport": airport_code, "service": service_slug, "price": price}
            )
        except Exception as audit_err:
            logger.warning("[AdminService] Audit log write failed: %s", audit_err)

        return {
            "id": str(new_mapping.id),
            "airport_code": apt.iata_code,
            "airport_name": apt.airport_name,
            "service_name": svc.name,
            "service_slug": svc.slug,
            "price": float(new_mapping.price),
            "currency": new_mapping.currency,
            "journey_type": new_mapping.journey_type,
            "flight_type": new_mapping.flight_type,
            "terminal": new_mapping.terminal,
            "is_available": new_mapping.is_available,
            "features": new_mapping.features,
            "short_description": new_mapping.short_description,
            "min_booking_notice_hours": new_mapping.min_booking_notice_hours,
            "updated_at": new_mapping.updated_at.isoformat(),
        }

    @classmethod
    def delete_airport_service(cls, db: Session, mapping_id: str, admin_email: str) -> Dict[str, Any]:
        from app.models.journey_models import AirportService
        try:
            m_uuid = uuid.UUID(mapping_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid mapping UUID format.")

        mapping = db.scalar(select(AirportService).where(AirportService.id == m_uuid))
        if not mapping:
            raise HTTPException(status_code=404, detail="Airport service mapping not found.")

        try:
            mapping.is_available = False
            mapping.updated_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as err:
            db.rollback()
            logger.error("[AdminService] Failed to deactivate airport service %s: %s", mapping_id, err)
            raise HTTPException(status_code=500, detail="Database transaction failed during deactivation.")

        try:
            cls.log_audit_action(
                db=db,
                actor_email=admin_email,
                action="DEACTIVATE_AIRPORT_SERVICE",
                resource_type="AirportService",
                resource_id=str(mapping.id)
            )
        except Exception as audit_err:
            logger.warning("[AdminService] Audit log write failed: %s", audit_err)

        return {"success": True, "id": str(mapping.id), "is_available": False}

