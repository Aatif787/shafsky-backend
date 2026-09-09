import secrets
import logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, or_, func

from app.models.charter_models import PrivateCharterRequest, CharterRequestStatus
from app.schemas.charter import PrivateCharterRequestCreate, PrivateCharterAdminUpdate
from app.monitoring.logging import structured_logger

logger = logging.getLogger(__name__)


class CharterService:

    @classmethod
    def generate_charter_reference(cls, db: Session) -> str:
        """
        Generate a collision-safe, high-entropy unique reference format 'SC-YYYYMMDD-XXXXXXXX'.
        Uses cryptographic random hex to eliminate predictable sequential guessing.
        """
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        for _ in range(50):
            token = secrets.token_hex(4).upper()
            ref = f"SC-{date_str}-{token}"
            existing = db.execute(
                select(PrivateCharterRequest.id).where(PrivateCharterRequest.request_reference == ref)
            ).scalar_one_or_none()
            if not existing:
                return ref

        # Fallback with milliseconds timestamp
        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        return f"SC-{date_str}-{secrets.token_hex(2).upper()}-{str(ts)[-4:]}"

    @classmethod
    def create_charter_request(
        cls,
        db: Session,
        payload: PrivateCharterRequestCreate,
        client_ip: Optional[str] = None,
    ) -> PrivateCharterRequest:
        """
        Create and persist a new Private Charter request in status REQUESTED.
        Submissions are 100% free with zero payment processing.
        """
        reference = cls.generate_charter_reference(db)

        # Convert Pydantic submodels to dicts
        itinerary_data = [leg.model_dump() for leg in payload.itinerary]
        passengers_data = payload.passengers.model_dump()

        request_record = PrivateCharterRequest(
            request_reference=reference,
            customer_name=payload.customer_name.strip(),
            country_code=payload.country_code.strip(),
            phone=payload.phone.strip(),
            email=payload.email.strip().lower(),
            company=payload.company.strip() if payload.company else None,
            preferred_contact_method=payload.preferred_contact_method,
            trip_type=payload.trip_type,
            origin=payload.origin.strip(),
            destination=payload.destination.strip(),
            departure_date=payload.departure_date.strip(),
            departure_time=payload.departure_time.strip() if payload.departure_time else None,
            return_date=payload.return_date.strip() if payload.return_date else None,
            return_time=payload.return_time.strip() if payload.return_time else None,
            itinerary=itinerary_data,
            passengers=passengers_data,
            aircraft_preference=payload.aircraft_preference,
            travel_requirements=payload.travel_requirements,
            special_requests=payload.special_requests.strip() if payload.special_requests else None,
            status=CharterRequestStatus.REQUESTED,
            client_ip=client_ip,
        )

        db.add(request_record)
        db.commit()
        db.refresh(request_record)

        structured_logger.info(
            "Private Charter Request Created",
            extra={
                "event": "charter_request_created",
                "request_reference": reference,
                "customer_name": request_record.customer_name,
                "email": request_record.email,
                "trip_type": request_record.trip_type,
                "origin": request_record.origin,
                "destination": request_record.destination,
                "status": request_record.status.value,
            },
        )

        # Optional: Dispatch internal notification record
        try:
            cls._dispatch_charter_notification(db, request_record)
        except Exception as notify_err:
            logger.warning("Charter notification dispatch skipped or failed: %s", str(notify_err))

        return request_record

    @classmethod
    def _dispatch_charter_notification(cls, db: Session, req: PrivateCharterRequest) -> None:
        """
        Record a Private Charter notification in notification_records if table exists.
        Completely isolated from normal airport-service templates.
        """
        try:
            from app.models.schema import NotificationRecord, NotificationStatus
            note = NotificationRecord(
                recipient_email=req.email,
                recipient_phone=f"{req.country_code}{req.phone}",
                template_type="PRIVATE_CHARTER_REQUEST_RECEIVED",
                channel="EMAIL_WHATSAPP",
                payload={
                    "request_reference": req.request_reference,
                    "customer_name": req.customer_name,
                    "origin": req.origin,
                    "destination": req.destination,
                    "departure_date": req.departure_date,
                    "aircraft_preference": req.aircraft_preference,
                    "passengers_total": req.passengers.get("total", 1),
                    "status": req.status.value,
                },
                status=NotificationStatus.QUEUED,
            )
            db.add(note)
            db.commit()
        except Exception:
            db.rollback()

    @classmethod
    def get_by_reference(cls, db: Session, reference: str) -> Optional[PrivateCharterRequest]:
        clean_ref = reference.strip().upper()
        return db.execute(
            select(PrivateCharterRequest).where(
                func.upper(PrivateCharterRequest.request_reference) == clean_ref
            )
        ).scalar_one_or_none()

    @classmethod
    def get_by_id(cls, db: Session, request_id: str) -> Optional[PrivateCharterRequest]:
        import uuid
        try:
            uid = uuid.UUID(request_id)
        except ValueError:
            return None
        return db.execute(
            select(PrivateCharterRequest).where(PrivateCharterRequest.id == uid)
        ).scalar_one_or_none()

    @classmethod
    def list_charter_requests(
        cls,
        db: Session,
        status: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[PrivateCharterRequest], int]:
        query = select(PrivateCharterRequest)

        if status:
            try:
                status_enum = CharterRequestStatus(status.upper())
                query = query.where(PrivateCharterRequest.status == status_enum)
            except ValueError:
                pass

        if search:
            search_term = f"%{search.strip()}%"
            query = query.where(
                or_(
                    PrivateCharterRequest.request_reference.ilike(search_term),
                    PrivateCharterRequest.customer_name.ilike(search_term),
                    PrivateCharterRequest.email.ilike(search_term),
                    PrivateCharterRequest.phone.ilike(search_term),
                    PrivateCharterRequest.origin.ilike(search_term),
                    PrivateCharterRequest.destination.ilike(search_term),
                )
            )

        # Count total
        count_stmt = select(func.count()).select_from(query.subquery())
        total = db.scalar(count_stmt) or 0

        # Execute paginated
        query = query.order_by(desc(PrivateCharterRequest.created_at)).offset(skip).limit(limit)
        results = db.execute(query).scalars().all()
        return list(results), total

    @classmethod
    def update_charter_request(
        cls,
        db: Session,
        request_id: str,
        update_data: PrivateCharterAdminUpdate,
    ) -> Optional[PrivateCharterRequest]:
        req = cls.get_by_id(db, request_id)
        if not req:
            return None

        if update_data.status is not None:
            req.status = update_data.status
        if update_data.assigned_staff_id is not None:
            req.assigned_staff_id = update_data.assigned_staff_id
        if update_data.assigned_staff_name is not None:
            req.assigned_staff_name = update_data.assigned_staff_name
        if update_data.internal_notes is not None:
            req.internal_notes = update_data.internal_notes

        req.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(req)

        structured_logger.info(
            "Private Charter Request Updated",
            extra={
                "event": "charter_request_updated",
                "request_reference": req.request_reference,
                "status": req.status.value,
                "assigned_staff": req.assigned_staff_name,
            },
        )
        return req
