"""
FastAPI Router for Phase B.5 — Shared Domain Services.

Provides generic REST APIs for Assignment, Timeline, Notes,
Attachment, SLA, and Search services.
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.security.dependencies import (
    get_required_user,
    get_required_staff_or_admin,
    get_required_admin,
)
from app.services.assignment_service import AssignmentService
from app.services.timeline_service import TimelineService
from app.services.notes_service import NotesService
from app.services.attachment_service import AttachmentService
from app.services.sla_service import SLAService
from app.services.search_service import SearchService
from app.schemas.shared_domain import (
    AssignmentCreate,
    AssignmentResponse,
    AssignmentHistoryResponse,
    ReassignRequest,
    WorkloadResponse,
    TimelineCommentCreate,
    TimelineEntryResponse,
    PaginatedTimelineResponse,
    NoteCreate,
    NoteUpdate,
    NoteResponse,
    NoteRevisionResponse,
    AttachmentRegister,
    AttachmentResponse,
    SLADefinitionCreate,
    SLADefinitionResponse,
    SLAStartRequest,
    SLAInstanceResponse,
    SLAOverdueResponse,
    SLAResolveRequest,
    SearchRequest,
    SearchResponse,
)

router = APIRouter(prefix="/api/shared", tags=["Shared Domain Services"])


# ─────────────────────────────────────────────
# Assignment Endpoints
# ─────────────────────────────────────────────

@router.post(
    "/assignments",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign Staff",
)
def assign_staff(
    data: AssignmentCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_staff_or_admin),
):
    """Assign staff member to an entity."""
    actor_id = current_user.get("sub") or current_user.get("email") or "SYSTEM"
    try:
        return AssignmentService.assign(
            db,
            entity_type=data.entity_type,
            entity_id=data.entity_id,
            staff_id=data.staff_id,
            assigned_by=actor_id,
            role_type=data.role_type,
            notes=data.notes,
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@router.post(
    "/assignments/{assignment_id}/reassign",
    response_model=AssignmentResponse,
    summary="Reassign Staff",
)
def reassign_staff(
    assignment_id: UUID,
    data: ReassignRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_staff_or_admin),
):
    """Reassign an existing assignment to a different staff member."""
    actor_id = current_user.get("sub") or current_user.get("email") or "SYSTEM"
    try:
        return AssignmentService.reassign(
            db,
            assignment_id=assignment_id,
            new_staff_id=data.new_staff_id,
            reassigned_by=actor_id,
            reason=data.reason,
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@router.post(
    "/assignments/{assignment_id}/release",
    response_model=AssignmentResponse,
    summary="Release Assignment",
)
def release_assignment(
    assignment_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_staff_or_admin),
):
    """Release a staff assignment."""
    actor_id = current_user.get("sub") or current_user.get("email") or "SYSTEM"
    try:
        return AssignmentService.release(db, assignment_id=assignment_id, released_by=actor_id)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@router.post(
    "/assignments/{assignment_id}/complete",
    response_model=AssignmentResponse,
    summary="Complete Assignment",
)
def complete_assignment(
    assignment_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_staff_or_admin),
):
    """Mark a staff assignment as completed."""
    actor_id = current_user.get("sub") or current_user.get("email") or "SYSTEM"
    try:
        return AssignmentService.complete(db, assignment_id=assignment_id, completed_by=actor_id)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@router.get(
    "/assignments/entity/{entity_type}/{entity_id}",
    response_model=List[AssignmentResponse],
    summary="Get Entity Assignments",
)
def get_entity_assignments(
    entity_type: str,
    entity_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_user),
):
    """Retrieve all assignments for an entity."""
    return AssignmentService.get_entity_assignments(db, entity_type, entity_id)


@router.get(
    "/assignments/workload/{staff_id}",
    response_model=WorkloadResponse,
    summary="Get Staff Workload",
)
def get_staff_workload(
    staff_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_admin),
):
    """Get active assignment count and list for a staff member."""
    return AssignmentService.get_workload(db, staff_id)


@router.get(
    "/assignments/{assignment_id}/history",
    response_model=List[AssignmentHistoryResponse],
    summary="Get Assignment History",
)
def get_assignment_history(
    assignment_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_user),
):
    """Retrieve immutable assignment change log."""
    return AssignmentService.get_history(db, assignment_id)


# ─────────────────────────────────────────────
# Timeline Endpoints
# ─────────────────────────────────────────────

@router.get(
    "/timeline/{entity_type}/{entity_id}",
    response_model=PaginatedTimelineResponse,
    summary="Get Entity Timeline",
)
def get_timeline(
    entity_type: str,
    entity_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    current_user=Depends(get_required_user),
):
    """Retrieve paginated chronological timeline for an entity."""
    result = TimelineService.get_timeline(db, entity_type, entity_id, limit, offset, sort)
    return PaginatedTimelineResponse(
        success=True,
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
        data=result["data"],
    )


@router.post(
    "/timeline/{entity_type}/{entity_id}/comment",
    response_model=TimelineEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add Timeline Comment",
)
def add_timeline_comment(
    entity_type: str,
    entity_id: str,
    data: TimelineCommentCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_user),
):
    """Add a comment to an entity's timeline."""
    actor_id = current_user.get("sub") or current_user.get("email") or "SYSTEM"
    actor_role = current_user.get("role", "CUSTOMER")
    return TimelineService.add_comment(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        content=data.content,
        actor_role=actor_role,
    )


# ─────────────────────────────────────────────
# Notes Endpoints
# ─────────────────────────────────────────────

@router.post(
    "/notes",
    response_model=NoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Note",
)
def create_note(
    data: NoteCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_user),
):
    """Create a new note for an entity."""
    author_id = current_user.get("sub") or current_user.get("email") or "SYSTEM"
    return NotesService.create(
        db,
        entity_type=data.entity_type,
        entity_id=data.entity_id,
        content=data.content,
        visibility=data.visibility,
        author_id=author_id,
        mentions=data.mentions,
    )


@router.put(
    "/notes/{note_id}",
    response_model=NoteResponse,
    summary="Update Note",
)
def update_note(
    note_id: UUID,
    data: NoteUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_user),
):
    """Update note content. Creates an immutable revision snapshot."""
    editor_id = current_user.get("sub") or current_user.get("email") or "SYSTEM"
    try:
        return NotesService.update(db, note_id=note_id, content=data.content, editor_id=editor_id, mentions=data.mentions)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


@router.delete(
    "/notes/{note_id}",
    response_model=NoteResponse,
    summary="Delete Note",
)
def delete_note(
    note_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_staff_or_admin),
):
    """Soft-delete a note."""
    actor_id = current_user.get("sub") or current_user.get("email") or "SYSTEM"
    try:
        return NotesService.delete(db, note_id=note_id, deleted_by=actor_id)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


@router.get(
    "/notes/entity/{entity_type}/{entity_id}",
    response_model=List[NoteResponse],
    summary="Get Entity Notes",
)
def get_entity_notes(
    entity_type: str,
    entity_id: str,
    visibility: Optional[str] = Query(None, description="Filter by INTERNAL or CUSTOMER"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_required_user),
):
    """Retrieve paginated notes for an entity with optional visibility filter."""
    result = NotesService.get_notes(db, entity_type, entity_id, visibility, limit, offset)
    return result["data"]


@router.get(
    "/notes/{note_id}/revisions",
    response_model=List[NoteRevisionResponse],
    summary="Get Note Revisions",
)
def get_note_revisions(
    note_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_staff_or_admin),
):
    """Retrieve immutable edit history for a note."""
    return NotesService.get_revisions(db, note_id)


# ─────────────────────────────────────────────
# Attachment Endpoints
# ─────────────────────────────────────────────

@router.post(
    "/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register Attachment",
)
def register_attachment(
    data: AttachmentRegister,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_staff_or_admin),
):
    """Register attachment metadata for an entity."""
    uploader = current_user.get("sub") or current_user.get("email") or "SYSTEM"
    return AttachmentService.register(
        db,
        entity_type=data.entity_type,
        entity_id=data.entity_id,
        filename=data.filename,
        storage_path=data.storage_path,
        file_size=data.file_size,
        mime_type=data.mime_type,
        category=data.category,
        uploaded_by=uploader,
        access_level=data.access_level,
    )


@router.get(
    "/attachments/entity/{entity_type}/{entity_id}",
    response_model=List[AttachmentResponse],
    summary="Get Entity Attachments",
)
def get_entity_attachments(
    entity_type: str,
    entity_id: str,
    category: Optional[str] = Query(None, description="Filter by category"),
    db: Session = Depends(get_db),
    current_user=Depends(get_required_user),
):
    """Retrieve attachments for an entity."""
    return AttachmentService.get_attachments(db, entity_type, entity_id, category)


@router.delete(
    "/attachments/{attachment_id}",
    response_model=AttachmentResponse,
    summary="Delete Attachment",
)
def delete_attachment(
    attachment_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_staff_or_admin),
):
    """Soft-delete an attachment record."""
    actor_id = current_user.get("sub") or current_user.get("email") or "SYSTEM"
    try:
        return AttachmentService.delete(db, attachment_id=attachment_id, deleted_by=actor_id)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


# ─────────────────────────────────────────────
# SLA Endpoints
# ─────────────────────────────────────────────

@router.post(
    "/sla/definitions",
    response_model=SLADefinitionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create SLA Definition",
)
def create_sla_definition(
    data: SLADefinitionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_admin),
):
    """Create or update an SLA definition for a service type + priority."""
    return SLAService.create_definition(
        db,
        service_type=data.service_type,
        priority=data.priority,
        response_time_minutes=data.response_time_minutes,
        resolution_time_minutes=data.resolution_time_minutes,
        escalation_rules=data.escalation_rules,
    )


@router.post(
    "/sla/start",
    response_model=SLAInstanceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start SLA Tracking",
)
def start_sla(
    data: SLAStartRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_staff_or_admin),
):
    """Start SLA tracking for an entity."""
    actor_id = current_user.get("sub") or current_user.get("email") or "SYSTEM"
    try:
        return SLAService.start_sla(
            db,
            entity_type=data.entity_type,
            entity_id=data.entity_id,
            service_type=data.service_type,
            priority=data.priority,
            started_by=actor_id,
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@router.get(
    "/sla/entity/{entity_type}/{entity_id}",
    response_model=SLAInstanceResponse,
    summary="Get Entity SLA",
)
def get_entity_sla(
    entity_type: str,
    entity_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_user),
):
    """Retrieve the most recent SLA instance for an entity."""
    sla = SLAService.get_entity_sla(db, entity_type, entity_id)
    if not sla:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No SLA instance found for this entity.")
    return sla


@router.get(
    "/sla/overdue",
    response_model=SLAOverdueResponse,
    summary="Get Overdue SLAs",
)
def get_overdue_slas(
    service_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_required_admin),
):
    """Retrieve all overdue SLA instances."""
    overdue = SLAService.get_overdue(db, service_type)
    return SLAOverdueResponse(success=True, total=len(overdue), data=overdue)


@router.post(
    "/sla/{sla_instance_id}/resolve",
    response_model=SLAInstanceResponse,
    summary="Resolve SLA",
)
def resolve_sla(
    sla_instance_id: UUID,
    data: SLAResolveRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_staff_or_admin),
):
    """Resolve an active SLA instance."""
    actor_id = current_user.get("sub") or current_user.get("email") or "SYSTEM"
    try:
        return SLAService.resolve_sla(db, sla_instance_id=sla_instance_id, resolved_by=actor_id)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


# ─────────────────────────────────────────────
# Search Endpoint
# ─────────────────────────────────────────────

@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Global Search",
)
def global_search(
    data: SearchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_required_user),
):
    """Global multi-entity search across all shared domain tables."""
    result = SearchService.search(
        db,
        query=data.query,
        entity_types=data.entity_types,
        filters=data.filters,
        limit=data.limit,
        offset=data.offset,
        sort_by=data.sort_by,
        sort_order=data.sort_order,
    )
    return SearchResponse(
        success=True,
        query=data.query,
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
        results=result["results"],
    )
