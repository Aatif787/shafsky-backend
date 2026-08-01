"""
Pydantic Request/Response Schemas for Phase B.5 — Shared Domain Services.
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# Assignment Schemas
# ─────────────────────────────────────────────

class AssignmentCreate(BaseModel):
    entity_type: str = Field(..., description="Polymorphic entity type (BOOKING, CASE, etc.)")
    entity_id: str = Field(..., description="Entity UUID as string")
    staff_id: UUID = Field(..., description="Staff user UUID")
    role_type: str = Field(default="GENERAL", description="Assignment role type")
    notes: Optional[str] = None

class ReassignRequest(BaseModel):
    new_staff_id: UUID = Field(..., description="New staff UUID to reassign to")
    reason: Optional[str] = None

class AssignmentResponse(BaseModel):
    id: UUID
    entity_type: str
    entity_id: str
    staff_id: UUID
    assigned_by: Optional[str] = None
    role_type: str
    status: str
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class AssignmentHistoryResponse(BaseModel):
    id: UUID
    assignment_id: UUID
    action: str
    from_status: Optional[str] = None
    to_status: str
    actor_id: Optional[str] = None
    reason: Optional[str] = None
    metadata_json: Dict[str, Any] = {}
    created_at: datetime

    class Config:
        from_attributes = True

class WorkloadResponse(BaseModel):
    staff_id: UUID
    active_count: int
    assignments: List[AssignmentResponse] = []


# ─────────────────────────────────────────────
# Timeline Schemas
# ─────────────────────────────────────────────

class TimelineCommentCreate(BaseModel):
    content: str = Field(..., min_length=1, description="Comment text")

class TimelineEntryResponse(BaseModel):
    id: UUID
    entity_type: str
    entity_id: str
    event_type: str
    title: str
    details: Dict[str, Any] = {}
    actor_id: Optional[str] = None
    actor_role: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class PaginatedTimelineResponse(BaseModel):
    success: bool = True
    total: int
    limit: int
    offset: int
    data: List[TimelineEntryResponse] = []


# ─────────────────────────────────────────────
# Notes Schemas
# ─────────────────────────────────────────────

class NoteCreate(BaseModel):
    entity_type: str = Field(..., description="Polymorphic entity type")
    entity_id: str = Field(..., description="Entity UUID as string")
    content: str = Field(..., min_length=1, description="Note content")
    visibility: str = Field(default="INTERNAL", description="INTERNAL or CUSTOMER")
    mentions: List[str] = Field(default_factory=list, description="Mentioned user IDs")

class NoteUpdate(BaseModel):
    content: str = Field(..., min_length=1, description="Updated note content")
    mentions: List[str] = Field(default_factory=list, description="Updated mentions")

class NoteResponse(BaseModel):
    id: UUID
    entity_type: str
    entity_id: str
    content: str
    visibility: str
    author_id: Optional[str] = None
    mentions: list = []
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class NoteRevisionResponse(BaseModel):
    id: UUID
    note_id: UUID
    content_snapshot: str
    edited_by: Optional[str] = None
    revision_number: int
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────────
# Attachment Schemas
# ─────────────────────────────────────────────

class AttachmentRegister(BaseModel):
    entity_type: str = Field(..., description="Polymorphic entity type")
    entity_id: str = Field(..., description="Entity UUID as string")
    filename: str = Field(..., description="Original filename")
    storage_path: str = Field(..., description="Cloud storage path or URL")
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    category: str = Field(default="GENERAL", description="File category (DOCUMENT, IMAGE, etc.)")
    access_level: str = Field(default="STAFF", description="PUBLIC, CUSTOMER, STAFF, ADMIN")

class AttachmentResponse(BaseModel):
    id: UUID
    entity_type: str
    entity_id: str
    filename: str
    storage_path: str
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    category: str
    uploaded_by: Optional[str] = None
    access_level: str
    is_deleted: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────────
# SLA Schemas
# ─────────────────────────────────────────────

class SLADefinitionCreate(BaseModel):
    service_type: str = Field(..., description="Service type (AIRPORT, HOTEL, etc.)")
    priority: str = Field(default="NORMAL", description="Priority level")
    response_time_minutes: int = Field(default=60, ge=1)
    resolution_time_minutes: int = Field(default=480, ge=1)
    escalation_rules: Dict[str, Any] = Field(default_factory=dict)

class SLADefinitionResponse(BaseModel):
    id: UUID
    service_type: str
    priority: str
    response_time_minutes: int
    resolution_time_minutes: int
    escalation_rules: Dict[str, Any] = {}
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

class SLAStartRequest(BaseModel):
    entity_type: str = Field(..., description="Polymorphic entity type")
    entity_id: str = Field(..., description="Entity UUID as string")
    service_type: str = Field(..., description="Service type to match SLA definition")
    priority: str = Field(default="NORMAL", description="Priority level")

class SLAResolveRequest(BaseModel):
    pass

class SLAInstanceResponse(BaseModel):
    id: UUID
    sla_definition_id: UUID
    entity_type: str
    entity_id: str
    status: str
    started_at: datetime
    deadline_at: datetime
    responded_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    escalated_at: Optional[datetime] = None
    breached_at: Optional[datetime] = None
    started_by: Optional[str] = None
    resolved_by: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class SLAOverdueResponse(BaseModel):
    success: bool = True
    total: int
    data: List[SLAInstanceResponse] = []


# ─────────────────────────────────────────────
# Search Schemas
# ─────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Search query text")
    entity_types: List[str] = Field(default_factory=list, description="Filter by entity types")
    filters: Dict[str, Any] = Field(default_factory=dict, description="Additional filters")
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    sort_by: str = Field(default="created_at", description="Sort field")
    sort_order: str = Field(default="desc", description="asc or desc")

class SearchResultItem(BaseModel):
    source: str
    entity_type: str
    entity_id: str
    match_field: str
    snippet: str
    relevance_score: float = 0.0
    created_at: Optional[datetime] = None

class SearchResponse(BaseModel):
    success: bool = True
    query: str
    total: int
    limit: int
    offset: int
    results: List[SearchResultItem] = []
