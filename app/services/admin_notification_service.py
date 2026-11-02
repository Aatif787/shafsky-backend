"""Enterprise Admin Real-Time Notification Service.

Provides:
1. In-process ConnectionManager with SSE queue distribution.
2. PostgreSQL LISTEN/NOTIFY support for multi-worker Gunicorn architectures.
3. Persistent, idempotent storage with deduplication on (booking_ref, event_type).
4. Safe, non-blocking hooks for booking and charter lifecycles.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.admin_notification import AdminNotification

logger = logging.getLogger(__name__)


class AdminNotificationConnectionManager:
    """
    Thread-safe connection manager for Server-Sent Events (SSE) admin subscribers.
    """

    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        """Register a new admin subscriber queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        logger.info(
            "Admin SSE client connected. Active connections: %d",
            len(self._subscribers),
        )
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Unregister an admin subscriber queue."""
        self._subscribers.discard(queue)
        logger.info(
            "Admin SSE client disconnected. Active connections: %d",
            len(self._subscribers),
        )

    def broadcast_local(self, data: Dict[str, Any]) -> None:
        """
        Synchronously push an event payload to all currently connected local queues.
        """
        dead_queues = []
        for q in list(self._subscribers):
            try:
                # If queue is full, drop oldest item to make space
                if q.full():
                    try:
                        q.get_nowait()
                    except (asyncio.QueueEmpty, ValueError):
                        pass
                q.put_nowait(data)
            except Exception as err:
                logger.debug("Failed to put item in subscriber queue: %s", err)
                dead_queues.append(q)

        for dq in dead_queues:
            self._subscribers.discard(dq)

    @property
    def active_count(self) -> int:
        return len(self._subscribers)


# Module singleton connection manager
_connection_manager = AdminNotificationConnectionManager()


class AdminNotificationService:
    """Enterprise Admin Real-Time Notification Service."""

    manager = _connection_manager

    @classmethod
    def get_manager(cls) -> AdminNotificationConnectionManager:
        return cls.manager

    @classmethod
    def create_or_get_notification(
        cls, db: Session, data: Dict[str, Any]
    ) -> AdminNotification:
        """
        Atomically persist a notification record with deduplication.
        Uses INSERT ... ON CONFLICT (booking_ref, event_type) DO NOTHING.
        Guarantees that an existing notification is returned if already created.
        """
        booking_ref = str(data.get("booking_ref") or "").strip()
        event_type = str(data.get("event_type") or "BOOKING_CREATED").strip()

        if not booking_ref:
            raise ValueError("booking_ref is required for AdminNotification.")

        raw_booking_id = data.get("booking_id")
        parsed_booking_id = None
        if raw_booking_id:
            try:
                parsed_booking_id = (
                    uuid.UUID(str(raw_booking_id))
                    if not isinstance(raw_booking_id, uuid.UUID)
                    else raw_booking_id
                )
            except (ValueError, TypeError):
                parsed_booking_id = None

        new_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        insert_values = {
            "id": new_id,
            "booking_id": parsed_booking_id,
            "booking_ref": booking_ref,
            "service_category": str(data.get("service_category") or "General").strip(),
            "service_type": str(data.get("service_type") or "General Service").strip(),
            "customer_name": str(data.get("customer_name") or "Guest").strip(),
            "customer_phone": str(data.get("customer_phone") or "").strip() or None,
            "status": str(data.get("status") or "PENDING").strip(),
            "event_type": event_type,
            "title": str(data.get("title") or f"New Booking: {booking_ref}").strip(),
            "message": str(data.get("message") or f"New booking received for {booking_ref}").strip(),
            "link": str(data.get("link") or f"/admin/operations?search={booking_ref}").strip(),
            "metadata_json": data.get("metadata_json") or {},
            "is_read": False,
            "read_at": None,
            "read_by_user_id": None,
            "created_at": now,
        }

        if db.bind and getattr(db.bind.dialect, "name", "") == "postgresql":
            stmt = (
                insert(AdminNotification)
                .values(**insert_values)
                .on_conflict_do_nothing(
                    index_elements=["booking_ref", "event_type"]
                )
            )
            db.execute(stmt)
            db.commit()
        else:
            existing = (
                db.query(AdminNotification)
                .filter(
                    AdminNotification.booking_ref == booking_ref,
                    AdminNotification.event_type == event_type,
                )
                .first()
            )
            if not existing:
                record = AdminNotification(**insert_values)
                db.add(record)
                db.commit()

        # Fetch the authoritative persisted record
        persisted = (
            db.query(AdminNotification)
            .filter(
                AdminNotification.booking_ref == booking_ref,
                AdminNotification.event_type == event_type,
            )
            .first()
        )
        if not persisted:
            persisted = (
                db.query(AdminNotification)
                .filter(AdminNotification.id == new_id)
                .first()
            )

        return persisted

    @classmethod
    def broadcast_notification(
        cls, db: Optional[Session], notification_data: Dict[str, Any]
    ) -> None:
        """
        Broadcast notification to:
        1. Local worker SSE subscribers.
        2. PostgreSQL NOTIFY channel for inter-worker delivery.
        """
        # 1. Local delivery
        cls.manager.broadcast_local(notification_data)

        # 2. Cross-worker PostgreSQL NOTIFY (best effort)
        if db and getattr(getattr(db, "bind", None), "dialect", None) and getattr(db.bind.dialect, "name", "") == "postgresql":
            try:
                payload_str = json.dumps(notification_data)
                if len(payload_str) < 7900:  # PostgreSQL NOTIFY payload cap is 8000 bytes
                    db.execute(
                        text("SELECT pg_notify('admin_notifications_channel', :payload)"),
                        {"payload": payload_str},
                    )
                    db.commit()
            except Exception as pg_err:
                logger.debug("PostgreSQL NOTIFY dispatch skipped or failed: %s", pg_err)

    @classmethod
    def handle_booking_created_event(
        cls, db: Session, context: Dict[str, Any]
    ) -> Optional[AdminNotification]:
        """
        Hook called after any booking or service enquiry is persisted.
        Persists record, broadcasts to connected admins, and handles errors safely.
        """
        try:
            booking_ref = str(
                context.get("booking_ref")
                or context.get("bookingRef")
                or context.get("reference")
                or ""
            ).strip()
            if not booking_ref:
                return None

            service_cat = str(context.get("service_category") or "Airport Services").strip()
            service_type = str(
                context.get("service_type")
                or context.get("service_name")
                or service_cat
            ).strip()
            customer_name = str(
                context.get("passenger_name")
                or context.get("customer_name")
                or context.get("fullName")
                or "Guest"
            ).strip()
            customer_phone = str(
                context.get("passenger_phone")
                or context.get("mobile_number")
                or context.get("phone")
                or ""
            ).strip()
            status = str(context.get("status") or "PENDING").strip()

            title = f"New {service_cat} ({booking_ref})"
            message = f"{customer_name} booked {service_type}"
            if context.get("origin_code") or context.get("airport"):
                loc = context.get("origin_code") or context.get("airport")
                message += f" at {loc}"

            notification_data = {
                "booking_id": context.get("booking_id") or context.get("id"),
                "booking_ref": booking_ref,
                "service_category": service_cat,
                "service_type": service_type,
                "customer_name": customer_name,
                "customer_phone": customer_phone or None,
                "status": status,
                "event_type": "BOOKING_CREATED",
                "title": title,
                "message": message,
                "link": f"/admin/operations?search={booking_ref}",
                "metadata_json": {
                    "origin_code": context.get("origin_code") or context.get("airport"),
                    "dest_code": context.get("dest_code"),
                    "flight_num": context.get("flight_num") or context.get("flight_number"),
                    "departure_time": context.get("departure_time") or context.get("travel_date"),
                },
            }

            notification = cls.create_or_get_notification(db, notification_data)
            cls.broadcast_notification(db, notification.to_dict())
            return notification
        except Exception as err:
            logger.warning(
                "AdminNotificationService.handle_booking_created_event failed: %s",
                err,
            )
            return None

    @classmethod
    def handle_charter_created_event(
        cls, db: Session, charter_request: Any
    ) -> Optional[AdminNotification]:
        """
        Hook called after a Private Charter request is created.
        """
        try:
            ref = getattr(charter_request, "request_reference", "")
            if not ref:
                return None

            customer_name = getattr(charter_request, "customer_name", "Guest")
            aircraft = getattr(charter_request, "aircraft_preference", "Private Charter") or "Private Charter"
            origin = getattr(charter_request, "origin", "")
            dest = getattr(charter_request, "destination", "")
            status_val = (
                charter_request.status.value
                if hasattr(charter_request.status, "value")
                else str(charter_request.status)
            )

            title = f"New Charter Request ({ref})"
            message = f"{customer_name} requested {aircraft}"
            if origin and dest:
                message += f" from {origin} to {dest}"

            notification_data = {
                "booking_id": getattr(charter_request, "id", None),
                "booking_ref": ref,
                "service_category": "Private Charter",
                "service_type": aircraft,
                "customer_name": customer_name,
                "customer_phone": getattr(charter_request, "phone", None),
                "status": status_val,
                "event_type": "BOOKING_CREATED",
                "title": title,
                "message": message,
                "link": f"/admin/operations?search={ref}",
                "metadata_json": {
                    "trip_type": getattr(charter_request, "trip_type", "One Way"),
                    "origin": origin,
                    "destination": dest,
                    "departure_date": getattr(charter_request, "departure_date", ""),
                },
            }

            notification = cls.create_or_get_notification(db, notification_data)
            cls.broadcast_notification(db, notification.to_dict())
            return notification
        except Exception as err:
            logger.warning(
                "AdminNotificationService.handle_charter_created_event failed: %s",
                err,
            )
            return None

    @classmethod
    def list_notifications(
        cls, db: Session, limit: int = 50, unread_only: bool = False
    ) -> Dict[str, Any]:
        """
        Retrieve recent notifications for the admin portal, along with total unread count.
        """
        query = db.query(AdminNotification)
        if unread_only:
            query = query.filter(AdminNotification.is_read == False)

        notifications = (
            query.order_by(AdminNotification.created_at.desc())
            .limit(limit)
            .all()
        )

        unread_count = (
            db.query(func.count(AdminNotification.id))
            .filter(AdminNotification.is_read == False)
            .scalar()
            or 0
        )

        return {
            "notifications": [n.to_dict() for n in notifications],
            "unread_count": int(unread_count),
        }

    @classmethod
    def mark_as_read(
        cls,
        db: Session,
        notification_id: uuid.UUID,
        admin_user_id: Optional[uuid.UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Mark a single notification as read.
        """
        notif = db.query(AdminNotification).filter(AdminNotification.id == notification_id).first()
        if not notif:
            return None

        if not notif.is_read:
            notif.is_read = True
            notif.read_at = datetime.now(timezone.utc)
            notif.read_by_user_id = admin_user_id
            db.commit()
            db.refresh(notif)

        return notif.to_dict()

    @classmethod
    def mark_all_as_read(
        cls, db: Session, admin_user_id: Optional[uuid.UUID] = None
    ) -> int:
        """
        Mark all unread notifications as read.
        Returns the number of notifications updated.
        """
        now = datetime.now(timezone.utc)
        updated_count = (
            db.query(AdminNotification)
            .filter(AdminNotification.is_read == False)
            .update(
                {
                    AdminNotification.is_read: True,
                    AdminNotification.read_at: now,
                    AdminNotification.read_by_user_id: admin_user_id,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return updated_count

