"""
Operations & Communication Engine — FastAPI Router (Phase 6).

Endpoints:
- GET  /api/operations/queue                  — List active operations queue
- GET  /api/operations/queue/{ref}             — Queue item details + timeline + notes
- POST /api/operations/queue/{ref}/status      — Transition workflow status (7 states)
- POST /api/operations/queue/{ref}/assign      — Execute auto or manual officer assignment
- POST /api/operations/queue/{ref}/notes       — Add staff-only internal note
- POST /api/operations/queue/{ref}/notify      — Re-trigger customer notifications (Email/WhatsApp)
"""

from typing import Optional, List, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.operations_models import OperationsQueue
from app.models.shared_domain import Note
from app.services.operations_engine import OperationsEngine
from app.services.timeline_service import TimelineService
from app.security.dependencies import get_required_staff_or_admin
from app.schemas.operations_schemas import (
    OperationsQueueItemResponse,
    OperationsQueueListResponse,
    StatusUpdateRequest,
    AssignStaffRequest,
    InternalNoteCreateRequest,
    InternalNoteResponse,
    NotificationDispatchResponse,
)

router = APIRouter(prefix="/api/operations", tags=["Operations & Communication Engine"])


@router.get(
    "/queue",
    response_model=OperationsQueueListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Operations Queue",
    description="Returns list of bookings in the operations queue with optional status or airport filters.",
)
def list_operations_queue(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status: NEW, ASSIGNED, IN_PROGRESS, READY, etc."),
    airport: Optional[str] = Query(None, description="Filter by 3-letter IATA code"),
    db: Session = Depends(get_db),
    _staff=Depends(get_required_staff_or_admin),
):
    query = db.query(OperationsQueue)
    if status_filter:
        query = query.filter(OperationsQueue.status == status_filter.strip().upper())
    if airport:
        query = query.filter(OperationsQueue.airport_code == airport.strip().upper())

    items = query.order_by(OperationsQueue.created_at.desc()).all()
    return OperationsQueueListResponse(
        success=True,
        total=len(items),
        data=[OperationsQueueItemResponse.model_validate(i) for i in items],
    )


@router.get(
    "/queue/{booking_reference}",
    status_code=status.HTTP_200_OK,
    summary="Get Operations Item Details",
    description="Returns detailed operations record, including timeline audit history and internal staff notes.",
)
def get_operations_item(
    booking_reference: str,
    db: Session = Depends(get_db),
    _staff=Depends(get_required_staff_or_admin),
):
    item = db.query(OperationsQueue).filter_by(booking_reference=booking_reference).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Operations queue item '{booking_reference}' not found.",
        )

    timeline = TimelineService.get_timeline(
        db=db,
        entity_type="OPERATIONS_BOOKING",
        entity_id=booking_reference,
        limit=50,
    )

    notes = (
        db.query(Note)
        .filter_by(entity_type="OPERATIONS_BOOKING", entity_id=booking_reference, is_deleted=False)
        .order_by(Note.created_at.desc())
        .all()
    )

    return {
        "success": True,
        "item": OperationsQueueItemResponse.model_validate(item),
        "timeline": timeline.get("items", []),
        "internal_notes": [
            {
                "id": str(n.id),
                "content": n.content,
                "author_id": n.author_id,
                "created_at": n.created_at.isoformat(),
            }
            for n in notes
        ],
    }


@router.post(
    "/queue/{booking_reference}/status",
    response_model=OperationsQueueItemResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Workflow Status",
    description="Transitions booking state (NEW, ASSIGNED, IN_PROGRESS, CUSTOMER_CONTACTED, READY, COMPLETED, CANCELLED).",
)
def update_workflow_status(
    booking_reference: str,
    payload: StatusUpdateRequest,
    db: Session = Depends(get_db),
    _staff=Depends(get_required_staff_or_admin),
):
    try:
        updated = OperationsEngine.update_status(
            db=db,
            booking_reference=booking_reference,
            new_status=payload.status,
            reason=payload.reason,
            actor_id=payload.actor_id or "STAFF",
        )
        return OperationsQueueItemResponse.model_validate(updated)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))


@router.post(
    "/queue/{booking_reference}/assign",
    response_model=OperationsQueueItemResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign Duty Officer",
    description="Executes automatic or manual duty officer assignment.",
)
def assign_duty_officer(
    booking_reference: str,
    payload: AssignStaffRequest,
    db: Session = Depends(get_db),
    _staff=Depends(get_required_staff_or_admin),
):
    item = db.query(OperationsQueue).filter_by(booking_reference=booking_reference).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Operations queue item '{booking_reference}' not found.",
        )

    if payload.staff_id and payload.staff_name:
        updated = OperationsEngine.assign_officer_manual(
            db=db,
            booking_reference=booking_reference,
            staff_id=payload.staff_id,
            staff_name=payload.staff_name,
            assigned_by=payload.assigned_by or "STAFF",
        )
    else:
        OperationsEngine.auto_assign_officer(db, item)
        db.commit()
        db.refresh(item)
        updated = item

    return OperationsQueueItemResponse.model_validate(updated)


@router.post(
    "/queue/{booking_reference}/notes",
    response_model=InternalNoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add Internal Staff Note",
    description="Creates a staff-only internal note for the booking.",
)
def add_internal_staff_note(
    booking_reference: str,
    payload: InternalNoteCreateRequest,
    db: Session = Depends(get_db),
    _staff=Depends(get_required_staff_or_admin),
):
    item = db.query(OperationsQueue).filter_by(booking_reference=booking_reference).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Operations queue item '{booking_reference}' not found.",
        )

    note = OperationsEngine.add_internal_note(
        db=db,
        booking_reference=booking_reference,
        content=payload.content,
        author_id=payload.author_id or "STAFF",
    )
    return InternalNoteResponse.model_validate(note)


@router.post(
    "/queue/{booking_reference}/notify",
    response_model=NotificationDispatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Re-trigger Customer Notifications",
    description="Dispatches Email & WhatsApp customer notifications non-blockingly.",
)
def trigger_notifications(
    booking_reference: str,
    db: Session = Depends(get_db),
    _staff=Depends(get_required_staff_or_admin),
):
    item = db.query(OperationsQueue).filter_by(booking_reference=booking_reference).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Operations queue item '{booking_reference}' not found.",
        )

    return OperationsEngine.dispatch_booking_notifications(db, item)
