"""Admin Notification Router for real-time SSE alerts and notification lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth_service import AuthService
from app.security.dependencies import ADMIN_ROLES, get_required_admin
from app.services.admin_notification_service import AdminNotificationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/notifications", tags=["Admin Real-Time Notifications"])


def authenticate_admin_token(
    token: Optional[str] = None,
    authorization: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Authenticate an admin for the SSE stream.
    Supports either ?token=<jwt> (EventSource compatible) or Authorization: Bearer <jwt>.
    """
    jwt_token = token
    if not jwt_token and authorization and authorization.startswith("Bearer "):
        jwt_token = authorization.split(" ")[1]

    if not jwt_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required for notifications stream.",
        )

    try:
        user = AuthService.decode_access_token(jwt_token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired or invalid.",
        ) from exc

    role = user.get("role")
    if role not in ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Insufficient administrative permissions.",
        )

    return user


@router.get("/stream")
async def notifications_stream(
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
):
    """
    Server-Sent Events (SSE) endpoint for authorized admin portal sessions.
    Streams real-time booking alerts and sends heartbeat pings every 25s.
    """
    admin_user = authenticate_admin_token(token, authorization)
    manager = AdminNotificationService.get_manager()
    queue = manager.subscribe()

    async def event_generator():
        # Initial connection handshake
        handshake = {
            "type": "CONNECTION_ESTABLISHED",
            "message": "Connected to Shafsky Admin real-time notifications stream.",
            "admin_email": admin_user.get("email"),
        }
        yield f"event: connected\ndata: {json.dumps(handshake)}\n\n"

        try:
            while True:
                try:
                    # Wait for next notification with 25-second keepalive timeout
                    event_data = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"event: notification\ndata: {json.dumps(event_data)}\n\n"
                except asyncio.TimeoutError:
                    # Send standard SSE keepalive comment
                    yield ": ping\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            manager.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("")
async def list_notifications(
    limit: int = Query(50, ge=1, le=100),
    unread_only: bool = Query(False),
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_required_admin),
):
    """
    List recent admin notifications and unread count.
    Used for initial load and reconnection catch-up.
    """
    result = AdminNotificationService.list_notifications(
        db, limit=limit, unread_only=unread_only
    )
    return {"success": True, **result}


@router.post("/{notification_id}/read")
async def mark_notification_as_read(
    notification_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin_user: Dict[str, Any] = Depends(get_required_admin),
):
    """
    Mark a single notification as read.
    """
    user_id_raw = admin_user.get("id") or admin_user.get("user_id")
    admin_uuid = None
    if user_id_raw:
        try:
            admin_uuid = (
                uuid.UUID(str(user_id_raw))
                if not isinstance(user_id_raw, uuid.UUID)
                else user_id_raw
            )
        except (ValueError, TypeError):
            admin_uuid = None

    notif = AdminNotificationService.mark_as_read(
        db, notification_id, admin_user_id=admin_uuid
    )
    if not notif:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found.",
        )
    return {"success": True, "notification": notif}


@router.post("/read-all")
async def mark_all_notifications_as_read(
    db: Session = Depends(get_db),
    admin_user: Dict[str, Any] = Depends(get_required_admin),
):
    """
    Mark all unread notifications as read.
    """
    user_id_raw = admin_user.get("id") or admin_user.get("user_id")
    admin_uuid = None
    if user_id_raw:
        try:
            admin_uuid = (
                uuid.UUID(str(user_id_raw))
                if not isinstance(user_id_raw, uuid.UUID)
                else user_id_raw
            )
        except (ValueError, TypeError):
            admin_uuid = None

    count = AdminNotificationService.mark_all_as_read(
        db, admin_user_id=admin_uuid
    )
    return {"success": True, "marked_count": count}
