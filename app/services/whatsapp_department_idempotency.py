"""
Idempotency lifecycle management mixin for WhatsApp department notifications.
"""

import hashlib
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
        now = time.time()

        with cls._lock:
            cached = cls._dispatched_events.get(clean_key)
            if cached:
                expires_at = cached.get("expires", 0)
                state = cached.get("state")
                if now < expires_at:
                    if state == "dispatched":
                        logger.info(
                            "[WhatsApp Idempotency] Suppressing duplicate notification for %s (in-memory dispatched).",
                            clean_key,
                        )
                        return "DUPLICATE"
                    if state == "in_progress":
                        logger.warning(
                            "[WhatsApp Idempotency] Concurrent dispatch active for %s. Rejecting redundant attempt.",
                            clean_key,
                        )
                        return "IN_PROGRESS"
                    if state == "unknown_timeout":
                        logger.warning(
                            "[WhatsApp Idempotency] Event %s recently timed out; holding retry to prevent duplicate.",
                            clean_key,
                        )
                        return "UNKNOWN_TIMEOUT"
                else:
                    cls._dispatched_events.pop(clean_key, None)

            # Check database for durable persistence across process restarts
            try:
                with SessionLocal() as db:
                    existing = (
                        db.query(NotificationRecord)
                        .filter(
                            NotificationRecord.template_type == "INTERNAL_OPERATIONS_WHATSAPP",
                            NotificationRecord.channel == "whatsapp",
                            NotificationRecord.recipient_phone == clean_key,
                            NotificationRecord.status.in_([
                                NotificationStatus.DELIVERED,
                                NotificationStatus.COMPLETED,
                            ]),
                        )
                        .first()
                    )
                    if existing:
                        logger.info(
                            "[WhatsApp Idempotency] Suppressing duplicate notification for %s (DB record exists).",
                            clean_key,
                        )
                        cls._dispatched_events[clean_key] = {
                            "state": "dispatched",
                            "expires": now + cls.IDEMPOTENCY_DISPATCHED_TTL_SECONDS,
                        }
                        return "DUPLICATE"
            except Exception as db_err:
                logger.debug("[WhatsApp Idempotency] DB persistence check skipped: %s", db_err)

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

        try:
            with SessionLocal() as db:
                accepted_ids = [
                    res.get("message_id")
                    for res in recipient_results
                    if res.get("status") in ("ACCEPTED", "DELIVERED") and res.get("message_id")
                ]
                msg_ids_str = ",".join(accepted_ids) if accepted_ids else None
                booking_identifier = (context or {}).get("booking_ref") or (context or {}).get("bookingRef") or clean_key

                db_status = NotificationStatus.DELIVERED if is_successful else NotificationStatus.FAILED

                note = NotificationRecord(
                    id=uuid.uuid4(),
                    template_type="INTERNAL_OPERATIONS_WHATSAPP",
                    recipient_phone=clean_key,
                    channel="whatsapp",
                    status=db_status,
                    message_id=msg_ids_str,
                    delivered_at=datetime.now(timezone.utc) if is_successful else None,
                    payload={
                        "idempotency_key": clean_key,
                        "booking_ref": booking_identifier,
                        "recipient_results": recipient_results,
                    },
                )
                db.add(note)
                db.commit()
        except Exception as db_err:
            logger.debug("[WhatsApp Idempotency] DB persistence save skipped: %s", db_err)

