"""
FastAPI Router for Website Contact Management System.
Provides public read-only and admin CRUD endpoints for the six canonical services.
"""

import logging
from typing import Any, Dict
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.contact_settings import (
    AdminContactSettingsResponse,
    ContactSettingsUpdateRequest,
    PublicContactSettingsResponse,
)
from app.security.dependencies import get_required_admin
from app.services.contact_settings_service import ContactSettingsService

logger = logging.getLogger("shafsky.contact_settings_router")

router = APIRouter(tags=["Website Contact Settings"])


@router.get("/api/public/contact-settings", response_model=PublicContactSettingsResponse)
async def get_public_contact_settings(
    response: Response,
    db: Session = Depends(get_db),
):
    """
    Public, read-only endpoint returning configured active phone numbers
    for the six website services. Unconfigured or cleared slots are omitted.
    """
    # Short cache duration to avoid indefinite caching while remaining fast
    response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=60"
    data = ContactSettingsService.get_public_contact_settings(db)
    return PublicContactSettingsResponse(success=True, data=data)


@router.get("/api/admin/contact-settings", response_model=AdminContactSettingsResponse)
async def get_admin_contact_settings(
    response: Response,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_required_admin),
):
    """
    Administrative endpoint returning all six services with both slot 1 and slot 2.
    Includes empty and disabled slots. Requires authorized admin.
    """
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    data = ContactSettingsService.get_admin_contact_settings(db)
    return AdminContactSettingsResponse(success=True, data=data)


@router.put("/api/admin/contact-settings", response_model=AdminContactSettingsResponse)
async def update_admin_contact_settings(
    payload: ContactSettingsUpdateRequest,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_required_admin),
):
    """
    Updates the contact phone numbers for specified services.
    Validates numbers and persists transactionally. Requires authorized admin.
    """
    updated = ContactSettingsService.update_contact_settings(db, payload)
    return AdminContactSettingsResponse(success=True, data=updated)
