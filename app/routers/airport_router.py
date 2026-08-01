"""
FastAPI Router for Airport Meet & Assist Module — Phase C.1.
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
from app.schemas.airport import (
    AirportBookingCreate,
    AirportBookingUpdate,
    AirportBookingResponse,
    PaginatedAirportBookingResponse,
    AirportTransitionRequest,
    AssignStaffRequest,
    RegisterAttachmentRequest,
)
from app.services.airport_service import AirportService
from app.services.assignment_service import AssignmentService
from app.services.attachment_service import AttachmentService
from app.services.timeline_service import TimelineService

router = APIRouter(prefix="/api/airport", tags=["Airport Meet & Assist"])


@router.post(
    "/bookings",
    response_model=AirportBookingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Airport Booking",
    description="Creates an Airport Meet & Assist booking, validates flight details, auto-initializes Workflow Instance, and records Timeline activity."
)
def create_airport_booking_endpoint(
    data: AirportBookingCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    customer_id = current_user.get("sub") or current_user.get("email") or "CUSTOMER"
    try:
        booking = AirportService.create_booking(
            db,
            customer_id=customer_id,
            service_package=data.service_package,
            passengers_data=[p.model_dump() for p in data.passengers],
            flight_detail_data=data.flight_detail.model_dump(),
            addons_data=[a.model_dump() for a in data.addons],
            special_instructions=data.special_instructions,
            actor_id=customer_id
        )
        return booking
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@router.get(
    "/bookings",
    response_model=PaginatedAirportBookingResponse,
    status_code=status.HTTP_200_OK,
    summary="List Airport Bookings",
    description="Retrieves paginated list of airport bookings with optional filters for customer ID and booking status."
)
def list_airport_bookings_endpoint(
    customer_id: Optional[str] = Query(None, description="Filter by customer ID"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by booking status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user = Depends(get_required_staff_or_admin)
):
    res = AirportService.list_bookings(db, customer_id=customer_id, status=status_filter, limit=limit, offset=offset)
    return PaginatedAirportBookingResponse(
        success=True,
        total=res["total"],
        limit=res["limit"],
        offset=res["offset"],
        data=res["data"]
    )


@router.get(
    "/bookings/me",
    response_model=PaginatedAirportBookingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get My Airport Bookings",
    description="Retrieves current logged-in customer's airport bookings."
)
def get_my_airport_bookings_endpoint(
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    customer_id = getattr(current_user, "id", None) or getattr(current_user, "user_id", None)
    customer_email = getattr(current_user, "email", None) or getattr(current_user, "sub", None)
    
    res = AirportService.list_bookings(
        db,
        customer_id=str(customer_id) if customer_id else customer_email,
        status=None,
        limit=100,
        offset=0
    )
    return PaginatedAirportBookingResponse(
        success=True,
        total=res["total"],
        limit=res["limit"],
        offset=res["offset"],
        data=res["data"]
    )


@router.get(
    "/bookings/{booking_id}",
    response_model=AirportBookingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Airport Booking Details",
    description="Retrieves airport booking details aggregated with workflow state, staff assignments, attachments, and flight details."
)
def get_airport_booking_endpoint(
    booking_id: UUID,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    try:
        details = AirportService.get_booking_details(db, booking_id)
        booking = details["booking"]
        # Attach aggregated fields
        booking.workflow_state = details["workflow_state"]
        booking.assignments = details["assignments"]
        booking.attachments = details["attachments"]
        return booking
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


@router.put(
    "/bookings/{booking_id}",
    response_model=AirportBookingResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Airport Booking",
    description="Updates airport booking package or instructions."
)
def update_airport_booking_endpoint(
    booking_id: UUID,
    data: AirportBookingUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    actor_id = current_user.get("sub") or current_user.get("email") or "USER"
    try:
        return AirportService.update_booking(
            db,
            booking_id=booking_id,
            service_package=data.service_package,
            special_instructions=data.special_instructions,
            actor_id=actor_id
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


@router.post(
    "/bookings/{booking_id}/transition",
    response_model=AirportBookingResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Workflow Transition",
    description="Executes a workflow transition for an airport booking via WorkflowEngine and syncs booking status."
)
def execute_transition_endpoint(
    booking_id: UUID,
    data: AirportTransitionRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    actor_id = current_user.get("sub") or current_user.get("email") or "USER"
    actor_role = current_user.get("role", "CUSTOMER")
    try:
        return AirportService.execute_transition(
            db,
            booking_id=booking_id,
            action=data.action,
            actor_id=actor_id,
            actor_role=actor_role,
            payload=data.payload
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@router.post(
    "/bookings/{booking_id}/cancel",
    response_model=AirportBookingResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel Airport Booking",
    description="Cancels an airport booking and terminates its associated workflow instance."
)
def cancel_airport_booking_endpoint(
    booking_id: UUID,
    reason: Optional[str] = Query(None, description="Optional cancellation reason"),
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    actor_id = current_user.get("sub") or current_user.get("email") or "USER"
    try:
        return AirportService.cancel_booking(db, booking_id=booking_id, actor_id=actor_id, reason=reason)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err


@router.post(
    "/bookings/{booking_id}/assign",
    status_code=status.HTTP_201_CREATED,
    summary="Assign Staff to Booking",
    description="Assigns staff to an airport booking using Phase B.5 AssignmentService."
)
def assign_staff_endpoint(
    booking_id: UUID,
    data: AssignStaffRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_staff_or_admin)
):
    actor_id = current_user.get("sub") or current_user.get("email") or "STAFF"
    try:
        assignment = AssignmentService.assign(
            db,
            entity_type="AIRPORT_BOOKING",
            entity_id=str(booking_id),
            staff_id=data.staff_id,
            assigned_by=actor_id,
            role_type=data.role_type,
            notes=data.notes
        )
        return {"success": True, "assignment_id": str(assignment.id), "status": assignment.status}
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@router.post(
    "/bookings/{booking_id}/attachments",
    status_code=status.HTTP_201_CREATED,
    summary="Register Attachment Reference",
    description="Registers attachment metadata for passport, visa, or ticket using Phase B.5 AttachmentService."
)
def register_attachment_endpoint(
    booking_id: UUID,
    data: RegisterAttachmentRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    uploader = current_user.get("sub") or current_user.get("email") or "USER"
    try:
        att = AttachmentService.register(
            db,
            entity_type="AIRPORT_BOOKING",
            entity_id=str(booking_id),
            filename=data.filename,
            storage_path=data.storage_path,
            category=data.category,
            uploaded_by=uploader,
            access_level=data.access_level
        )
        return {"success": True, "attachment_id": str(att.id), "filename": att.filename}
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


@router.get(
    "/bookings/{booking_id}/timeline",
    status_code=status.HTTP_200_OK,
    summary="Get Booking Activity Timeline",
    description="Retrieves chronological activity feed for an airport booking using Phase B.5 TimelineService."
)
def get_booking_timeline_endpoint(
    booking_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    res = TimelineService.get_timeline(db, entity_type="AIRPORT_BOOKING", entity_id=str(booking_id), limit=limit, offset=offset)
    return {"success": True, "data": res["data"], "total": res["total"]}
