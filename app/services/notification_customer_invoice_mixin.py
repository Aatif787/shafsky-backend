"""
Customer WhatsApp invoice document delivery mixin for NotificationService.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.schema import NotificationRecord, NotificationStatus
from app.services.notification_customer_formatting import _ns


class NotificationCustomerInvoiceMixin:
    """Customer WhatsApp PDF invoice document delivery and deduplication."""

    @classmethod
    def _resolve_invoice_template_settings(cls) -> Tuple[str, str]:
        """Return (template_name, template_language) for Tax Invoice PDF templates."""
        settings = _ns().settings
        raw_name = (
            getattr(settings, "WHATSAPP_INVOICE_PDF_TEMPLATE", "")
            or os.getenv("WHATSAPP_INVOICE_PDF_TEMPLATE", "")
            or os.getenv("WHATSAPP_INVOICE_TEMPLATE", "")
        )
        raw_lang = (
            getattr(settings, "WHATSAPP_INVOICE_TEMPLATE_LANG", "")
            or os.getenv("WHATSAPP_INVOICE_TEMPLATE_LANG", "")
        )
        template_name = str(raw_name or "").strip()
        template_lang = str(raw_lang or "").strip() or "en"
        if template_lang.replace("-", "_").lower() == "en_us":
            template_lang = "en"
        return template_name, template_lang

    @classmethod
    def _build_invoice_template_body_params(
        cls,
        db: Session,
        *,
        booking_ref: str,
        invoice_number: str,
    ) -> List[str]:
        """Build ordered body parameters for an approved invoice document template."""
        raw_fields = (
            os.getenv("WHATSAPP_INVOICE_TEMPLATE_BODY_FIELDS", "invoice_number,booking_ref") or ""
        ).strip()
        if not raw_fields or raw_fields.lower() == "none":
            return []

        fields = [f.strip().lower() for f in raw_fields.split(",") if f.strip()]
        customer_name = "Valued Guest"
        amount_display = ""

        if any(f in ("customer_name", "guest_name", "amount", "total_amount") for f in fields):
            try:
                from sqlalchemy.orm import Session as _OrmSession

                if isinstance(db, _OrmSession):
                    from app.models.payment import Invoice

                    inv = db.scalar(
                        select(Invoice).where(Invoice.invoice_number == invoice_number)
                    )
                    if inv:
                        if getattr(inv, "customer_name", None):
                            customer_name = str(inv.customer_name).strip()
                        if getattr(inv, "total_amount", None) is not None:
                            curr = (getattr(inv, "currency", None) or "INR").strip()
                            amount_display = f"{curr} {float(inv.total_amount):,.2f}"
            except Exception:
                pass

        mapping = {
            "invoice_number": invoice_number or booking_ref,
            "booking_ref": booking_ref,
            "customer_name": customer_name,
            "guest_name": customer_name,
            "amount": amount_display or invoice_number,
            "total_amount": amount_display or invoice_number,
        }
        return [mapping.get(f, booking_ref) for f in fields]

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
        Send the Tax Invoice PDF as a WhatsApp document message or approved document
        template when outside Meta's 24-hour customer service window.
        Atomically claimed and idempotent per booking_ref + invoice_number.
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

        template_name, template_lang = cls._resolve_invoice_template_settings()
        window_open = (
            cls._customer_service_window_open(db, phone)
            if hasattr(cls, "_customer_service_window_open")
            else False
        )
        channel_used = "document"
        body_params: Optional[List[str]] = None

        def _send_session_document() -> Dict[str, Any]:
            return whatsapp_client.send_document_message(
                phone,
                filename=filename,
                caption=caption,
                document_url=document_url,
                pdf_bytes=pdf_bytes,
            )

        def _send_template_document(language: str) -> Dict[str, Any]:
            nonlocal body_params
            if body_params is None:
                body_params = cls._build_invoice_template_body_params(
                    db,
                    booking_ref=booking_ref,
                    invoice_number=invoice_number,
                )
            return whatsapp_client.send_document_template_message(
                phone,
                template_name=template_name,
                template_language=language,
                filename=filename,
                body_parameters=body_params,
                document_url=document_url,
                pdf_bytes=pdf_bytes,
            )

        try:
            if template_name and not window_open:
                channel_used = "document_template"
                result = _send_template_document(template_lang)
            else:
                result = _send_session_document()
                if (
                    template_name
                    and not result.get("success")
                    and result.get("error_code") == 131047
                ):
                    logger.warning(
                        "[WhatsApp Invoice] Session document rejected for %s (131047); retrying via template %s/%s",
                        booking_ref,
                        template_name,
                        template_lang,
                    )
                    channel_used = "document_template"
                    result = _send_template_document(template_lang)

            if (
                channel_used == "document_template"
                and template_name
                and not result.get("success")
                and result.get("error_code") == 132001
                and "_" in template_lang
            ):
                base_lang = template_lang.split("_", 1)[0].strip()
                if base_lang and base_lang != template_lang:
                    retry_res = _send_template_document(base_lang)
                    if retry_res and retry_res.get("success"):
                        result = retry_res
                        template_lang = base_lang
        except Exception as exc:
            logger.warning("[WhatsApp Invoice] Document send exception: %s", type(exc).__name__)
            result = {"success": False, "error": "exception", "status": "failed"}

        now = datetime.now(timezone.utc)
        record.payload = {
            **(record.payload or payload),
            "delivery": {
                "channel": channel_used,
                "template": template_name or None,
                "template_language": template_lang if channel_used == "document_template" else None,
                "error_code": result.get("error_code"),
                "http_status": result.get("status_code"),
                "updated_at": now.isoformat(),
            },
        }

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
            return {"status": "DELIVERED", "message_id": result.get("message_id"), "channel": channel_used}

        if result.get("status") == "timeout_unverified":
            record.status = NotificationStatus.PENDING
            record.error_log = "Timeout connecting to Meta WhatsApp API; delivery state unknown (not retried automatically)."
            record.updated_at = now
            try:
                db.commit()
            except Exception:
                db.rollback()
            return {"status": "UNKNOWN", "error": record.error_log}

        err_text = str(result.get("error") or result.get("status") or "whatsapp_document_failed")
        if result.get("error_code") == 131047 and not template_name:
            err_text = (
                f"{err_text} | Customer outside 24h window: set WHATSAPP_INVOICE_PDF_TEMPLATE "
                "to an approved document-header utility template"
            )
        record.status = NotificationStatus.FAILED
        record.error_log = err_text[:400]
        record.updated_at = now
        try:
            db.commit()
        except Exception:
            db.rollback()
        return {"status": "FAILED", "error": record.error_log, "error_code": result.get("error_code")}
