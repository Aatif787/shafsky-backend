"""Super Admin configuration for WhatsApp notification recipients and routes.

This service writes notification_recipients, notification_service_routes, and
notification_settings. Live operations dispatch reads the settings row once
per event and, only when routing_mode is database, the active service routes.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import Base
from app.models.notification_routing import (
    NOTIFICATION_SETTINGS_ROW_ID,
    ROUTING_MODE_DATABASE,
    ROUTING_MODE_ENV,
    NotificationRecipient,
    NotificationServiceRoute,
    NotificationSettings,
)
from app.models.schema import AuditLog, UserAuth
from app.schemas.notification_routing import (
    NOTIFICATION_SERVICE_KEYS,
    NOTIFICATION_SERVICE_KEY_SET,
    RecipientCreate,
    RecipientUpdate,
    ServiceRouteUpdateRequest,
    normalize_recipient_phone,
)

logger = logging.getLogger(__name__)

RESOURCE_TYPE = "WHATSAPP_NOTIFY_CONFIG"
ENV_PHONE_FIELDS = (
    "WHATSAPP_BOOKING_NOTIFY_PHONE_1",
    "WHATSAPP_BOOKING_NOTIFY_PHONE_2",
    "WHATSAPP_BOOKING_NOTIFY_PHONE_3",
    "WHATSAPP_BOOKING_NOTIFY_PHONE_4",
    "WHATSAPP_BOOKING_NOTIFY_PHONE_5",
)

ACTION_RECIPIENT_ADDED = "RECIPIENT_ADDED"
ACTION_RECIPIENT_EDITED = "RECIPIENT_EDITED"
ACTION_RECIPIENT_ENABLED = "RECIPIENT_ENABLED"
ACTION_RECIPIENT_DISABLED = "RECIPIENT_DISABLED"
ACTION_RECIPIENT_DELETED = "RECIPIENT_DELETED"
ACTION_SERVICE_ROUTING_CHANGED = "SERVICE_ROUTING_CHANGED"
ACTION_ROUTING_MODE_CHANGED = "ROUTING_MODE_CHANGED"
ACTION_ENV_RECIPIENTS_IMPORTED = "ENV_RECIPIENTS_IMPORTED"


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _as_uuid(value: Any) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


_ROUTING_TABLES = (
    NotificationRecipient.__table__,
    NotificationServiceRoute.__table__,
    NotificationSettings.__table__,
)
_schema_ready = False


class NotificationRoutingService:
    @staticmethod
    def ensure_schema(db: Session) -> None:
        """Create the routing tables when a deploy skipped migration d3e4f5a6b7c8.

        Same DDL and names as that migration, so a later alembic upgrade skips
        the tables. No settings row is written here; reads create it as env.
        """
        global _schema_ready
        if _schema_ready:
            return
        bind = db.get_bind()
        try:
            existing = set(inspect(bind).get_table_names())
            missing = [table for table in _ROUTING_TABLES if table.name not in existing]
            if missing:
                logger.warning(
                    "Notification routing tables missing (%s). Creating them.",
                    ", ".join(table.name for table in missing),
                )
                Base.metadata.create_all(bind=bind, tables=missing, checkfirst=True)
        except Exception:
            logger.exception("Notification routing tables could not be created.")
            existing = set(inspect(bind).get_table_names())
            if any(table.name not in existing for table in _ROUTING_TABLES):
                raise
        _schema_ready = True

    @staticmethod
    def _postgres(db: Session) -> bool:
        bind = db.get_bind()
        return bind is not None and bind.dialect.name == "postgresql"

    @classmethod
    def _lock(cls, stmt, db: Session):
        if cls._postgres(db):
            return stmt.with_for_update()
        return stmt

    @classmethod
    def _require_actor(cls, db: Session, actor: Dict[str, Any]) -> tuple[uuid.UUID, str]:
        email = str(actor.get("email") or "").strip()
        raw_id = actor.get("user_id") or actor.get("sub")
        try:
            actor_id = uuid.UUID(str(raw_id))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="Authenticated user id is required.") from exc
        if not email:
            raise HTTPException(status_code=401, detail="Authenticated user email is required.")
        exists = db.scalar(select(UserAuth.id).where(UserAuth.id == actor_id))
        if exists is None:
            raise HTTPException(status_code=401, detail="Authenticated user was not found.")
        return actor_id, email

    @classmethod
    def _audit(
        cls,
        db: Session,
        *,
        actor_id: uuid.UUID,
        actor_email: str,
        action: str,
        resource_id: str,
        previous: Optional[Dict[str, Any]],
        new: Optional[Dict[str, Any]],
        ip_address: Optional[str],
    ) -> None:
        db.add(
            AuditLog(
                id=uuid.uuid4(),
                actor_id=actor_id,
                actor_email=actor_email,
                action=action,
                resource_type=RESOURCE_TYPE,
                resource_id=resource_id,
                details=_json_safe({"previous": previous, "new": new}),
                ip_address=ip_address,
                created_at=datetime.now(timezone.utc),
            )
        )

    @staticmethod
    def _conflict(exc: IntegrityError) -> HTTPException:
        text = str(getattr(exc, "orig", exc)).lower()
        if "phone" in text:
            detail = "A recipient with this phone number already exists."
        elif "user_auth" in text or "actor" in text:
            detail = "Acting Super Admin is not a registered user."
            return HTTPException(status_code=400, detail=detail)
        elif "service" in text:
            detail = "This service assignment already exists."
        else:
            detail = "Configuration update conflicted with another change. Retry."
        return HTTPException(status_code=409, detail=detail)

    @classmethod
    def _persist(cls, db: Session) -> None:
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise cls._conflict(exc) from exc

    @classmethod
    def _settings(cls, db: Session, *, lock: bool = False) -> tuple[NotificationSettings, bool]:
        stmt = cls._lock(
            select(NotificationSettings).where(NotificationSettings.id == NOTIFICATION_SETTINGS_ROW_ID),
            db if lock else db,
        )
        if not lock:
            stmt = select(NotificationSettings).where(NotificationSettings.id == NOTIFICATION_SETTINGS_ROW_ID)
        row = db.scalars(stmt).first()
        if row is not None:
            return row, False
        row = NotificationSettings(
            id=NOTIFICATION_SETTINGS_ROW_ID,
            routing_mode=ROUTING_MODE_ENV,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            row = db.scalars(
                select(NotificationSettings).where(NotificationSettings.id == NOTIFICATION_SETTINGS_ROW_ID)
            ).first()
            if row is None:
                raise
            return row, False
        return row, True

    @staticmethod
    def _require_service_key(service_key: str) -> str:
        key = (service_key or "").strip()
        if key not in NOTIFICATION_SERVICE_KEY_SET:
            raise HTTPException(status_code=400, detail=f"Unknown service_key '{service_key}'.")
        return key

    @staticmethod
    def _recipient_dict(row: NotificationRecipient) -> Dict[str, Any]:
        return {
            "id": row.id,
            "label": row.label,
            "phone_number": row.phone_number,
            "is_active": bool(row.is_active),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _snapshot(row: NotificationRecipient) -> Dict[str, Any]:
        return {
            "id": str(row.id),
            "label": row.label,
            "phone_number": row.phone_number,
            "is_active": bool(row.is_active),
        }

    @classmethod
    def _load_recipient(cls, db: Session, recipient_id: uuid.UUID, *, lock: bool) -> NotificationRecipient:
        stmt = select(NotificationRecipient).where(NotificationRecipient.id == recipient_id)
        if lock:
            stmt = cls._lock(stmt, db)
        row = db.scalars(stmt).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Recipient not found.")
        return row

    @classmethod
    def list_service_keys(cls) -> List[Dict[str, str]]:
        return [{"service_key": key} for key in NOTIFICATION_SERVICE_KEYS]

    @classmethod
    def list_recipients(cls, db: Session) -> List[Dict[str, Any]]:
        rows = db.scalars(
            select(NotificationRecipient).order_by(NotificationRecipient.created_at, NotificationRecipient.id)
        ).all()
        return [cls._recipient_dict(row) for row in rows]

    @classmethod
    def add_recipient(
        cls,
        db: Session,
        payload: RecipientCreate,
        actor: Dict[str, Any],
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        actor_id, email = cls._require_actor(db, actor)
        existing = db.scalar(
            select(NotificationRecipient.id).where(NotificationRecipient.phone_number == payload.phone_number)
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="A recipient with this phone number already exists.")
        row = NotificationRecipient(
            label=payload.label,
            phone_number=payload.phone_number,
            is_active=payload.is_active,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise cls._conflict(exc) from exc
        cls._audit(
            db,
            actor_id=actor_id,
            actor_email=email,
            action=ACTION_RECIPIENT_ADDED,
            resource_id=str(row.id),
            previous=None,
            new=cls._snapshot(row),
            ip_address=ip_address,
        )
        cls._persist(db)
        db.refresh(row)
        return cls._recipient_dict(row)

    @classmethod
    def update_recipient(
        cls,
        db: Session,
        recipient_id: uuid.UUID,
        payload: RecipientUpdate,
        actor: Dict[str, Any],
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        actor_id, email = cls._require_actor(db, actor)
        row = cls._load_recipient(db, recipient_id, lock=True)
        previous = cls._snapshot(row)
        changes = payload.model_dump(exclude_unset=True)
        now = datetime.now(timezone.utc)

        if "phone_number" in changes and changes["phone_number"] != row.phone_number:
            taken = db.scalar(
                select(NotificationRecipient.id).where(
                    NotificationRecipient.phone_number == changes["phone_number"],
                    NotificationRecipient.id != row.id,
                )
            )
            if taken is not None:
                raise HTTPException(status_code=409, detail="A recipient with this phone number already exists.")
            row.phone_number = changes["phone_number"]

        if "label" in changes:
            row.label = changes["label"]
        if "is_active" in changes:
            row.is_active = changes["is_active"]

        edited = (
            previous["label"] != row.label or previous["phone_number"] != row.phone_number
        )
        activity_changed = previous["is_active"] != bool(row.is_active)
        if not edited and not activity_changed:
            return cls._recipient_dict(row)
        row.updated_at = now

        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise cls._conflict(exc) from exc

        new_snapshot = cls._snapshot(row)
        if edited:
            cls._audit(
                db,
                actor_id=actor_id,
                actor_email=email,
                action=ACTION_RECIPIENT_EDITED,
                resource_id=str(row.id),
                previous=previous,
                new=new_snapshot,
                ip_address=ip_address,
            )
        if activity_changed:
            cls._audit(
                db,
                actor_id=actor_id,
                actor_email=email,
                action=ACTION_RECIPIENT_ENABLED if row.is_active else ACTION_RECIPIENT_DISABLED,
                resource_id=str(row.id),
                previous=previous,
                new=new_snapshot,
                ip_address=ip_address,
            )
        cls._persist(db)
        db.refresh(row)
        return cls._recipient_dict(row)

    @classmethod
    def delete_recipient(
        cls,
        db: Session,
        recipient_id: uuid.UUID,
        actor: Dict[str, Any],
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        actor_id, email = cls._require_actor(db, actor)
        row = cls._load_recipient(db, recipient_id, lock=True)
        routes = db.scalars(
            select(NotificationServiceRoute).where(NotificationServiceRoute.recipient_id == row.id)
        ).all()
        for route in routes:
            db.delete(route)
        db.flush()
        previous = cls._snapshot(row)
        previous["service_keys"] = [route.service_key for route in routes]
        resource_id = str(row.id)
        db.delete(row)
        cls._audit(
            db,
            actor_id=actor_id,
            actor_email=email,
            action=ACTION_RECIPIENT_DELETED,
            resource_id=resource_id,
            previous=previous,
            new=None,
            ip_address=ip_address,
        )
        cls._persist(db)
        return {"deleted": True, "recipient_id": recipient_id}

    @classmethod
    def _routes_for(cls, db: Session, service_key: str, *, lock: bool) -> Sequence[NotificationServiceRoute]:
        stmt = (
            select(NotificationServiceRoute)
            .where(NotificationServiceRoute.service_key == service_key)
            .order_by(NotificationServiceRoute.created_at, NotificationServiceRoute.id)
        )
        if lock:
            stmt = cls._lock(stmt, db)
            return db.scalars(stmt).all()
        stmt = stmt.options(joinedload(NotificationServiceRoute.recipient))
        return db.scalars(stmt).unique().all()

    @classmethod
    def _service_state(cls, db: Session, service_key: str) -> Dict[str, Any]:
        routes = cls._routes_for(db, service_key, lock=False)
        assignments = []
        effective = []
        for route in routes:
            recipient = route.recipient
            recipient_active = bool(recipient.is_active)
            route_active = bool(route.is_active)
            is_effective = recipient_active and route_active
            assignments.append(
                {
                    "id": route.id,
                    "service_key": route.service_key,
                    "recipient_id": recipient.id,
                    "recipient_label": recipient.label,
                    "phone_number": recipient.phone_number,
                    "is_active": route_active,
                    "recipient_is_active": recipient_active,
                    "effective": is_effective,
                }
            )
            if is_effective:
                effective.append(cls._recipient_dict(recipient))
        return {
            "service_key": service_key,
            "assignments": assignments,
            "effective_recipients": effective,
        }

    @classmethod
    def list_routes(cls, db: Session) -> List[Dict[str, Any]]:
        return [cls._service_state(db, key) for key in NOTIFICATION_SERVICE_KEYS]

    @classmethod
    def replace_routes(
        cls,
        db: Session,
        service_key: str,
        payload: ServiceRouteUpdateRequest,
        actor: Dict[str, Any],
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        key = cls._require_service_key(service_key)
        actor_id, email = cls._require_actor(db, actor)
        wanted_ids = [_as_uuid(item.recipient_id) for item in payload.assignments]
        recipients = []
        if wanted_ids:
            stmt = cls._lock(
                select(NotificationRecipient).where(NotificationRecipient.id.in_(wanted_ids)),
                db,
            )
            recipients = list(db.scalars(stmt).all())
        found = {_as_uuid(row.id) for row in recipients}
        if found != set(wanted_ids):
            raise HTTPException(status_code=404, detail="One or more recipient IDs do not exist.")

        existing = list(cls._routes_for(db, key, lock=True))
        previous = [
            {
                "recipient_id": str(route.recipient_id),
                "phone_number": route.recipient.phone_number,
                "is_active": bool(route.is_active),
            }
            for route in existing
        ]
        desired = {_as_uuid(item.recipient_id): item.is_active for item in payload.assignments}
        now = datetime.now(timezone.utc)
        existing_ids = set()
        for route in existing:
            route_recipient_id = _as_uuid(route.recipient_id)
            existing_ids.add(route_recipient_id)
            if route_recipient_id not in desired:
                db.delete(route)
                continue
            route.is_active = desired[route_recipient_id]
            route.updated_at = now

        for recipient_id, is_active in desired.items():
            if recipient_id in existing_ids:
                continue
            db.add(
                NotificationServiceRoute(
                    service_key=key,
                    recipient_id=recipient_id,
                    is_active=is_active,
                )
            )
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise cls._conflict(exc) from exc

        state = cls._service_state(db, key)
        cls._audit(
            db,
            actor_id=actor_id,
            actor_email=email,
            action=ACTION_SERVICE_ROUTING_CHANGED,
            resource_id=key,
            previous={"service_key": key, "assignments": previous},
            new={
                "service_key": key,
                "assignments": [
                    {
                        "recipient_id": str(item["recipient_id"]),
                        "phone_number": item["phone_number"],
                        "is_active": item["is_active"],
                    }
                    for item in state["assignments"]
                ],
            },
            ip_address=ip_address,
        )
        cls._persist(db)
        return cls._service_state(db, key)

    @classmethod
    def set_recipient_services(
        cls,
        db: Session,
        recipient_id: uuid.UUID,
        service_keys: Sequence[str],
        actor: Dict[str, Any],
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Replace the services one recipient receives. Other recipients stay unchanged."""
        keys = [cls._require_service_key(key) for key in service_keys]
        actor_id, email = cls._require_actor(db, actor)
        row = cls._load_recipient(db, recipient_id, lock=True)
        existing = list(
            db.scalars(
                cls._lock(
                    select(NotificationServiceRoute).where(NotificationServiceRoute.recipient_id == row.id),
                    db,
                )
            ).all()
        )
        previous = [route.service_key for route in existing]
        desired = set(keys)
        existing_by_key = {route.service_key: route for route in existing}
        now = datetime.now(timezone.utc)
        for service_key, route in existing_by_key.items():
            if service_key not in desired:
                db.delete(route)
                continue
            route.is_active = True
            route.updated_at = now
        for service_key in keys:
            if service_key in existing_by_key:
                continue
            db.add(
                NotificationServiceRoute(
                    service_key=service_key,
                    recipient_id=row.id,
                    is_active=True,
                )
            )
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise cls._conflict(exc) from exc
        cls._audit(
            db,
            actor_id=actor_id,
            actor_email=email,
            action=ACTION_SERVICE_ROUTING_CHANGED,
            resource_id=str(row.id),
            previous={"recipient_id": str(row.id), "service_keys": previous},
            new={"recipient_id": str(row.id), "service_keys": keys},
            ip_address=ip_address,
        )
        cls._persist(db)
        return {"recipient_id": row.id, "service_keys": keys}

    @classmethod
    def adopt_environment_routing(
        cls,
        db: Session,
        actor: Dict[str, Any],
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Make the current environment numbers editable without changing who is notified.

        The first adoption copies those numbers, assigns every service to each of
        them, and switches live routing to the database. Later calls leave the
        admin's edits in place.
        """
        from app.services.whatsapp_department_formatting import WhatsAppDepartmentFormattingMixin

        actor_id, email = cls._require_actor(db, actor)
        settings_row, _created = cls._settings(db, lock=True)
        if settings_row.routing_mode == ROUTING_MODE_DATABASE:
            return {
                "routing_mode": settings_row.routing_mode,
                "adopted": False,
                "recipients_created": 0,
                "routes_created": 0,
            }

        live = WhatsAppDepartmentFormattingMixin.describe_recipient_phone_numbers()
        if not live:
            return {
                "routing_mode": settings_row.routing_mode,
                "adopted": False,
                "recipients_created": 0,
                "routes_created": 0,
            }

        existing_rows = list(db.scalars(select(NotificationRecipient)).all())
        by_phone = {row.phone_number: row for row in existing_rows}
        created = 0
        managed: List[NotificationRecipient] = []
        for item in live:
            row = by_phone.get(item["phone_number"])
            if row is None:
                row = NotificationRecipient(
                    label=item["label"],
                    phone_number=item["phone_number"],
                    is_active=True,
                )
                db.add(row)
                db.flush()
                by_phone[row.phone_number] = row
                created += 1
            managed.append(row)

        route_count = db.scalar(select(func.count()).select_from(NotificationServiceRoute)) or 0
        routes_created = 0
        if route_count == 0:
            for row in managed:
                if not row.is_active:
                    continue
                for service_key in NOTIFICATION_SERVICE_KEYS:
                    db.add(
                        NotificationServiceRoute(
                            service_key=service_key,
                            recipient_id=row.id,
                            is_active=True,
                        )
                    )
                    routes_created += 1
            db.flush()

        previous_mode = settings_row.routing_mode
        settings_row.routing_mode = ROUTING_MODE_DATABASE
        settings_row.updated_at = datetime.now(timezone.utc)
        cls._audit(
            db,
            actor_id=actor_id,
            actor_email=email,
            action=ACTION_ROUTING_MODE_CHANGED,
            resource_id=str(settings_row.id),
            previous={"routing_mode": previous_mode, "recipients_created": 0, "routes_created": 0},
            new={
                "routing_mode": settings_row.routing_mode,
                "recipients_created": created,
                "routes_created": routes_created,
            },
            ip_address=ip_address,
        )
        cls._persist(db)
        db.refresh(settings_row)
        return {
            "routing_mode": settings_row.routing_mode,
            "adopted": True,
            "recipients_created": created,
            "routes_created": routes_created,
        }

    @classmethod
    def get_settings(cls, db: Session) -> Dict[str, Any]:
        row, created = cls._settings(db, lock=False)
        if created:
            cls._persist(db)
            db.refresh(row)
        return {
            "routing_mode": row.routing_mode,
            "settings_id": row.id,
            "live_recipients": cls._live_recipients(db, row.routing_mode),
        }

    @classmethod
    def _live_recipients(cls, db: Session, routing_mode: str) -> List[Dict[str, str]]:
        """Numbers that receive an operations alert for the current routing mode."""
        if routing_mode != ROUTING_MODE_DATABASE:
            from app.services.whatsapp_department_formatting import WhatsAppDepartmentFormattingMixin

            return WhatsAppDepartmentFormattingMixin.describe_recipient_phone_numbers()

        rows = db.execute(
            select(NotificationRecipient.label, NotificationRecipient.phone_number)
            .join(
                NotificationServiceRoute,
                NotificationServiceRoute.recipient_id == NotificationRecipient.id,
            )
            .where(
                NotificationServiceRoute.is_active.is_(True),
                NotificationRecipient.is_active.is_(True),
            )
            .order_by(NotificationRecipient.created_at, NotificationRecipient.id)
        ).all()
        described: List[Dict[str, str]] = []
        seen = set()
        for label, phone in rows:
            if phone and phone not in seen:
                seen.add(phone)
                described.append({"label": (label or "").strip() or "Recipient", "phone_number": phone})
        return described

    @classmethod
    def update_settings(
        cls,
        db: Session,
        routing_mode: str,
        actor: Dict[str, Any],
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Store env or database. Live WhatsApp dispatch is not switched here."""
        actor_id, email = cls._require_actor(db, actor)
        row, _created = cls._settings(db, lock=True)
        if row.routing_mode == routing_mode:
            return {"routing_mode": row.routing_mode, "settings_id": row.id}
        previous = row.routing_mode
        row.routing_mode = routing_mode
        row.updated_at = datetime.now(timezone.utc)
        cls._audit(
            db,
            actor_id=actor_id,
            actor_email=email,
            action=ACTION_ROUTING_MODE_CHANGED,
            resource_id=str(row.id),
            previous={"routing_mode": previous},
            new={"routing_mode": routing_mode},
            ip_address=ip_address,
        )
        cls._persist(db)
        db.refresh(row)
        return {"routing_mode": row.routing_mode, "settings_id": row.id}

    @classmethod
    def get_config(cls, db: Session) -> Dict[str, Any]:
        settings_row, created = cls._settings(db, lock=False)
        data = {
            "routing_mode": settings_row.routing_mode,
            "settings_id": settings_row.id,
            "recipients": cls.list_recipients(db),
            "services": cls.list_routes(db),
        }
        if created:
            cls._persist(db)
        return data

    @classmethod
    def import_env_recipients(
        cls,
        db: Session,
        actor: Dict[str, Any],
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Copy the five env numbers into recipients. Does not assign services or change mode."""
        actor_id, email = cls._require_actor(db, actor)
        settings_row, _created = cls._settings(db, lock=True)
        mode_before = settings_row.routing_mode
        existing_rows = db.scalars(select(NotificationRecipient)).all()
        by_phone = {row.phone_number: row for row in existing_rows}

        skipped_empty: List[str] = []
        skipped_invalid: List[str] = []
        skipped_existing: List[str] = []
        created_rows: List[NotificationRecipient] = []

        for index, field in enumerate(ENV_PHONE_FIELDS, start=1):
            raw = getattr(settings, field, None)
            if raw is None:
                raw = os.getenv(field, "")
            raw_text = str(raw or "").strip()
            if not raw_text:
                skipped_empty.append(field)
                continue
            try:
                phone = normalize_recipient_phone(raw_text)
            except ValueError:
                skipped_invalid.append(field)
                continue
            if phone in by_phone:
                skipped_existing.append(phone)
                continue
            row = NotificationRecipient(
                label=f"Environment recipient {index}",
                phone_number=phone,
                is_active=True,
            )
            try:
                with db.begin_nested():
                    db.add(row)
                    db.flush()
            except IntegrityError:
                skipped_existing.append(phone)
                continue
            by_phone[phone] = row
            created_rows.append(row)

        created_payload = [cls._recipient_dict(row) for row in created_rows]
        cls._audit(
            db,
            actor_id=actor_id,
            actor_email=email,
            action=ACTION_ENV_RECIPIENTS_IMPORTED,
            resource_id="env-recipients",
            previous={"routing_mode": mode_before, "unchanged_phones": skipped_existing},
            new={
                "created": [
                    {"id": item["id"], "phone_number": item["phone_number"], "label": item["label"]}
                    for item in created_payload
                ],
                "skipped_invalid": skipped_invalid,
                "skipped_empty": skipped_empty,
                "routes_created": 0,
                "routing_mode": settings_row.routing_mode,
            },
            ip_address=ip_address,
        )
        if settings_row.routing_mode != mode_before:
            settings_row.routing_mode = mode_before
        cls._persist(db)
        for row in created_rows:
            db.refresh(row)
        db.refresh(settings_row)
        return {
            "created": [cls._recipient_dict(row) for row in created_rows],
            "skipped_existing": skipped_existing,
            "skipped_invalid": skipped_invalid,
            "skipped_empty": skipped_empty,
            "routes_created": 0,
            "routing_mode": settings_row.routing_mode,
        }


def _env_recipients() -> Dict[str, Any]:
    from app.services.whatsapp_department_formatting import WhatsAppDepartmentFormattingMixin

    return {
        "routing_mode": ROUTING_MODE_ENV,
        "recipients": WhatsAppDepartmentFormattingMixin.get_recipient_phone_numbers(),
    }


def _dedupe_normalized_phones(raw_numbers: Sequence[Any]) -> List[str]:
    from app.services.whatsapp_department_formatting import WhatsAppDepartmentFormattingMixin

    phones: List[str] = []
    seen = set()
    for raw in raw_numbers:
        phone = WhatsAppDepartmentFormattingMixin.normalize_phone_number(str(raw or ""))
        if phone and phone not in seen:
            seen.add(phone)
            phones.append(phone)
    return phones


def resolve_operations_recipients(
    service_key: Union[str, Dict[str, Any], None] = None,
    db: Optional[Session] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Read routing once and return the phones for this operations event.

    Database mode is the authoritative source of truth:
    1. Resolves service_key via NotificationServiceMapper across website and WhatsApp flows.
    2. Queries active recipients on active routes for the resolved service.
    3. Normalizes phone numbers to international format (E.164) and deduplicates them.
    4. If no active recipient is assigned to a service, sends to NOBODY (never falls back).
    5. Unknown service keys log a routing failure and return nobody.

    Env mode is only used if routing_mode is explicitly set to 'env' or if settings table is missing.
    """
    ctx: Dict[str, Any] = {}
    raw_service: Optional[str] = None
    if isinstance(service_key, dict):
        ctx = dict(service_key)
        raw_service = (
            ctx.get("service_category")
            or ctx.get("serviceCategory")
            or ctx.get("service_name")
            or ctx.get("selected_service_name")
            or ""
        )
        if db is None and "db" in ctx:
            db = ctx.get("db")
    elif isinstance(service_key, str):
        raw_service = service_key.strip()
        if context and isinstance(context, dict):
            ctx = dict(context)
            if db is None and "db" in ctx:
                db = ctx.get("db")
    elif context and isinstance(context, dict):
        ctx = dict(context)
        raw_service = (
            ctx.get("service_category")
            or ctx.get("serviceCategory")
            or ctx.get("service_name")
            or ctx.get("selected_service_name")
            or ""
        )
        if db is None and "db" in ctx:
            db = ctx.get("db")

    owned = False
    if db is None:
        try:
            from app.database import SessionLocal

            db = SessionLocal()
            owned = True
        except Exception:
            logger.warning(
                "Notification routing database session could not be opened. Env recipients remain in use."
            )
            return _env_recipients()

    try:
        try:
            row = db.get(NotificationSettings, NOTIFICATION_SETTINGS_ROW_ID)
        except Exception:
            logger.warning(
                "Notification routing settings could not be read. Env recipients remain in use."
            )
            return _env_recipients()

        if row is None or row.routing_mode != ROUTING_MODE_DATABASE:
            return _env_recipients()

        from app.services.notification_service_mapping import NotificationServiceMapper

        # If caller passed an exact service key with no extra context, check that exact key
        if raw_service and raw_service in NOTIFICATION_SERVICE_KEY_SET and not ctx:
            candidates = [raw_service]
        else:
            candidates = NotificationServiceMapper.resolve_candidate_service_keys(
                raw_service, ctx
            )

        if not candidates:
            return {"routing_mode": ROUTING_MODE_DATABASE, "recipients": []}

        try:
            selected_rows = []
            for candidate in candidates:
                rows = db.execute(
                    select(NotificationRecipient.phone_number)
                    .join(
                        NotificationServiceRoute,
                        NotificationServiceRoute.recipient_id == NotificationRecipient.id,
                    )
                    .where(
                        NotificationServiceRoute.service_key == candidate,
                        NotificationServiceRoute.is_active.is_(True),
                        NotificationRecipient.is_active.is_(True),
                    )
                    .order_by(NotificationRecipient.created_at, NotificationRecipient.id)
                ).all()
                if rows:
                    selected_rows = rows
                    break

            raw_phones = [item[0] for item in selected_rows]
            return {
                "routing_mode": ROUTING_MODE_DATABASE,
                "recipients": _dedupe_normalized_phones(raw_phones),
            }
        except Exception as exc:
            logger.warning(
                "Notification routes could not be read for %s: %s. Database mode sends to nobody for this event.",
                candidates,
                exc,
            )
            return {"routing_mode": ROUTING_MODE_DATABASE, "recipients": []}
    finally:
        if owned and db is not None:
            db.close()


def database_routing_active(db: Optional[Session] = None) -> bool:
    """True only when the stored mode is database. Missing settings stay in env mode."""
    return resolve_operations_recipients(None, db)["routing_mode"] == ROUTING_MODE_DATABASE


def deliver_operations_text(
    recipients: Sequence[str],
    message: str,
    send_one: Callable[[str, str], Any],
) -> Dict[str, Any]:
    """Send one operations text to each recipient. One failure does not stop the rest."""
    if not recipients:
        logger.info("Database routing has no active recipient. No operations message sent.")
        return {"status": "BYPASSED", "reason": "no_recipients_configured"}

    delivered: Optional[Dict[str, Any]] = None
    failures = 0
    for phone in recipients:
        try:
            result = send_one(phone, message) or {}
        except Exception as exc:
            failures += 1
            logger.error(
                "Operations WhatsApp send failed for one recipient: %s",
                type(exc).__name__,
            )
            continue
        if result.get("success"):
            delivered = result
        else:
            failures += 1
            logger.error(
                "Operations WhatsApp send was rejected for one recipient: %s",
                result.get("error") or result.get("reason") or "delivery_failed",
            )
    if delivered is not None:
        return {
            "status": "DELIVERED",
            "message_id": delivered.get("message_id"),
            "failures": failures,
        }
    return {"status": "FAILED", "error": "all_recipients_failed", "failures": failures}
