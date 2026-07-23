from typing import Optional, Dict, Any
from pydantic import BaseModel, EmailStr, Field

class NotificationSendRequest(BaseModel):
    template_type: str = Field(..., min_length=2)
    recipient_email: Optional[EmailStr] = None
    recipient_phone: Optional[str] = None
    channel: Optional[str] = "ALL"  # EMAIL_ONLY, WHATSAPP_ONLY, SMS_ONLY, ALL
    payload: Dict[str, Any] = Field(default_factory=dict)

class NotificationApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
