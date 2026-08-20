from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, BackgroundTasks, Query, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.schemas.notification import NotificationSendRequest, NotificationApiResponse
from app.services.notification_service import NotificationService
from app.security.dependencies import get_required_admin, get_required_user

router = APIRouter(prefix="/api/notifications", tags=["Communication & Automation Hub"])

@router.post("/send", response_model=NotificationApiResponse)
async def send_notification(
    payload: NotificationSendRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    record = NotificationService.enqueue_notification(
        db,
        background_tasks,
        payload,
        session_factory=SessionLocal
    )
    return NotificationApiResponse(
        success=True,
        data={
            "id": str(record.id),
            "templateType": record.template_type,
            "status": record.status.value if hasattr(record.status, "value") else str(record.status),
            "message": "Notification enqueued for asynchronous delivery."
        }
    )

@router.get("/queue", response_model=NotificationApiResponse)
async def get_notification_queue(
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    queue = NotificationService.get_notification_queue(db, limit=limit)
    return NotificationApiResponse(success=True, data=queue)

@router.post("/{notification_id}/retry", response_model=NotificationApiResponse)
async def retry_failed_notification(
    notification_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    record = NotificationService.retry_notification(
        db,
        notification_id,
        background_tasks,
        session_factory=SessionLocal
    )
    return NotificationApiResponse(
        success=True,
        data={
            "id": str(record.id),
            "status": record.status.value if hasattr(record.status, "value") else str(record.status),
            "message": "Notification re-queued for background delivery."
        }
    )

@router.post("/webhooks/{provider}")
async def provider_webhook_listener(
    provider: str,
    _payload: Dict[str, Any]
):
    # Webhook delivery receipt processing
    return {"status": "SUCCESS", "provider": provider, "processed": True}


from datetime import datetime, timezone
import uuid
from fastapi import Header
from sqlalchemy import select, update, delete
from app.models.schema import UserNotification
from app.services.auth_service import AuthService


@router.get("", response_model=NotificationApiResponse)
@router.get("/", response_model=NotificationApiResponse)
async def list_user_notifications(
    authorization: Optional[str] = Header(None),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    user_context: Dict[str, Any] = Depends(get_required_user),
):
    user_id_str = user_context.get("user_id") or user_context.get("userId")

    try:
        u_uuid = uuid.UUID(user_id_str) if user_id_str else None
    except Exception:
        u_uuid = None

    if u_uuid:
        records = list(db.scalars(
            select(UserNotification)
            .where(UserNotification.user_id == u_uuid)
            .order_by(UserNotification.created_at.desc())
            .limit(limit)
        ).all())
    else:
        records = []

    data = [
        {
            "id": str(r.id),
            "userId": str(r.user_id),
            "kind": r.kind,
            "title": r.title,
            "body": r.body,
            "link": r.link,
            "readAt": r.read_at.isoformat() if r.read_at else None,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]

    return NotificationApiResponse(success=True, data=data)


@router.post("/{notification_id}/read", response_model=NotificationApiResponse)
async def mark_notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    user_context: Dict[str, Any] = Depends(get_required_user),
):
    try:
        n_uuid = uuid.UUID(notification_id)
    except Exception:
        return NotificationApiResponse(success=False, data={"error": "Invalid notification ID format."})

    n = db.scalar(select(UserNotification).where(UserNotification.id == n_uuid))
    if not n:
        return NotificationApiResponse(success=False, data={"error": "Notification not found."})
    user_id_str = str(user_context.get("user_id") or user_context.get("userId") or "")
    if user_id_str and str(n.user_id) != user_id_str:
        raise HTTPException(status_code=403, detail="Access denied.")

    n.read_at = datetime.now(timezone.utc)
    db.commit()

    return NotificationApiResponse(
        success=True,
        data={"id": str(n.id), "readAt": n.read_at.isoformat(), "message": "Notification marked as read."}
    )


@router.post("/read-all", response_model=NotificationApiResponse)
async def mark_all_notifications_read(
    db: Session = Depends(get_db),
    user_context: Dict[str, Any] = Depends(get_required_user),
):
    user_id_str = user_context.get("user_id") or user_context.get("userId")
    try:
        u_uuid = uuid.UUID(user_id_str) if user_id_str else None
    except Exception:
        u_uuid = None

    now = datetime.now(timezone.utc)
    if not u_uuid:
        raise HTTPException(status_code=401, detail="Missing user identity.")
    db.execute(
        update(UserNotification)
        .where(UserNotification.user_id == u_uuid, UserNotification.read_at.is_(None))
        .values(read_at=now)
    )
    db.commit()

    return NotificationApiResponse(
        success=True,
        data={"message": "All notifications marked as read."}
    )


@router.delete("/{notification_id}", response_model=NotificationApiResponse)
async def delete_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    user_context: Dict[str, Any] = Depends(get_required_user),
):
    try:
        n_uuid = uuid.UUID(notification_id)
    except Exception:
        return NotificationApiResponse(success=False, data={"error": "Invalid notification ID format."})

    n = db.scalar(select(UserNotification).where(UserNotification.id == n_uuid))
    if not n:
        return NotificationApiResponse(success=False, data={"error": "Notification not found."})
    user_id_str = str(user_context.get("user_id") or user_context.get("userId") or "")
    if user_id_str and str(n.user_id) != user_id_str:
        raise HTTPException(status_code=403, detail="Access denied.")
    db.execute(delete(UserNotification).where(UserNotification.id == n_uuid))
    db.commit()

    return NotificationApiResponse(
        success=True,
        data={"id": notification_id, "message": "Notification deleted."}
    )
