from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks, Query
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any

from app.database import get_db, SessionLocal
from app.schemas.notification import NotificationSendRequest, NotificationApiResponse
from app.services.notification_service import NotificationService
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/notifications", tags=["Communication & Automation Hub"])

def get_required_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization token.")
    token = authorization.split(" ")[1]
    try:
        decoded = AuthService.decode_access_token(token)
        if decoded.get("role") not in ["ADMIN", "SUPER_ADMIN", "OPERATIONS_MANAGER", "DISPATCHER"]:
            raise HTTPException(status_code=403, detail="Access denied. Administrative privileges required.")
        return decoded
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Token expired or invalid.")

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
    admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    queue = NotificationService.get_notification_queue(db, limit=limit)
    return NotificationApiResponse(success=True, data=queue)

@router.post("/{notification_id}/retry", response_model=NotificationApiResponse)
async def retry_failed_notification(
    notification_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_admin)
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
    payload: Dict[str, Any]
):
    # Webhook delivery receipt processing
    return {"status": "SUCCESS", "provider": provider, "processed": True}
