"""
Operations & Communication Engine — Pydantic Schemas (Phase 6).
"""

from typing import List, Optional, Any, Dict
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


class OperationsQueueItemResponse(BaseModel):
    id: UUID
    booking_reference: str
    airport_code: str
    journey_type: str
    service_date: str
    service_time: str
    status: str
    assigned_staff_id: Optional[UUID] = None
    assigned_staff_name: Optional[str] = None
    customer_name: str
    customer_phone: str
    customer_email: str
    guest_count: int
    flight_number: Optional[str] = None
    selected_services: List[Any] = []
    special_requests: Optional[str] = None
    email_notification_sent: bool = False
    whatsapp_notification_sent: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class OperationsQueueListResponse(BaseModel):
    success: bool = True
    total: int
    data: List[OperationsQueueItemResponse] = []


class StatusUpdateRequest(BaseModel):
    status: str = Field(..., description="NEW, ASSIGNED, IN_PROGRESS, CUSTOMER_CONTACTED, READY, COMPLETED, CANCELLED")
    reason: Optional[str] = Field(None, description="Optional transition reason or staff comment")
    actor_id: Optional[str] = Field("SYSTEM", description="ID or name of staff member updating status")


class AssignStaffRequest(BaseModel):
    staff_id: Optional[UUID] = Field(None, description="Duty officer UUID for manual assignment; omit for auto-assign")
    staff_name: Optional[str] = Field(None, description="Duty officer display name")
    assigned_by: Optional[str] = Field("SYSTEM", description="Assigner staff name")


class InternalNoteCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, description="Staff-only internal note text")
    author_id: Optional[str] = Field("SYSTEM", description="Author staff ID or name")


class InternalNoteResponse(BaseModel):
    id: UUID
    entity_type: str
    entity_id: str
    content: str
    visibility: str = "INTERNAL"
    author_id: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationDispatchResponse(BaseModel):
    success: bool = True
    email_sent: bool = False
    whatsapp_sent: bool = False
    details: Dict[str, Any] = {}
