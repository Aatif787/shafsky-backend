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

router = APIRouter(prefix="/api/whatsapp", tags=["Official Meta WhatsApp Cloud API"])


@router.get(
    "/webhook",
    summary="Meta WhatsApp Cloud API Webhook Challenge Verification"
)
def verify_whatsapp_webhook_challenge(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge")
):
    """Verifies challenge parameter sent by Meta Graph API server."""
    challenge = whatsapp_client.verify_webhook_challenge(
        mode=hub_mode,
        token=hub_verify_token,
        challenge=hub_challenge
    )
    if challenge:
        return Response(content=challenge, media_type="text/plain", status_code=200)

    raise HTTPException(status_code=403, detail="Webhook challenge verification failed.")


@router.post(
    "/webhook",
    response_model=WhatsAppApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Meta WhatsApp Cloud API Event Ingestion Webhook"
)
def handle_whatsapp_webhook_event(
    payload: WhatsAppWebhookPayload,
    db: Session = Depends(get_db)
):
    """Receives and processes incoming WhatsApp events from Meta Cloud API."""
    try:
        result = WhatsAppService.handle_incoming_webhook(db, payload)
        return WhatsAppApiResponse(success=True, data=result)
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err)) from err
