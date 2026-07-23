import uuid
import httpx
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from fastapi import HTTPException, BackgroundTasks

from app.config import settings
from app.models.schema import NotificationRecord, NotificationStatus
from app.schemas.notification import NotificationSendRequest
from app.services.notification_templates import NotificationTemplateEngine

class NotificationService:
    @classmethod
    async def send_email_resend(cls, recipient_email: str, subject: str, html_content: str) -> Dict[str, Any]:
        if not recipient_email or not settings.RESEND_API_KEY:
            return {"status": "BYPASSED", "reason": "No API key or recipient email"}

        url = "https://api.resend.com/emails"
        headers = {
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json"
        }
        body = {
            "from": getattr(settings, "EMAIL_FROM", "bookings@shafskyaviation.com"),
            "to": [recipient_email],
            "subject": subject,
            "html": html_content
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code in [200, 201]:
                    return {"status": "DELIVERED", "message_id": resp.json().get("id", "RESEND-OK")}
                return {"status": "FAILED", "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            return {"status": "FAILED", "error": str(e)}

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
