"""Booking recycle bin: soft-delete, restore, and Super Admin hard purge."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import or_, select, func, desc, text, delete
from sqlalchemy.orm import Session

from app.models.schema import AuditLog, Booking, BookingStatus
from app.models.payment import PaymentTransaction
from app.models.operations_models import OperationsQueue
from app.services.booking_service import BookingService

RECYCLE_ACTIONS = ("BOOKING_RECYCLED", "BOOKING_RESTORED", "BOOKING_PURGED")


class BookingRecycleService:
    @staticmethod
    def actor_from_context(user_context: Optional[Dict[str, Any]]) -> Tuple[Optional[uuid.UUID], str, str]:
        ctx = user_context or {}
        email = (ctx.get("sub") or ctx.get("email") or "").strip().lower()
        role = str(ctx.get("role") or "")
        raw_id = ctx.get("user_id") or ctx.get("userId")
        uid: Optional[uuid.UUID] = None
        if raw_id:
            try:
                uid = uuid.UUID(str(raw_id))
            except ValueError:
                uid = None
        return uid, email, role

    @classmethod
    def get_booking(cls, db: Session, identifier: str, *, include_deleted: bool = False) -> Booking:
        stmt = select(Booking)
        if not include_deleted:
            stmt = stmt.where(Booking.deleted_at.is_(None))
        try:
            val_uuid = uuid.UUID(identifier)
            stmt = stmt.where(or_(Booking.id == val_uuid, Booking.booking_ref == identifier))
        except ValueError:
            stmt = stmt.where(Booking.booking_ref == identifier)
        booking = db.scalar(stmt)
        if not booking:
            raise HTTPException(status_code=404, detail=f"Booking '{identifier}' not found.")
        return booking

    @classmethod
    def format_bin_item(
        cls,
        booking: Booking,
        *,
        viewer_is_super_admin: bool,
    ) -> Dict[str, Any]:
        payload = BookingService.format_booking_dict(booking)
        payload["deletedAt"] = booking.deleted_at.isoformat() if booking.deleted_at else None
        if viewer_is_super_admin:
            payload["deletedByUserId"] = str(booking.deleted_by_user_id) if booking.deleted_by_user_id else None
            payload["deletedByEmail"] = booking.deleted_by_email
            payload["deletedByRole"] = booking.deleted_by_role
        else:
            payload["deletedByUserId"] = None
            payload["deletedByEmail"] = None
            payload["deletedByRole"] = None
        return payload

    @classmethod
    def _write_audit(
        cls,
        db: Session,
        *,
        action: str,
        booking: Booking,
        actor_id: Optional[uuid.UUID],
        actor_email: str,
        actor_role: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        details = {
            "bookingRef": booking.booking_ref,
            "passengerName": booking.passenger_name,
            "passengerEmail": booking.passenger_email,
            "status": booking.status.value if isinstance(booking.status, BookingStatus) else str(booking.status),
            "totalAmount": float(booking.total_amount) if booking.total_amount is not None else None,
            "actorRole": actor_role,
            "originalDeletedByUserId": str(booking.deleted_by_user_id) if booking.deleted_by_user_id else None,
            "originalDeletedByEmail": booking.deleted_by_email,
            "originalDeletedByRole": booking.deleted_by_role,
        }
        if extra:
            details.update(extra)
        db.add(
            AuditLog(
                id=uuid.uuid4(),
                actor_id=actor_id,
                actor_email=actor_email or "unknown",
                action=action,
                resource_type="BOOKING",
                resource_id=booking.booking_ref,
                details=details,
                created_at=datetime.now(timezone.utc),
            )
        )

    @classmethod
    def recycle_booking(
        cls,
        db: Session,
        identifier: str,
        user_context: Dict[str, Any],
    ) -> Booking:
        booking = cls.get_booking(db, identifier, include_deleted=False)
        actor_id, actor_email, actor_role = cls.actor_from_context(user_context)
        now = datetime.now(timezone.utc)
        booking.deleted_at = now
        booking.deleted_by_user_id = actor_id
        booking.deleted_by_email = actor_email or "unknown"
        booking.deleted_by_role = actor_role or "ADMIN"
        booking.updated_at = now
        cls._write_audit(
            db,
            action="BOOKING_RECYCLED",
            booking=booking,
            actor_id=actor_id,
            actor_email=actor_email,
            actor_role=actor_role,
        )
        db.commit()
        db.refresh(booking)
        return booking

    @classmethod
    def restore_booking(
        cls,
        db: Session,
        identifier: str,
        user_context: Dict[str, Any],
    ) -> Booking:
        booking = cls.get_booking(db, identifier, include_deleted=True)
        if booking.deleted_at is None:
            raise HTTPException(status_code=400, detail="Booking is not in the recycle bin.")
        actor_id, actor_email, actor_role = cls.actor_from_context(user_context)
        cls._write_audit(
            db,
            action="BOOKING_RESTORED",
            booking=booking,
            actor_id=actor_id,
            actor_email=actor_email,
            actor_role=actor_role,
        )
        now = datetime.now(timezone.utc)
        booking.deleted_at = None
        booking.deleted_by_user_id = None
        booking.deleted_by_email = None
        booking.deleted_by_role = None
        booking.updated_at = now
        db.commit()
        db.refresh(booking)
        return booking

    @staticmethod
    def _safe_execute(db: Session, sql: str, params: Dict[str, Any]) -> int:
        try:
            with db.begin_nested():
                result = db.execute(text(sql), params)
                return int(result.rowcount or 0)
        except Exception:
            return 0

    @classmethod
    def _purge_related(cls, db: Session, booking: Booking) -> Dict[str, int]:
        ref = booking.booking_ref
        booking_id = str(booking.id)
        removed = {
            "payments": 0,
            "notifications": 0,
            "ops_queue": 0,
            "notification_logs": 0,
        }

        txs = db.scalars(
            select(PaymentTransaction).where(
                or_(
                    PaymentTransaction.entity_id == ref,
                    PaymentTransaction.transaction_ref == ref,
                )
            )
        ).all()
        removed["payments"] = len(txs)
        for tx in txs:
            db.delete(tx)

        ops_count = db.scalar(
            select(func.count()).select_from(OperationsQueue).where(
                OperationsQueue.booking_reference == ref
            )
        ) or 0
        removed["ops_queue"] = int(ops_count)
        if ops_count:
            db.execute(delete(OperationsQueue).where(OperationsQueue.booking_reference == ref))

        removed["notifications"] = cls._safe_execute(
            db,
            """
            DELETE FROM notification_records
            WHERE COALESCE(payload->>'booking_ref', payload->>'bookingRef', '') = :ref
            """,
            {"ref": ref},
        )
        removed["notification_logs"] = cls._safe_execute(
            db,
            "DELETE FROM notification_logs WHERE booking_ref = :ref OR booking_id = :bid",
            {"ref": ref, "bid": booking_id},
        )

        for table in (
            "assignments",
            "timeline_entries",
            "notes",
            "attachments",
            "sla_instances",
            "workflow_instances",
        ):
            cls._safe_execute(
                db,
                f"DELETE FROM {table} WHERE entity_id IN (:ref, :bid)",
                {"ref": ref, "bid": booking_id},
            )

        return removed

    @classmethod
    def purge_booking(
        cls,
        db: Session,
        identifier: str,
        user_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        booking = cls.get_booking(db, identifier, include_deleted=True)
        if booking.deleted_at is None:
            raise HTTPException(
                status_code=400,
                detail="Move the booking to the recycle bin before permanently deleting it.",
            )
        actor_id, actor_email, actor_role = cls.actor_from_context(user_context)
        snapshot = {
            "bookingId": str(booking.id),
            "bookingRef": booking.booking_ref,
            "passengerName": booking.passenger_name,
            "passengerEmail": booking.passenger_email,
        }
        related = cls._purge_related(db, booking)
        cls._write_audit(
            db,
            action="BOOKING_PURGED",
            booking=booking,
            actor_id=actor_id,
            actor_email=actor_email,
            actor_role=actor_role,
            extra={"relatedRemoved": related, **snapshot},
        )
        db.delete(booking)
        db.commit()
        return {**snapshot, "relatedRemoved": related}

    @classmethod
    def list_bin(
        cls,
        db: Session,
        *,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> Tuple[List[Booking], int]:
        stmt = select(Booking).where(
            Booking.deleted_at.isnot(None),
            Booking.deleted_by_email.isnot(None),
        )
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Booking.booking_ref.ilike(pattern),
                    Booking.passenger_name.ilike(pattern),
                    Booking.passenger_email.ilike(pattern),
                    Booking.deleted_by_email.ilike(pattern),
                )
            )
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        page = max(1, int(page or 1))
        page_size = max(1, min(100, int(page_size or 25)))
        rows = db.scalars(
            stmt.order_by(desc(Booking.deleted_at)).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return list(rows), int(total)

    @classmethod
    def list_deletion_log(
        cls,
        db: Session,
        *,
        page: int = 1,
        page_size: int = 25,
    ) -> Tuple[List[AuditLog], int]:
        stmt = select(AuditLog).where(AuditLog.action.in_(RECYCLE_ACTIONS))
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        page = max(1, int(page or 1))
        page_size = max(1, min(100, int(page_size or 25)))
        rows = db.scalars(
            stmt.order_by(desc(AuditLog.created_at)).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return list(rows), int(total)

    @staticmethod
    def format_deletion_log(entry: AuditLog) -> Dict[str, Any]:
        details = entry.details or {}
        return {
            "id": str(entry.id),
            "action": entry.action,
            "bookingRef": entry.resource_id,
            "actorId": str(entry.actor_id) if entry.actor_id else None,
            "actorEmail": entry.actor_email,
            "actorRole": details.get("actorRole"),
            "passengerName": details.get("passengerName"),
            "passengerEmail": details.get("passengerEmail"),
            "originalDeletedByUserId": details.get("originalDeletedByUserId"),
            "originalDeletedByEmail": details.get("originalDeletedByEmail"),
            "createdAt": entry.created_at.isoformat() if entry.created_at else None,
            "details": details,
        }
