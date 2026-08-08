"""
Meta WhatsApp Cloud API Webhook & Outbound Payload Schemas.
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, ConfigDict


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

    model_config = ConfigDict(populate_by_name=True)


class WhatsAppStatusError(BaseModel):
    code: int
    title: str
    message: Optional[str] = None
    error_data: Optional[Dict[str, Any]] = None


class WhatsAppMessageStatus(BaseModel):
    id: str
    status: str  # sent, delivered, read, failed
    timestamp: str
    recipient_id: str
    errors: Optional[List[WhatsAppStatusError]] = None


class WhatsAppValue(BaseModel):
    messaging_product: str
    metadata: Optional[Dict[str, Any]] = None
    contacts: Optional[List[WhatsAppContact]] = None
    messages: Optional[List[WhatsAppIncomingMessage]] = None
    statuses: Optional[List[WhatsAppMessageStatus]] = None


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
    text: Dict[str, Any]


class WhatsAppTestSendRequest(BaseModel):
    recipient_phone: str = Field(..., description="Recipient phone number with country code (e.g., 919876543210)")
    message: Optional[str] = Field(None, description="Custom message text to send")
    template_name: Optional[str] = Field(None, description="Meta WhatsApp template name (if sending template)")


class WhatsAppApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
