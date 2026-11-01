"""Schemas for the persistent booking-draft / cart API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.booking_draft import DRAFT_STATUSES


class DraftCredential(BaseModel):
    draft_id: UUID
    draft_token: str = Field(..., min_length=8, max_length=200)


class BookingDraftUpsert(BaseModel):
    draft_id: Optional[UUID] = None
    draft_token: Optional[str] = Field(default=None, max_length=200)
    guest_id: Optional[str] = Field(default=None, max_length=64)
    client_key: str = Field(..., min_length=1, max_length=80)
    service_key: str = Field(..., min_length=1, max_length=60)
    service_name: str = Field(..., min_length=1, max_length=150)
    current_step: int = Field(default=1, ge=0, le=200)
    step_key: Optional[str] = Field(default=None, max_length=80)
    completed_steps: List[str] = Field(default_factory=list, max_length=200)
    form_data: Dict[str, Any] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)
    resume_url: Optional[str] = Field(default=None, max_length=2048)
    status: Optional[str] = None
    booking_ref: Optional[str] = Field(default=None, max_length=40)

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        clean = value.strip().lower()
        if clean not in DRAFT_STATUSES:
            raise ValueError(f"status must be one of {', '.join(DRAFT_STATUSES)}")
        return clean


class BookingDraftLookup(BaseModel):
    drafts: List[DraftCredential] = Field(default_factory=list, max_length=100)


class BookingDraftClaim(BaseModel):
    drafts: List[DraftCredential] = Field(default_factory=list, max_length=100)


class BookingDraftComplete(BaseModel):
    booking_ref: Optional[str] = Field(default=None, max_length=40)


class BookingDraftResponse(BaseModel):
    draft_id: UUID
    draft_ref: str
    service_key: str
    service_name: str
    current_step: int
    step_key: Optional[str] = None
    completed_steps: List[str] = Field(default_factory=list)
    form_data: Dict[str, Any] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)
    resume_url: Optional[str] = None
    status: str
    booking_ref: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_saved_at: datetime
    expires_at: Optional[datetime] = None


class BookingDraftCreateResponse(BookingDraftResponse):
    access_token: str
    guest_id: Optional[str] = None


class BookingDraftListResponse(BaseModel):
    drafts: List[BookingDraftResponse] = Field(default_factory=list)


class BookingDraftClaimResponse(BaseModel):
    claimed: int
    drafts: List[BookingDraftResponse] = Field(default_factory=list)
