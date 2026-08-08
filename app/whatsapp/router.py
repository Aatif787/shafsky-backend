"""
REST Router for Official Meta WhatsApp Cloud API Webhook Integration.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, status, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.whatsapp.client import whatsapp_client
from app.whatsapp.schemas import WhatsAppWebhookPayload, WhatsAppApiResponse
from app.whatsapp.service import WhatsAppService

from app.integrations.whatsapp.router import router as whatsapp_integration_router

router = whatsapp_integration_router

__all__ = ["router"]
