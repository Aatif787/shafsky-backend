"""
Pydantic Schemas for AI Conversation Module & Lifecycle Management.
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from pydantic import BaseModel, Field


class ConversationState(str, Enum):
    NEW = "NEW"
    COLLECTING_DETAILS = "COLLECTING_DETAILS"
    WAITING_FOR_CUSTOMER = "WAITING_FOR_CUSTOMER"
    PROCESSING = "PROCESSING"
    WAITING_FOR_PAYMENT = "WAITING_FOR_PAYMENT"
    WAITING_FOR_STAFF = "WAITING_FOR_STAFF"
    HANDOFF_TO_HUMAN = "HANDOFF_TO_HUMAN"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"


class ChatMessage(BaseModel):
    role: str = Field(..., description="Message sender role: 'user', 'assistant', or 'staff'")
    content: str = Field(..., description="Message text content")
    timestamp: Optional[str] = None


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Unique conversation reference ID")
    message: str = Field(..., min_length=1, description="Incoming message")
    user_id: Optional[str] = None
    phone_number: Optional[str] = None
    channel: str = Field("WEB", description="Channel code: 'WEB', 'WHATSAPP', 'SMS'")
    metadata: Optional[Dict[str, Any]] = None


class ConversationSessionData(BaseModel):
    conversation_id: str
    phone_number: Optional[str] = None
    booking_id: Optional[str] = None
    current_state: ConversationState = ConversationState.NEW
    active_intent: Optional[str] = None
    selected_services: List[str] = Field(default_factory=list)
    selected_airport: Optional[str] = None
    selected_flight: Optional[str] = None
    assigned_staff: Optional[str] = None
    failed_intent_attempts: int = 0
    last_messages: List[Dict[str, Any]] = Field(default_factory=list)


class ChatResponseData(BaseModel):
    session_id: str
    reply: str
    channel: str
    current_state: ConversationState
    assigned_staff: Optional[str] = None
    handoff_triggered: bool = False
    tool_calls_executed: List[str] = Field(default_factory=list)
    timestamp: str


class TakeoverRequest(BaseModel):
    conversation_id: str
    staff_user_id: str
    notes: Optional[str] = None


class ResumeRequest(BaseModel):
    conversation_id: str
    reason: Optional[str] = None


class WhatsAppWebhookPayload(BaseModel):
    object: Optional[str] = None
    entry: Optional[List[Dict[str, Any]]] = None
    from_number: Optional[str] = None
    message_body: Optional[str] = None


class AiApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
