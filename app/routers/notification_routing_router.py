"""Super Admin APIs for WhatsApp notification recipients, routes, and routing mode.

Mounted from app/main.py. No existing router owns /api/admin/whatsapp-notifications.
Every route depends on get_required_super_admin. These endpoints do not send
WhatsApp messages and are not mounted on a public path.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.notification_routing import (
    EnvImportEnvelope,
    NotificationConfigEnvelope,
    RecipientCreate,
    RecipientDeleteResponse,
    RecipientEnvelope,
    RecipientListResponse,
    RecipientUpdate,
    RoutingSettingsEnvelope,
    RoutingSettingsUpdate,
    ServiceKeyListResponse,
    ServiceRouteUpdateRequest,
    ServiceRoutingEnvelope,
    ServiceRoutingListResponse,
)
from app.security.dependencies import get_required_super_admin
from app.services.notification_routing_service import NotificationRoutingService

def _routing_schema(
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_required_super_admin),
) -> None:
    NotificationRoutingService.ensure_schema(db)


router = APIRouter(
    prefix="/api/admin/whatsapp-notifications",
    tags=["WhatsApp Notification Routing"],
    dependencies=[Depends(_routing_schema)],
)


def _ip(request: Request) -> Optional[str]:
    if request.client is None:
        return None
    return request.client.host


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("/config", response_model=NotificationConfigEnvelope)
def get_notification_config(
    response: Response,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    _no_store(response)
    return {"success": True, "data": NotificationRoutingService.get_config(db)}


@router.get("/recipients", response_model=RecipientListResponse)
def list_recipients(
    response: Response,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    _no_store(response)
    return {"success": True, "data": NotificationRoutingService.list_recipients(db)}


@router.post("/recipients", response_model=RecipientEnvelope, status_code=201)
def create_recipient(
    payload: RecipientCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    data = NotificationRoutingService.add_recipient(db, payload, admin, _ip(request))
    return {"success": True, "data": data}


@router.patch("/recipients/{recipient_id}", response_model=RecipientEnvelope)
def update_recipient(
    recipient_id: uuid.UUID,
    payload: RecipientUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    data = NotificationRoutingService.update_recipient(db, recipient_id, payload, admin, _ip(request))
    return {"success": True, "data": data}


@router.delete("/recipients/{recipient_id}", response_model=RecipientDeleteResponse)
def delete_recipient(
    recipient_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    data = NotificationRoutingService.delete_recipient(db, recipient_id, admin, _ip(request))
    return {"success": True, "data": data}


@router.get("/services", response_model=ServiceKeyListResponse)
def list_services(
    response: Response,
    _admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    _no_store(response)
    return {"success": True, "data": NotificationRoutingService.list_service_keys()}


@router.get("/routes", response_model=ServiceRoutingListResponse)
def list_routes(
    response: Response,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    _no_store(response)
    return {"success": True, "data": NotificationRoutingService.list_routes(db)}


@router.put("/routes/{service_key}", response_model=ServiceRoutingEnvelope)
def replace_routes(
    service_key: str,
    payload: ServiceRouteUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    data = NotificationRoutingService.replace_routes(db, service_key, payload, admin, _ip(request))
    return {"success": True, "data": data}


@router.get("/settings", response_model=RoutingSettingsEnvelope)
def get_settings(
    response: Response,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    _no_store(response)
    return {"success": True, "data": NotificationRoutingService.get_settings(db)}


@router.patch("/settings", response_model=RoutingSettingsEnvelope)
def update_settings(
    payload: RoutingSettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    data = NotificationRoutingService.update_settings(db, payload.routing_mode, admin, _ip(request))
    return {"success": True, "data": data}


@router.post("/import-env-recipients", response_model=EnvImportEnvelope)
def import_env_recipients(
    request: Request,
    db: Session = Depends(get_db),
    admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    data = NotificationRoutingService.import_env_recipients(db, admin, _ip(request))
    return {"success": True, "data": data}
