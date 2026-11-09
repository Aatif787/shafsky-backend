"""
Idempotency lifecycle management mixin for WhatsApp department notifications.
"""

import hashlib
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.database import SessionLocal
from app.models.schema import NotificationRecord, NotificationStatus

logger = logging.getLogger("shafsky.whatsapp_department")


class WhatsAppDepartmentIdempotencyMixin:
    """Provides thread-safe in-memory and database-backed idempotency tracking."""

    # In-memory idempotency state with thread lock: dict of key -> {"state": str, "expires": float}
    _dispatched_events: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()

    IDEMPOTENCY_LOCK_TTL_SECONDS: int = 120  # In-progress concurrency mutex
    IDEMPOTENCY_DISPATCHED_TTL_SECONDS: int = 86400  # 24 hours completed state
    IDEMPOTENCY_TIMEOUT_HOLD_SECONDS: int = 300  # 5 minutes hold on ambiguous timeout

    @classmethod
    def clear_idempotency_cache(cls) -> None:
        """Utility for test suites to reset in-memory idempotency records."""
        with cls._lock:
            cls._dispatched_events.clear()

    @classmethod
    def generate_idempotency_key(
        cls,
        entity_prefix: str,
        identifier: Optional[str],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generates a stable, collision-free event identifier.
        Uses reference if valid; otherwise creates a deterministic hash of core booking details
        so separate anonymous/unreferenced bookings never collide.
        """
        clean_id = (identifier or "").strip()
        if clean_id and clean_id.upper() != "N/A":
            return f"wa_{entity_prefix}_notify:{clean_id}"

        ctx = context or {}
        parts = [
            str(ctx.get("passenger_email") or ctx.get("email") or ""),
            str(ctx.get("passenger_phone") or ctx.get("phone") or ""),
            str(ctx.get("service_category") or ctx.get("trip_type") or ""),
            str(ctx.get("service_type") or ctx.get("origin") or ""),
            str(ctx.get("departure_time") or ctx.get("departure_date") or ""),
        ]
        digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:16]
        return f"wa_{entity_prefix}_notify:anon_{digest}"

    @classmethod
    def claim_idempotency(cls, idempotency_key: str) -> str:
        """
        Atomically checks and claims an event for dispatch.
        Returns:
            - 'ACQUIRED': Successfully acquired in-progress lock. Proceed to dispatch.
            - 'DUPLICATE': Event was already successfully dispatched within 24 hours.
            - 'IN_PROGRESS': Another concurrent request is actively dispatching right now.
            - 'UNKNOWN_TIMEOUT': Event recently timed out; held to avoid blind duplicate sending.
        """
        if not idempotency_key or not idempotency_key.strip():
            return "ACQUIRED"

        clean_key = idempotency_key.strip()

        # 1. Check PostgreSQL NotificationRecord persistence for durable completed record.
        try:
            completed_since = datetime.now(timezone.utc) - timedelta(
                seconds=cls.IDEMPOTENCY_DISPATCHED_TTL_SECONDS
            )
            with SessionLocal() as db:
                candidates = db.scalars(
                    select(NotificationRecord)
                    .where(NotificationRecord.template_type == "WHATSAPP_OPS_NOTIFICATION")
                    .where(
                        NotificationRecord.status.in_(
                            [NotificationStatus.COMPLETED, NotificationStatus.DELIVERED]
                        )
                    )
                    .where(NotificationRecord.created_at >= completed_since)
                    .order_by(NotificationRecord.created_at.desc())
                    .limit(1000)
                ).all()
                for record in candidates:
                    payload = record.payload if isinstance(record.payload, dict) else {}
                    if payload.get("idempotency_key") == clean_key:
                        return "DUPLICATE"
        except Exception as db_err:
            logger.warning("[WhatsApp Idempotency] Durable duplicate check unavailable: %s", db_err)

        # 2. Check Redis if available
        try:
            from app.core.redis import get_redis_client
            redis_client = get_redis_client()
            if redis_client is not None:
                redis_key = f"lock:{clean_key}"
                val = redis_client.get(redis_key)
                if val:
                    val_str = val.decode("utf-8") if isinstance(val, bytes) else val
                    if val_str == "dispatched":
                        return "DUPLICATE"
                    if val_str == "in_progress":
                        return "IN_PROGRESS"
                    if val_str == "unknown_timeout":
                        return "UNKNOWN_TIMEOUT"

                # Acquire atomic in-progress lock
                acquired = redis_client.set(redis_key, "in_progress", ex=cls.IDEMPOTENCY_LOCK_TTL_SECONDS, nx=True)
                if not acquired:
                    return "IN_PROGRESS"
                return "ACQUIRED"
        except Exception as redis_err:
            logger.debug("[WhatsApp Idempotency] Redis check skipped: %s", redis_err)

        # 3. Thread-safe in-memory fallback
        now = time.time()
        with cls._lock:
            # Purge expired
            expired = [k for k, item in cls._dispatched_events.items() if now > item.get("expires", 0)]
            for k in expired:
                del cls._dispatched_events[k]

            entry = cls._dispatched_events.get(clean_key)
            if entry:
                state = entry.get("state")
                if state == "dispatched":
                    return "DUPLICATE"
                if state == "in_progress":
                    return "IN_PROGRESS"
                if state == "unknown_timeout":
                    return "UNKNOWN_TIMEOUT"

            cls._dispatched_events[clean_key] = {
                "state": "in_progress",
                "expires": now + cls.IDEMPOTENCY_LOCK_TTL_SECONDS,
            }
            return "ACQUIRED"

    @classmethod
    def finalize_idempotency(
        cls,
        idempotency_key: str,
        outcome: str,
        recipient_results: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Finalizes idempotency state based on dispatch outcome:
        - ACCEPTED / PARTIALLY_ACCEPTED: Upgrades lock to 'dispatched' (24h TTL) and records in DB.
        - UNKNOWN (timeout): Sets 'unknown_timeout' hold (300s TTL) to prevent blind resending.
        - FAILED (non-timeout error): Deletes lock so the notification can be retried cleanly.
        """
        if not idempotency_key or not idempotency_key.strip():
            return

        clean_key = idempotency_key.strip()
        is_successful = outcome in ("ACCEPTED", "PARTIALLY_ACCEPTED", "DELIVERED", "PARTIALLY_DELIVERED")
        is_unknown = outcome == "UNKNOWN"

        now = time.time()

        # Update Redis
        try:
            from app.core.redis import get_redis_client
            redis_client = get_redis_client()
            if redis_client is not None:
                redis_key = f"lock:{clean_key}"
                if is_successful:
                    redis_client.set(redis_key, "dispatched", ex=cls.IDEMPOTENCY_DISPATCHED_TTL_SECONDS)
                elif is_unknown:
                    redis_client.set(redis_key, "unknown_timeout", ex=cls.IDEMPOTENCY_TIMEOUT_HOLD_SECONDS)
                else:
                    redis_client.delete(redis_key)
        except Exception as redis_err:
            logger.debug("[WhatsApp Idempotency] Redis finalize skipped: %s", redis_err)

        # Update in-memory fallback
        with cls._lock:
            if is_successful:
                cls._dispatched_events[clean_key] = {
                    "state": "dispatched",
                    "expires": now + cls.IDEMPOTENCY_DISPATCHED_TTL_SECONDS,
                }
            elif is_unknown:
                cls._dispatched_events[clean_key] = {
                    "state": "unknown_timeout",
                    "expires": now + cls.IDEMPOTENCY_TIMEOUT_HOLD_SECONDS,
                }
            else:
                cls._dispatched_events.pop(clean_key, None)

        # Durable PostgreSQL DB persistence on success or recorded failure
        try:
            with SessionLocal() as db:
                accepted_ids = [r.get("message_id") for r in recipient_results if r.get("message_id")]
                msg_ids_str = ",".join(str(i) for i in accepted_ids) if accepted_ids else None
                rec_status = NotificationStatus.COMPLETED if is_successful else (
                    NotificationStatus.PENDING if is_unknown else NotificationStatus.FAILED
                )
                status_counts: Dict[str, int] = {}
                for entry in recipient_results:
                    key = str(entry.get("status") or "FAILED")
                    status_counts[key] = status_counts.get(key, 0) + 1

                messages = [
                    {
                        "index": position,
                        "phone": entry.get("masked_phone") or entry.get("phone"),
                        "message_id": entry.get("message_id"),
                        "status": "accepted" if entry.get("status") == "ACCEPTED" else str(entry.get("status") or "FAILED").lower(),
                    }
                    for position, entry in enumerate(recipient_results, 1)
                    if entry.get("message_id")
                ]

                note = NotificationRecord(
                    recipient_email=str((context or {}).get("passenger_email") or (context or {}).get("email") or "ops@shafsky.aero"),
                    recipient_phone="ALL_5_RECIPIENTS",
                    template_type="WHATSAPP_OPS_NOTIFICATION",
                    channel="WHATSAPP_OPS",
                    payload={
                        "idempotency_key": clean_key,
                        "outcome": outcome,
                        "status_counts": status_counts,
                        "messages": messages,
                        "recipient_results": recipient_results,
                        "context_summary": {
                            "booking_ref": (context or {}).get("booking_ref"),
                            "service_category": (context or {}).get("service_category"),
                        },
                    },
                    status=rec_status,
                    message_id=msg_ids_str,
                    delivered_at=datetime.now(timezone.utc) if is_successful else None,
                )
                db.add(note)
                db.commit()
        except Exception as db_err:
            logger.debug("[WhatsApp Idempotency] DB persistence save skipped: %s", db_err)
