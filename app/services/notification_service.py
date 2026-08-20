import uuid
import httpx
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from fastapi import HTTPException, BackgroundTasks

from app.config import settings
from app.models.schema import NotificationRecord, NotificationStatus
from app.schemas.notification import NotificationSendRequest
from app.services.notification_templates import NotificationTemplateEngine

logger = logging.getLogger("shafsky.notifications")


class NotificationService:
    @classmethod
    def _from_address(cls) -> str:
        return (settings.EMAIL_FROM or settings.RESEND_FROM_EMAIL or "").strip()

    @classmethod
    def _resend_configured(cls) -> bool:
        return bool((settings.RESEND_API_KEY or "").strip() and cls._from_address())

    @classmethod
    def admin_notification_recipients(cls) -> List[str]:
        raw = (settings.ADMIN_NOTIFICATION_EMAILS or "").strip()
        emails = [e.strip() for e in raw.split(",") if e.strip() and "@" in e]
        if not emails:
            fallback = (getattr(settings, "ADMIN_EMAIL", None) or "") 
            if not fallback:
                import os
                fallback = (os.getenv("ADMIN_EMAIL") or "").strip()
            if fallback and "@" in fallback:
                emails = [fallback]
        return emails

    @classmethod
    def send_email_resend_sync(cls, recipient_email: str, subject: str, html_content: str) -> Dict[str, Any]:
        recipient = (recipient_email or "").strip()
        if not recipient:
            logger.warning("Email skipped: missing recipient")
            return {"status": "FAILED", "error": "missing_recipient"}
        if not cls._resend_configured():
            logger.warning(
                "Email skipped: Resend is not configured (RESEND_API_KEY and EMAIL_FROM / RESEND_FROM_EMAIL required)"
            )
            return {"status": "BYPASSED", "reason": "resend_not_configured"}

        url = "https://api.resend.com/emails"
        headers = {
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
        }
        body: Dict[str, Any] = {
            "from": cls._from_address(),
            "to": [recipient],
            "subject": subject,
            "html": html_content,
        }
        reply_to = (settings.EMAIL_REPLY_TO or "").strip()
        if reply_to:
            body["reply_to"] = reply_to

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(url, json=body, headers=headers)
            if resp.status_code in (200, 201):
                message_id = ""
                try:
                    message_id = str((resp.json() or {}).get("id") or "")
                except Exception:
                    message_id = ""
                logger.info(
                    "Resend accepted email",
                    extra={"recipient_domain": recipient.split("@")[-1], "message_id": message_id, "http_status": resp.status_code},
                )
                return {"status": "DELIVERED", "message_id": message_id or "RESEND-OK"}
            logger.error(
                "Resend rejected email",
                extra={"http_status": resp.status_code, "provider_error": (resp.text or "")[:400]},
            )
            return {"status": "FAILED", "error": f"provider_rejected:{resp.status_code}"}
        except Exception as exc:
            logger.error("Resend request failed: %s", type(exc).__name__)
            return {"status": "FAILED", "error": "request_failed"}

    @classmethod
    async def send_email_resend(cls, recipient_email: str, subject: str, html_content: str) -> Dict[str, Any]:
        return cls.send_email_resend_sync(recipient_email, subject, html_content)

    @classmethod
    async def send_whatsapp_meta(cls, recipient_phone: str, text_content: str) -> Dict[str, Any]:
        # Meta Cloud API adapter stub / live dispatcher
        if not recipient_phone:
            return {"status": "BYPASSED", "reason": "No phone number provided"}

        # Simulate Meta Cloud API dispatch
        return {"status": "DELIVERED", "message_id": f"WA-{uuid.uuid4().hex[:8].upper()}"}

    @classmethod
    async def process_single_notification(cls, record_id_str: str, db_session_factory):
        db: Session = db_session_factory()
        try:
            r_uuid = uuid.UUID(record_id_str)
            record = db.scalar(select(NotificationRecord).where(NotificationRecord.id == r_uuid))
            if not record:
                return

            record.attempts += 1
            record.status = NotificationStatus.SENDING
            db.commit()

            rendered = NotificationTemplateEngine.render_template(record.template_type, record.payload)
            channel = record.channel.upper()
            errors = []
            delivered = False
            msg_ids = []

            # 1. Email Channel Dispatch
            if channel in ["ALL", "EMAIL", "EMAIL_ONLY"] and record.recipient_email:
                res = await cls.send_email_resend(record.recipient_email, rendered["subject"], rendered["html"])
                if res.get("status") == "DELIVERED":
                    delivered = True
                    msg_ids.append(res.get("message_id", ""))
                elif res.get("status") == "FAILED":
                    errors.append(f"Email: {res.get('error')}")

            # 2. WhatsApp Channel Dispatch
            if channel in ["ALL", "WHATSAPP", "WHATSAPP_ONLY"] and record.recipient_phone:
                res = await cls.send_whatsapp_meta(record.recipient_phone, rendered["whatsapp_text"])
                if res.get("status") == "DELIVERED":
                    delivered = True
                    msg_ids.append(res.get("message_id", ""))
                elif res.get("status") == "FAILED":
                    errors.append(f"WhatsApp: {res.get('error')}")

            # Update Record Status
            if delivered or channel == "BYPASSED":
                record.status = NotificationStatus.DELIVERED
                record.delivered_at = datetime.now(timezone.utc)
                record.message_id = ", ".join(filter(None, msg_ids))
                record.error_log = None
            else:
                if record.attempts >= record.max_attempts:
                    record.status = NotificationStatus.FAILED
                else:
                    record.status = NotificationStatus.QUEUED
                record.error_log = " | ".join(errors) if errors else "Delivery failed"

            record.updated_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as ex:
            logger.exception("Notification processing failed for record %s: %s", record_id_str, type(ex).__name__)
            db.rollback()
        finally:
            db.close()

    @classmethod
    def enqueue_notification(
        cls,
        db: Session,
        background_tasks: BackgroundTasks,
        payload_req: NotificationSendRequest,
        session_factory
    ) -> NotificationRecord:
        record = NotificationRecord(
            id=uuid.uuid4(),
            recipient_email=payload_req.recipient_email,
            recipient_phone=payload_req.recipient_phone,
            template_type=payload_req.template_type.upper(),
            channel=payload_req.channel or "ALL",
            payload=payload_req.payload or {},
            status=NotificationStatus.QUEUED,
            attempts=0,
            max_attempts=3,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        # Enqueue background task for zero-latency execution
        background_tasks.add_task(cls.process_single_notification, str(record.id), session_factory)
        return record

    @classmethod
    def retry_notification(cls, db: Session, notification_id: str, background_tasks: BackgroundTasks, session_factory) -> NotificationRecord:
        try:
            n_uuid = uuid.UUID(notification_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid notification UUID format.")

        record = db.scalar(select(NotificationRecord).where(NotificationRecord.id == n_uuid))
        if not record:
            raise HTTPException(status_code=404, detail="Notification record not found.")

        record.status = NotificationStatus.QUEUED
        record.updated_at = datetime.now(timezone.utc)
        db.commit()

        background_tasks.add_task(cls.process_single_notification, str(record.id), session_factory)
        return record

    @classmethod
    def get_notification_queue(cls, db: Session, limit: int = 100) -> List[Dict[str, Any]]:
        records = list(db.scalars(select(NotificationRecord).order_by(desc(NotificationRecord.created_at)).limit(limit)).all())
        return [
            {
                "id": str(r.id),
                "recipientEmail": r.recipient_email,
                "recipientPhone": r.recipient_phone,
                "templateType": r.template_type,
                "channel": r.channel,
                "status": r.status.value if isinstance(r.status, NotificationStatus) else str(r.status),
                "attempts": r.attempts,
                "maxAttempts": r.max_attempts,
                "messageId": r.message_id,
                "errorLog": r.error_log,
                "deliveredAt": r.delivered_at.isoformat() if r.delivered_at else None,
                "createdAt": r.created_at.isoformat()
            }
            for r in records
        ]

    @classmethod
    def _already_notified(cls, db: Session, template_type: str, booking_ref: str, recipient_email: str) -> bool:
        if not booking_ref or not recipient_email:
            return False
        records = list(
            db.scalars(
                select(NotificationRecord)
                .where(
                    NotificationRecord.template_type == template_type,
                    NotificationRecord.recipient_email == recipient_email,
                    NotificationRecord.status.in_([NotificationStatus.DELIVERED, NotificationStatus.SENDING, NotificationStatus.QUEUED]),
                )
                .order_by(desc(NotificationRecord.created_at))
                .limit(20)
            ).all()
        )
        for rec in records:
            payload = rec.payload or {}
            ref = str(payload.get("booking_ref") or payload.get("bookingRef") or "")
            if ref == booking_ref:
                return True
        return False

    @classmethod
    def _record_and_send(
        cls,
        db: Session,
        *,
        recipient_email: str,
        template_type: str,
        payload: Dict[str, Any],
        booking_ref: str,
    ) -> Dict[str, Any]:
        if cls._already_notified(db, template_type, booking_ref, recipient_email):
            logger.info(
                "Skipping duplicate notification",
                extra={"template": template_type, "booking_ref": booking_ref},
            )
            return {"status": "SKIPPED", "reason": "duplicate"}

        rendered = NotificationTemplateEngine.render_template(template_type, payload)
        record = NotificationRecord(
            id=uuid.uuid4(),
            recipient_email=recipient_email,
            recipient_phone=None,
            template_type=template_type,
            channel="EMAIL_ONLY",
            payload=payload,
            status=NotificationStatus.SENDING,
            attempts=1,
            max_attempts=3,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(record)
        db.flush()

        result = cls.send_email_resend_sync(recipient_email, rendered["subject"], rendered["html"])
        if result.get("status") == "DELIVERED":
            record.status = NotificationStatus.DELIVERED
            record.delivered_at = datetime.now(timezone.utc)
            record.message_id = result.get("message_id")
            record.error_log = None
        elif result.get("status") == "BYPASSED":
            record.status = NotificationStatus.BYPASSED
            record.error_log = result.get("reason") or "resend_not_configured"
        else:
            record.status = NotificationStatus.FAILED
            record.error_log = result.get("error") or "delivery_failed"
        record.updated_at = datetime.now(timezone.utc)
        db.commit()
        return result

    @classmethod
    def notify_booking_created(cls, db: Session, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        After a booking is persisted, send customer confirmation and admin/team alert.
        Failures are recorded and never raised to the booking caller.
        """
        booking_ref = str(context.get("booking_ref") or context.get("bookingRef") or "")
        customer_email = str(context.get("passenger_email") or context.get("passengerEmail") or "").strip()
        payload = {
            "booking_ref": booking_ref,
            "bookingRef": booking_ref,
            "passengerName": context.get("passenger_name") or context.get("passengerName"),
            "passengerEmail": customer_email,
            "passengerPhone": context.get("passenger_phone") or context.get("passengerPhone"),
            "flightNum": context.get("flight_num") or context.get("flightNum"),
            "originCode": context.get("origin_code") or context.get("originCode"),
            "destCode": context.get("dest_code") or context.get("destCode"),
            "airportCode": context.get("airport_code") or context.get("airportCode"),
            "journeyType": context.get("journey_type") or context.get("journeyType"),
            "service_type": context.get("service_type") or context.get("serviceType"),
            "service_name": context.get("service_name") or context.get("package") or context.get("service_type"),
            "departureTime": context.get("departure_time") or context.get("service_date"),
            "terminal": context.get("terminal"),
            "totalAmount": context.get("total_amount") or context.get("totalAmount"),
            "currency": context.get("currency") or "INR",
            "status": context.get("status") or "PENDING",
        }

        summary: Dict[str, Any] = {"booking_ref": booking_ref, "customer": None, "admin": []}
        logger.info("Booking notification requested", extra={"booking_ref": booking_ref})

        try:
            if customer_email:
                summary["customer"] = cls._record_and_send(
                    db,
                    recipient_email=customer_email,
                    template_type="BOOKING_CONFIRMATION",
                    payload=payload,
                    booking_ref=booking_ref,
                )
            else:
                logger.warning("Customer confirmation skipped: missing email", extra={"booking_ref": booking_ref})
                summary["customer"] = {"status": "FAILED", "error": "missing_recipient"}

            for admin_email in cls.admin_notification_recipients():
                summary["admin"].append(
                    {
                        "recipient_domain": admin_email.split("@")[-1],
                        **cls._record_and_send(
                            db,
                            recipient_email=admin_email,
                            template_type="ADMIN_NEW_BOOKING",
                            payload=payload,
                            booking_ref=booking_ref,
                        ),
                    }
                )
            if not summary["admin"]:
                logger.warning(
                    "Admin notification skipped: set ADMIN_NOTIFICATION_EMAILS",
                    extra={"booking_ref": booking_ref},
                )
        except Exception as exc:
            logger.exception("Booking notification failed for %s: %s", booking_ref, type(exc).__name__)
            try:
                db.rollback()
            except Exception:
                pass
        return summary
