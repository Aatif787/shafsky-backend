from typing import Dict, Any
from fastapi import APIRouter, Depends, BackgroundTasks, Query
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.schemas.notification import NotificationSendRequest, NotificationApiResponse
from app.services.notification_service import NotificationService
from app.security.dependencies import get_required_admin

router = APIRouter(prefix="/api/notifications", tags=["Communication & Automation Hub"])

@router.post("/send", response_model=NotificationApiResponse)
async def send_notification(
    payload: NotificationSendRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
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
