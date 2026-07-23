import uuid
import secrets
from datetime import datetime, timezone
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
    VipTier,
    CaseStatus,
    CustomerCase,
    CustomerInteraction
)
from app.schemas.crm import (
    CustomerCreate,
    CustomerUpdate,
    CaseCreate,
    CaseUpdate
)

class CrmService:
    @staticmethod
    def generate_case_number() -> str:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        rand_suffix = secrets.token_hex(2).upper()
        return f"CAS-{date_str}-{rand_suffix}"

    @classmethod
    def log_timeline_event(
        cls,
        db: Session,
        customer_id: uuid.UUID,
        event_type: str,
        title: str,
        details: Optional[Dict[str, Any]] = None,
        actor_email: Optional[str] = None
    ) -> CustomerInteraction:
        interaction = CustomerInteraction(
            id=uuid.uuid4(),
            customer_id=customer_id,
            event_type=event_type.upper(),
            title=title,
            details=details or {},
            actor_email=actor_email,
            created_at=datetime.now(timezone.utc)
        )
        db.add(interaction)
        db.commit()
        return interaction

    @classmethod
    def create_customer(cls, db: Session, payload: CustomerCreate, actor_email: str) -> Dict[str, Any]:
        existing_profile = db.scalar(select(Profile).where(Profile.email == payload.email))
        if existing_profile:
            raise HTTPException(status_code=400, detail=f"Customer profile with email '{payload.email}' already exists.")

        try:
            tier_enum = VipTier(payload.vip_tier.upper()) if payload.vip_tier else VipTier.REGULAR
        except ValueError:
            valid_tiers = [t.value for t in VipTier]
            raise HTTPException(status_code=400, detail=f"Invalid VIP tier '{payload.vip_tier}'. Valid tiers: {valid_tiers}")

        # Create user auth record if missing
        user_auth = db.scalar(select(UserAuth).where(UserAuth.email == payload.email))
        if not user_auth:
            user_auth = UserAuth(
                id=uuid.uuid4(),
                email=payload.email,
                password_hash="ShafskyCustomerPlaceholderHash2026",
                role=Role.CUSTOMER,
                is_verified=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(user_auth)
            db.commit()

        # Create profile record
        is_vip = tier_enum in [VipTier.VVIP, VipTier.VIP, VipTier.DIPLOMATIC, VipTier.PRIVATE_CHARTER]
        profile = Profile(
            id=uuid.uuid4(),
            auth_id=user_auth.id,
            email=payload.email,
            full_name=payload.full_name,
            phone_number=payload.phone_number,
            company=payload.company,
            role=Role.CUSTOMER,
            vip_status=is_vip,
            vip_tier=tier_enum,
            passport_number=payload.passport_number,
            tags=payload.tags or {},
            documents_config=payload.documents_config or {},
            notes=payload.notes,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db.add(profile)
        db.commit()

        cls.log_timeline_event(
            db,
            customer_id=profile.id,
            event_type="CUSTOMER_CREATED",
            title="Customer Profile Created",
            details={"email": profile.email, "vipTier": profile.vip_tier.value},
            actor_email=actor_email
        )

        return cls.format_customer_dict(profile)

    @classmethod
    def update_customer(cls, db: Session, customer_id: str, payload: CustomerUpdate, actor_email: str) -> Dict[str, Any]:
        try:
            p_uuid = uuid.UUID(customer_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid customer UUID format.")

        profile = db.scalar(select(Profile).where(Profile.id == p_uuid, Profile.deleted_at.is_(None)))
        if not profile:
            raise HTTPException(status_code=404, detail="Customer profile not found.")

        if payload.full_name is not None:
            profile.full_name = payload.full_name
        if payload.phone_number is not None:
            profile.phone_number = payload.phone_number
        if payload.company is not None:
            profile.company = payload.company
        if payload.passport_number is not None:
            profile.passport_number = payload.passport_number
        if payload.notes is not None:
            profile.notes = payload.notes
        if payload.tags is not None:
            profile.tags = payload.tags
        if payload.documents_config is not None:
            profile.documents_config = payload.documents_config

        if payload.vip_tier is not None:
            try:
                tier_enum = VipTier(payload.vip_tier.upper())
                profile.vip_tier = tier_enum
                profile.vip_status = tier_enum in [VipTier.VVIP, VipTier.VIP, VipTier.DIPLOMATIC, VipTier.PRIVATE_CHARTER]
            except ValueError:
                valid_tiers = [t.value for t in VipTier]
                raise HTTPException(status_code=400, detail=f"Invalid VIP tier '{payload.vip_tier}'. Valid tiers: {valid_tiers}")

        profile.updated_at = datetime.now(timezone.utc)
        db.commit()

        cls.log_timeline_event(
            db,
            customer_id=profile.id,
            event_type="PROFILE_UPDATED",
            title="Customer Profile Updated",
            details={"updatedBy": actor_email},
            actor_email=actor_email
        )

        return cls.format_customer_dict(profile)

    @classmethod
    def soft_delete_customer(cls, db: Session, customer_id: str, actor_email: str) -> Dict[str, Any]:
        try:
            p_uuid = uuid.UUID(customer_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid customer UUID format.")

        profile = db.scalar(select(Profile).where(Profile.id == p_uuid, Profile.deleted_at.is_(None)))
        if not profile:
            raise HTTPException(status_code=404, detail="Customer profile not found.")

        profile.deleted_at = datetime.now(timezone.utc)
        db.commit()

        cls.log_timeline_event(
            db,
            customer_id=profile.id,
            event_type="CUSTOMER_DELETED",
            title="Customer Account Soft-Deleted",
            details={"deletedBy": actor_email},
            actor_email=actor_email
        )

        return {"message": f"Customer '{customer_id}' successfully soft-deleted."}

    @classmethod
    def search_customers(
        cls,
        db: Session,
        query: Optional[str] = None,
        vip_tier: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        stmt = select(Profile).where(Profile.deleted_at.is_(None))

        if vip_tier:
            try:
                tier_enum = VipTier(vip_tier.upper())
                stmt = stmt.where(Profile.vip_tier == tier_enum)
            except ValueError:
                pass

        if query:
            search_pattern = f"%{query}%"
            stmt = stmt.where(
                or_(
                    Profile.full_name.ilike(search_pattern),
                    Profile.email.ilike(search_pattern),
                    Profile.phone_number.ilike(search_pattern),
                    Profile.company.ilike(search_pattern),
                    Profile.passport_number.ilike(search_pattern)
                )
            )

        stmt = stmt.order_by(desc(Profile.created_at)).limit(limit)
        profiles = list(db.scalars(stmt).all())
        return [cls.format_customer_dict(p) for p in profiles]

    @classmethod
    def get_customer_details_and_stats(cls, db: Session, customer_id: str) -> Dict[str, Any]:
        try:
            p_uuid = uuid.UUID(customer_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid customer UUID format.")

        profile = db.scalar(select(Profile).where(Profile.id == p_uuid, Profile.deleted_at.is_(None)))
        if not profile:
            raise HTTPException(status_code=404, detail="Customer profile not found.")

        # Aggregate Travel Statistics & Booking Categories
        bookings = list(db.scalars(
            select(Booking).where(
                or_(
                    Booking.user_id == profile.id,
                    Booking.passenger_email == profile.email
                )
            ).order_by(desc(Booking.created_at))
        ).all())

        completed_count = 0
        upcoming_count = 0
        cancelled_count = 0
        total_spent = 0.0
        airports_set = set()

        previous_bookings = []
        upcoming_bookings = []
        cancelled_bookings = []

        now = datetime.now(timezone.utc)

        for b in bookings:
            b_dict = {
                "bookingRef": b.booking_ref,
                "flightNum": b.flight_num,
                "originCode": b.origin_code,
                "destCode": b.dest_code,
                "departureTime": b.departure_time.isoformat(),
                "totalAmount": float(b.total_amount),
                "status": b.status.value if isinstance(b.status, BookingStatus) else str(b.status)
            }
            airports_set.add(b.origin_code)
            airports_set.add(b.dest_code)

            if b.status == BookingStatus.COMPLETED:
                completed_count += 1
                total_spent += float(b.total_amount)
                previous_bookings.append(b_dict)
            elif b.status == BookingStatus.CANCELLED:
                cancelled_count += 1
                cancelled_bookings.append(b_dict)
            else:
                upcoming_count += 1
                total_spent += float(b.total_amount)
                upcoming_bookings.append(b_dict)

        customer_dict = cls.format_customer_dict(profile)
        customer_dict["travelStatistics"] = {
            "completedBookingsCount": completed_count,
            "upcomingBookingsCount": upcoming_count,
            "cancelledBookingsCount": cancelled_count,
            "totalSpentINR": total_spent,
            "frequentAirports": list(airports_set)
        }
        customer_dict["previousBookings"] = previous_bookings
        customer_dict["upcomingBookings"] = upcoming_bookings
        customer_dict["cancelledBookings"] = cancelled_bookings

        return customer_dict

    @classmethod
    def create_case(cls, db: Session, payload: CaseCreate, actor_email: str) -> Dict[str, Any]:
        try:
            c_uuid = uuid.UUID(payload.customer_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid customer_id format.")

        profile = db.scalar(select(Profile).where(Profile.id == c_uuid, Profile.deleted_at.is_(None)))
        if not profile:
            raise HTTPException(status_code=404, detail="Customer not found.")

        assigned_uuid = None
        if payload.assigned_to_id:
            try:
                assigned_uuid = uuid.UUID(payload.assigned_to_id)
            except ValueError:
                pass

        case_num = cls.generate_case_number()
        case = CustomerCase(
            id=uuid.uuid4(),
            case_number=case_num,
            customer_id=profile.id,
            title=payload.title,
            description=payload.description,
            category=payload.category or "GENERAL",
            status=CaseStatus.OPEN,
            assigned_to_id=assigned_uuid,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db.add(case)
        db.commit()

        cls.log_timeline_event(
            db,
            customer_id=profile.id,
            event_type="CASE_CREATED",
            title=f"Support Case Created ({case_num})",
            details={"caseNumber": case_num, "title": case.title},
            actor_email=actor_email
        )

        return cls.format_case_dict(case)

    @classmethod
    def update_case(cls, db: Session, case_id: str, payload: CaseUpdate, actor_email: str) -> Dict[str, Any]:
        stmt = select(CustomerCase)
        try:
            c_uuid = uuid.UUID(case_id)
            stmt = stmt.where(or_(CustomerCase.id == c_uuid, CustomerCase.case_number == case_id))
        except ValueError:
            stmt = stmt.where(CustomerCase.case_number == case_id)

        case = db.scalar(stmt)
        if not case:
            raise HTTPException(status_code=404, detail="Case ticket not found.")

        if payload.status:
            try:
                case.status = CaseStatus(payload.status.upper())
            except ValueError:
                valid_statuses = [s.value for s in CaseStatus]
                raise HTTPException(status_code=400, detail=f"Invalid case status '{payload.status}'. Valid: {valid_statuses}")

        if payload.assigned_to_id:
            try:
                case.assigned_to_id = uuid.UUID(payload.assigned_to_id)
            except ValueError:
                pass

        if payload.resolution_notes:
            case.resolution_notes = payload.resolution_notes

        case.updated_at = datetime.now(timezone.utc)
        db.commit()

        cls.log_timeline_event(
            db,
            customer_id=case.customer_id,
            event_type="CASE_UPDATED",
            title=f"Support Case Updated ({case.case_number})",
            details={"status": case.status.value, "updatedBy": actor_email},
            actor_email=actor_email
        )

        return cls.format_case_dict(case)

    @classmethod
    def list_cases(cls, db: Session, status: Optional[str] = None) -> List[Dict[str, Any]]:
        stmt = select(CustomerCase)
        if status:
            try:
                status_enum = CaseStatus(status.upper())
                stmt = stmt.where(CustomerCase.status == status_enum)
            except ValueError:
                pass

        stmt = stmt.order_by(desc(CustomerCase.created_at))
        cases = list(db.scalars(stmt).all())
        return [cls.format_case_dict(c) for c in cases]

    @classmethod
    def get_customer_timeline(cls, db: Session, customer_id: str) -> List[Dict[str, Any]]:
        try:
            p_uuid = uuid.UUID(customer_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid customer UUID format.")

        interactions = list(db.scalars(
            select(CustomerInteraction)
            .where(CustomerInteraction.customer_id == p_uuid)
            .order_by(desc(CustomerInteraction.created_at))
        ).all())

        return [
            {
                "id": str(i.id),
                "eventType": i.event_type,
                "title": i.title,
                "details": i.details,
                "actorEmail": i.actor_email,
                "timestamp": i.created_at.isoformat()
            }
            for i in interactions
        ]

    @classmethod
    def get_crm_stats(cls, db: Session) -> Dict[str, Any]:
        total_customers = db.scalar(select(func.count(Profile.id)).where(Profile.deleted_at.is_(None))) or 0
        vip_customers = db.scalar(select(func.count(Profile.id)).where(Profile.deleted_at.is_(None), Profile.vip_status.is_(True))) or 0
        open_cases = db.scalar(select(func.count(CustomerCase.id)).where(CustomerCase.status == CaseStatus.OPEN)) or 0

        tier_counts = db.execute(
            select(Profile.vip_tier, func.count(Profile.id))
            .where(Profile.deleted_at.is_(None))
            .group_by(Profile.vip_tier)
        ).all()

        vip_breakdown = {t.value if isinstance(t, VipTier) else str(t): count for t, count in tier_counts}

        return {
            "totalCustomers": total_customers,
            "vipCustomersCount": vip_customers,
            "openCasesCount": open_cases,
            "vipTierBreakdown": vip_breakdown
        }

    @classmethod
    def format_customer_dict(cls, profile: Profile) -> Dict[str, Any]:
        return {
            "id": str(profile.id),
            "email": profile.email,
            "fullName": profile.full_name,
            "phoneNumber": profile.phone_number,
            "company": profile.company,
            "vipStatus": profile.vip_status,
            "vipTier": profile.vip_tier.value if isinstance(profile.vip_tier, VipTier) else str(profile.vip_tier),
            "passportNumber": profile.passport_number,
            "tags": profile.tags or {},
            "documentsConfig": profile.documents_config or {},
            "notes": profile.notes,
            "createdAt": profile.created_at.isoformat()
        }

    @classmethod
    def format_case_dict(cls, case: CustomerCase) -> Dict[str, Any]:
        return {
            "id": str(case.id),
            "caseNumber": case.case_number,
            "customerId": str(case.customer_id),
            "title": case.title,
            "description": case.description,
            "category": case.category,
            "status": case.status.value if isinstance(case.status, CaseStatus) else str(case.status),
            "assignedToId": str(case.assigned_to_id) if case.assigned_to_id else None,
            "resolutionNotes": case.resolution_notes,
            "createdAt": case.created_at.isoformat()
        }
