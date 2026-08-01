"""
Meta WhatsApp Cloud API Webhook & Outbound Payload Schemas.
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class WhatsAppProfile(BaseModel):
    name: Optional[str] = None


class WhatsAppContact(BaseModel):
    profile: Optional[WhatsAppProfile] = None
    wa_id: str


class TextMessageContent(BaseModel):
    body: str


class WhatsAppIncomingMessage(BaseModel):
    from_number: str = Field(..., alias="from")
    id: str
    timestamp: str
    type: str
    text: Optional[TextMessageContent] = None

    class Config:
        populate_by_name = True


class WhatsAppValue(BaseModel):
    messaging_product: str
    metadata: Dict[str, Any]
    contacts: Optional[List[WhatsAppContact]] = None
    messages: Optional[List[WhatsAppIncomingMessage]] = None


class WhatsAppChange(BaseModel):
    value: WhatsAppValue
    field: str


class WhatsAppEntry(BaseModel):
    id: str
    changes: List[WhatsAppChange]


class WhatsAppWebhookPayload(BaseModel):
    object: str
    entry: List[WhatsAppEntry]


class OutboundTextMessage(BaseModel):
    messaging_product: str = "whatsapp"
    recipient_type: str = "individual"
    to: str
    type: str = "text"
    text: Dict[str, str]


class WhatsAppApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
