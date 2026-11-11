"""Request and response schemas for Super Admin WhatsApp notification routing.

Called by app/services/notification_routing_service.py and
app/routers/notification_routing_router.py. contact_settings.py is the public
website phone list and does not cover these tables.

Phone numbers are normalized with the same rules dispatch already uses.
Service keys are exact backend service_category values and are not merged.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.notification_routing import ROUTING_MODE_DATABASE, ROUTING_MODE_ENV

# Exact service_category values used by bookings, enquiries, or the WhatsApp
# menu. Similar names stay separate. There is no Railway service in the backend.
NOTIFICATION_SERVICE_KEYS: tuple[str, ...] = (
    "Airport Assistance",
    "Airport Services",
    "Meet & Greet",
    "Lounge",
    "Travel Support",
    "Travel Services",
    "Hotel",
    "Hotels",
    "Hotel Services",
    "Hotel Booking",
    "Hotel & Transportation",
    "Private Charter",
    "Charter",
    "Ground Transport",
    "Transport",
    "Transportation Services",
    "Ticketing",
    "Round Trip",
    "Cargo & Logistics",
    "Cargo",
    "Medical Assistance",
    "Medical Services",
)

NOTIFICATION_SERVICE_KEY_SET = frozenset(NOTIFICATION_SERVICE_KEYS)


def normalize_recipient_phone(value: str) -> str:
    """Return digits-only international form, or raise ValueError."""
    from app.services.whatsapp_department_formatting import WhatsAppDepartmentFormattingMixin

    normalized = WhatsAppDepartmentFormattingMixin.normalize_phone_number(value)
    if not normalized:
        raise ValueError("phone_number must be a valid international number.")
    if normalized.startswith("91") and len(normalized) == 12 and normalized[2] not in "6789":
        raise ValueError("Indian mobile numbers must start with 6, 7, 8, or 9.")
    return normalized


class RecipientCreate(BaseModel):
    label: Optional[str] = Field(default=None, max_length=120)
    phone_number: str = Field(min_length=1, max_length=32)
    is_active: bool = True

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @field_validator("phone_number")
    @classmethod
    def clean_phone(cls, value: str) -> str:
        return normalize_recipient_phone(value)


class RecipientUpdate(BaseModel):
    label: Optional[str] = Field(default=None, max_length=120)
    phone_number: Optional[str] = Field(default=None, max_length=32)
    is_active: Optional[bool] = None

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @field_validator("phone_number")
    @classmethod
    def clean_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_recipient_phone(value)

    @model_validator(mode="after")
    def require_a_change(self) -> "RecipientUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one of label, phone_number, or is_active is required.")
        return self


class RecipientResponse(BaseModel):
    id: uuid.UUID
    label: Optional[str] = None
    phone_number: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RecipientEnvelope(BaseModel):
    success: bool = True
    data: RecipientResponse


class RecipientListResponse(BaseModel):
    success: bool = True
    data: List[RecipientResponse]


class RecipientDeleteResult(BaseModel):
    deleted: bool
    recipient_id: uuid.UUID


class RecipientDeleteResponse(BaseModel):
    success: bool = True
    data: RecipientDeleteResult


class ServiceRouteAssignmentInput(BaseModel):
    recipient_id: uuid.UUID
    is_active: bool = True


class ServiceRouteUpdateRequest(BaseModel):
    assignments: List[ServiceRouteAssignmentInput] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_recipients(self) -> "ServiceRouteUpdateRequest":
        ids = [item.recipient_id for item in self.assignments]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate recipient in this service assignment.")
        return self


class RouteAssignmentResponse(BaseModel):
    id: uuid.UUID
    service_key: str
    recipient_id: uuid.UUID
    recipient_label: Optional[str] = None
    phone_number: str
    is_active: bool
    recipient_is_active: bool
    effective: bool


class ServiceRoutingResponse(BaseModel):
    service_key: str
    assignments: List[RouteAssignmentResponse]
    effective_recipients: List[RecipientResponse]


class ServiceRoutingEnvelope(BaseModel):
    success: bool = True
    data: ServiceRoutingResponse


class ServiceRoutingListResponse(BaseModel):
    success: bool = True
    data: List[ServiceRoutingResponse]


class ServiceKeyItem(BaseModel):
    service_key: str


class ServiceKeyListResponse(BaseModel):
    success: bool = True
    data: List[ServiceKeyItem]


class RecipientServicesUpdate(BaseModel):
    service_keys: List[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_services(self) -> "RecipientServicesUpdate":
        if len(self.service_keys) != len(set(self.service_keys)):
            raise ValueError("Duplicate service in this recipient assignment.")
        return self


class RecipientServicesResponse(BaseModel):
    recipient_id: uuid.UUID
    service_keys: List[str]


class RecipientServicesEnvelope(BaseModel):
    success: bool = True
    data: RecipientServicesResponse


class AdoptEnvRoutingResponse(BaseModel):
    routing_mode: str
    adopted: bool
    recipients_created: int
    routes_created: int


class AdoptEnvRoutingEnvelope(BaseModel):
    success: bool = True
    data: AdoptEnvRoutingResponse


class LiveRecipient(BaseModel):
    label: str
    phone_number: str


class RoutingSettingsResponse(BaseModel):
    routing_mode: str
    settings_id: uuid.UUID
    live_recipients: List[LiveRecipient] = []


class RoutingSettingsEnvelope(BaseModel):
    success: bool = True
    data: RoutingSettingsResponse


class RoutingSettingsUpdate(BaseModel):
    routing_mode: str

    @field_validator("routing_mode")
    @classmethod
    def allowed_mode(cls, value: str) -> str:
        mode = (value or "").strip().lower()
        if mode not in {ROUTING_MODE_ENV, ROUTING_MODE_DATABASE}:
            raise ValueError("routing_mode must be env or database.")
        return mode


class NotificationConfigResponse(BaseModel):
    routing_mode: str
    settings_id: uuid.UUID
    recipients: List[RecipientResponse]
    services: List[ServiceRoutingResponse]


class NotificationConfigEnvelope(BaseModel):
    success: bool = True
    data: NotificationConfigResponse


class EnvImportResponse(BaseModel):
    created: List[RecipientResponse]
    skipped_existing: List[str]
    skipped_invalid: List[str]
    skipped_empty: List[str]
    routes_created: int = 0
    routing_mode: str


class EnvImportEnvelope(BaseModel):
    success: bool = True
    data: EnvImportResponse
