"""
Customer WhatsApp invoice document delivery mixin for NotificationService.
"""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.schema import NotificationRecord, NotificationStatus
from app.services.notification_customer_formatting import (
    _ns,
    TEMPLATE_PARAM_MAX_CHARS,
    build_template_parameter,
    _format_datetime,
    build_customer_summary,
)


class NotificationCustomerInvoiceMixin:
    """Customer WhatsApp PDF invoice document delivery and deduplication."""
    @classmethod
    def _already_sent_whatsapp_invoice(cls, db: Session, booking_ref: str) -> bool:
        if not booking_ref:
            return False
        records = list(
            db.scalars(
                select(NotificationRecord)
                .where(
                    NotificationRecord.template_type == cls.WHATSAPP_INVOICE_TEMPLATE,
                    NotificationRecord.status.in_([NotificationStatus.DELIVERED, NotificationStatus.SENDING]),
                )
                .order_by(desc(NotificationRecord.created_at))
                .limit(30)
            ).all()
        )
        for rec in records:
            payload = rec.payload or {}
            ref = str(payload.get("booking_ref") or payload.get("bookingRef") or "")
            if ref == booking_ref:
                return True
        return False

    @classmethod
    def send_whatsapp_invoice_document(
        cls,
        db: Session,
        *,
        booking_ref: str,
        recipient_phone: str,
        invoice_number: str,
        pdf_bytes: Optional[bytes] = None,
        document_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send the Tax Invoice PDF as a WhatsApp DOCUMENT message.
        Atomically claimed and idempotent per booking_ref + invoice_number.
        Marks DELIVERED only after Meta accepts the message.
        Failures are retryable and never raise to the payment caller.
        """
        ns = _ns()
        whatsapp_client = ns.whatsapp_client
        logger = ns.logger

        booking_ref = (booking_ref or "").strip()
        phone = (recipient_phone or "").strip()
        if not booking_ref or not phone:
            return {"status": "FAILED", "error": "missing_recipient"}

        filename = f"Shafsky-Aviation-Tax-Invoice-{invoice_number}.pdf"
        caption = (
            f"Shafsky Aviation Tax Invoice\n"
            f"Invoice: {invoice_number}\n"
            f"Booking: {booking_ref}"
        )
        payload = {
            "booking_ref": booking_ref,
            "invoice_number": invoice_number,
            "filename": filename,
        }

        record, action = cls._acquire_notification_claim(
            db,
            template_type=cls.WHATSAPP_INVOICE_TEMPLATE,
            booking_ref=booking_ref,
            recipient=phone,
            channel="WHATSAPP",
            payload=payload,
        )

        if action in ("ALREADY_DELIVERED", "IN_PROGRESS"):
            logger.info("Skipping duplicate WhatsApp invoice PDF (%s)", action, extra={"booking_ref": booking_ref})
            return {"status": "SKIPPED", "reason": "duplicate", "claim_status": action}
        if action == "MAX_ATTEMPTS_EXCEEDED":
            logger.warning("Max attempts exceeded for WhatsApp invoice PDF", extra={"booking_ref": booking_ref})
            return {"status": "FAILED", "error": "max_attempts_exceeded"}

        if not record:
            return {"status": "FAILED", "error": "claim_failed"}

        try:
            result = whatsapp_client.send_document_message(
                phone,
                filename=filename,
                caption=caption,
                document_url=document_url,
                pdf_bytes=pdf_bytes,
            )
        except Exception as exc:
            logger.warning("[WhatsApp Invoice] Document send exception: %s", type(exc).__name__)
            result = {"success": False, "error": "exception", "status": "failed"}

        now = datetime.now(timezone.utc)
        if result.get("success"):
            record.status = NotificationStatus.DELIVERED
            record.delivered_at = now
            msg_id = result.get("message_id")
            record.message_id = str(msg_id) if msg_id else None
            record.error_log = None
            try:
                db.commit()
            except Exception:
                db.rollback()
            return {"status": "DELIVERED", "message_id": result.get("message_id")}

        record.status = NotificationStatus.FAILED
        record.error_log = str(result.get("error") or result.get("status") or "whatsapp_document_failed")[:400]
        record.updated_at = now
        try:
            db.commit()
        except Exception:
            db.rollback()
        return {"status": "FAILED", "error": record.error_log}
