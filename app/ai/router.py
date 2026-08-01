"""
REST Router for AI Conversation Engine, Lifecycle Management & Human Handoff.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.ai.schemas import (
    ChatRequest,
    WhatsAppWebhookPayload,
    AiApiResponse,
    TakeoverRequest,
    ResumeRequest
)
from app.ai.service import AiService
from app.ai.memory import ConversationMemory

router = APIRouter(prefix="/api/ai", tags=["AI Conversation Engine"])


@router.post(
    "/chat",
    response_model=AiApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Website / App Interactive AI Chat"
)
def interactive_chat_endpoint(
    payload: ChatRequest,
    db: Session = Depends(get_db)
):
    """Processes interactive website or customer portal chat messages with state tracking."""
    try:
        response = AiService.process_chat(db, payload)
        return AiApiResponse(success=True, data=response)
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err)) from err


@router.post(
    "/whatsapp",
    response_model=AiApiResponse,
    status_code=status.HTTP_200_OK,
    summary="WhatsApp Webhook Message Processing"
)
@router.post(
    "/webhook/whatsapp",
    response_model=AiApiResponse,
    status_code=status.HTTP_200_OK,
    summary="WhatsApp Webhook Message Processing (Alias)"
)
def whatsapp_webhook_endpoint(
    payload: WhatsAppWebhookPayload,
    db: Session = Depends(get_db)
):
    """Processes incoming WhatsApp Webhook messages and returns AI assistant reply."""
    from_num = payload.from_number or "whatsapp_user"
    msg_body = payload.message_body or "Hello"

    chat_req = ChatRequest(
        session_id=f"wa_{from_num}",
        message=msg_body,
        phone_number=from_num,
        channel="WHATSAPP",
        metadata={"from_number": from_num}
    )

    try:
        response = AiService.process_chat(db, chat_req)
        return AiApiResponse(success=True, data=response)
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err)) from err


@router.post(
    "/takeover",
    response_model=AiApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Human Staff Conversation Takeover"
)
def staff_takeover_endpoint(
    payload: TakeoverRequest,
    db: Session = Depends(get_db)
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
    db: Session = Depends(get_db)
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
    conversation_id: str
):
    """Retrieves full conversation session state and message history."""
    try:
        session = ConversationMemory.get_session(conversation_id)
        return AiApiResponse(success=True, data=session)
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err)) from err
