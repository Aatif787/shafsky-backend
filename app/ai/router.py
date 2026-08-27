"""
REST Router for AI Conversation Engine, Lifecycle Management & Human Handoff.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.ai.schemas import (
    ChatRequest,
    AiApiResponse,
    TakeoverRequest,
    ResumeRequest
)
from app.ai.service import AiService
from app.security.dependencies import get_required_staff_or_admin

router = APIRouter(prefix="/api/ai", tags=["AI Conversation Engine"])


@router.post(
    "/chat",
    response_model=AiApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Website / App Interactive AI Chat"
)
def interactive_chat_endpoint(
    payload: ChatRequest,
    db: Session = Depends(get_db),
):
    """
    Public website chat (rate-limited by SecurityMiddleware).
    Does not expose staff takeover/history. Abuse controls: IP rate limit on /api/ai/chat.
    """
    try:
        response = AiService.process_chat(db, payload)
        return AiApiResponse(success=True, data=response)
    except Exception as err:
        raise HTTPException(status_code=500, detail="AI chat temporarily unavailable.") from err


@router.post(
    "/whatsapp",
    response_model=AiApiResponse,
    status_code=status.HTTP_403_FORBIDDEN,
    summary="Disabled — use official Meta WhatsApp webhook"
)
@router.post(
    "/webhook/whatsapp",
    response_model=AiApiResponse,
    status_code=status.HTTP_403_FORBIDDEN,
    summary="Disabled — use official Meta WhatsApp webhook"
)
def whatsapp_webhook_endpoint_disabled():
    """
    C10: Public AI WhatsApp ingest is disabled.
    Use POST /api/whatsapp/webhook (HMAC-verified Meta Cloud API) instead.
    """
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="This endpoint is disabled. Use the official Meta WhatsApp webhook at /api/whatsapp/webhook.",
    )


@router.post(
    "/takeover",
    response_model=AiApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Human Staff Conversation Takeover"
)
def staff_takeover_endpoint(
    payload: TakeoverRequest,
    db: Session = Depends(get_db),
    _staff=Depends(get_required_staff_or_admin),
):
    """Transfers conversation from AI to human staff duty officer."""
    try:
        result = AiService.take_over_conversation(db, payload)
        return AiApiResponse(success=True, data=result)
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err)) from err


@router.post(
    "/resume",
    response_model=AiApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume AI Assistant Control"
)
def resume_ai_endpoint(
    payload: ResumeRequest,
    db: Session = Depends(get_db),
    _staff=Depends(get_required_staff_or_admin),
):
    """Resumes AI control of conversation from human staff."""
    try:
        result = AiService.resume_ai_conversation(db, payload)
        return AiApiResponse(success=True, data=result)
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err)) from err


@router.get(
    "/conversations/{conversation_id}",
    response_model=AiApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve Active Conversation Session Details"
)
def get_conversation_details_endpoint(
    conversation_id: str,
    _staff=Depends(get_required_staff_or_admin),
):
    """Retrieves full conversation session state and message history."""
    try:
        session = ConversationMemory.get_session(conversation_id)
        return AiApiResponse(success=True, data=session)
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err)) from err
