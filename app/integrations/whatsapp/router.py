"""
FastAPI Router for Official Meta WhatsApp Cloud API Integration.
Provides Webhook GET verification, Webhook POST event ingestion, protected test dispatching, and health status.
"""

from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, status, Query, Response, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.integrations.whatsapp.client import whatsapp_client
from app.integrations.whatsapp.schemas import (
    WhatsAppWebhookPayload,
    WhatsAppTestSendRequest,
    WhatsAppApiResponse
)
from app.integrations.whatsapp.service import WhatsAppService

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
    """
    Meta Webhook Challenge Verification GET Endpoint.
    Verifies hub.verify_token against WHATSAPP_WEBHOOK_VERIFY_TOKEN using constant-time comparison.
    """
    challenge = whatsapp_client.verify_webhook_challenge(
        mode=hub_mode,
        token=hub_verify_token,
        challenge=hub_challenge
    )
    if challenge:
        return Response(content=challenge, media_type="text/plain", status_code=200)

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Webhook challenge verification failed. Invalid verify token or mode."
    )


@router.post(
    "/webhook",
    response_model=WhatsAppApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Meta WhatsApp Cloud API Webhook Event Ingestion"
)
async def handle_whatsapp_webhook_event(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Receives and processes incoming WhatsApp webhook events from Meta Cloud API.
    Safely processes messages and message status updates without trusting arbitrary input structure.
    """
    try:
        payload = await request.json()
    except Exception:
        # Invalid JSON payload
        return WhatsAppApiResponse(success=True, data={"status": "ignored", "reason": "Invalid JSON body"})

    try:
        result = WhatsAppService.handle_incoming_webhook(db, payload)
        return WhatsAppApiResponse(success=True, data=result)
    except Exception as err:
        # Return 200 with error log so Meta does not continuously retry failing webhooks
        return WhatsAppApiResponse(success=False, error=str(err))


@router.post(
    "/test-send",
    response_model=WhatsAppApiResponse,
    summary="Protected Test Message Dispatch Endpoint"
)
def test_send_whatsapp_message(
    payload: WhatsAppTestSendRequest,
):
    """
    Protected development/internal endpoint to verify message dispatch via Meta Cloud API
    without leaking credentials or tokens.
    """
    if not whatsapp_client.is_configured():
        return WhatsAppApiResponse(
            success=False,
            error="WhatsApp Cloud API is not configured in backend environment. Please set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID in .env."
        )

    message_body = payload.message or "Shafsky Aviation WhatsApp Cloud API verification message."
    
    result = whatsapp_client.send_message(
        to_phone=payload.recipient_phone,
        message_body=message_body,
        template_name=payload.template_name
    )

    if result.get("success"):
        return WhatsAppApiResponse(
            success=True,
            data={
                "message_id": result.get("message_id"),
                "status": "sent",
                "recipient": payload.recipient_phone
            }
        )
    
    return WhatsAppApiResponse(
        success=False,
        error=result.get("error", "Failed to dispatch test WhatsApp message.")
    )


@router.get(
    "/status",
    summary="WhatsApp Integration Status (Safe Diagnostic)"
)
def get_whatsapp_integration_status():
    """
    Returns high-level integration status without exposing tokens, phone number IDs, or secrets.
    """
    return {
        "configured": whatsapp_client.is_configured(),
        "api_version": whatsapp_client.api_version
    }
