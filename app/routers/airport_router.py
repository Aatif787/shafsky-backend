"""
FastAPI Router for Airport Meet & Assist Module — Phase C.1.
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Body, status
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
from app.services.service_config_service import ServiceConfigService
from app.services.assignment_service import AssignmentService
from app.services.attachment_service import AttachmentService
from app.services.timeline_service import TimelineService

router = APIRouter(prefix="/api/airport", tags=["Airport Meet & Assist"])


@router.get(
    "/services",
    status_code=status.HTTP_200_OK,
    summary="Get Airport Services Catalog",
    description="Returns dynamic database and master catalog-driven packages, individual services, pricing, and currency for a specified airport, journey type, and flight type."
)
def get_airport_services_catalog_endpoint(
    airport: str = Query(..., description="IATA airport code, e.g. BOM"),
    service_type: Optional[str] = Query(None, alias="journey_type", description="Service/journey type: arrival, departure, transit"),
    flight_type: Optional[str] = Query(None, description="Flight type: domestic, international"),
    origin: Optional[str] = Query(None, description="Origin airport IATA code"),
    destination: Optional[str] = Query(None, description="Destination airport IATA code"),
    terminal: Optional[str] = Query(None, description="Terminal name or code"),
    db: Session = Depends(get_db)
):
    return ServiceConfigService.resolve_catalog_services(
        db=db,
        airport_code=airport,
        journey_type=service_type or "arrival",
        flight_type=flight_type,
        terminal=terminal,
        origin_code=origin,
        dest_code=destination
    )


@router.post(
    "/calculate-price",
    status_code=status.HTTP_200_OK,
    summary="Calculate Authoritative Booking Price",
    description="Authoritative backend price calculation engine. Recalculates package and individual service totals, ignoring overlapping services included in selected package to prevent double charging."
)
def calculate_authoritative_price_endpoint(payload: Dict[str, Any] = Body(...)):
    airport_code = (payload.get("airport_code") or "DEL").strip().upper()
    selected_package_id = payload.get("selected_package_id")
    selected_service_ids = payload.get("selected_service_ids") or []
    guest_count = max(1, int(payload.get("guest_count") or 1))

    config = ServiceConfigService.get_airport_configuration(airport_code)
    packages = config.get("packages", [])
    individual_services = config.get("individualServices", [])

    total_base_price = 0.0
    package_detail = None
    included_service_ids = set()

    if selected_package_id:
        pkg_match = next((p for p in packages if p["id"] == selected_package_id), None)
        if pkg_match:
            total_base_price += pkg_match["basePrice"]
            included_service_ids = set(pkg_match.get("serviceIds", []))
            package_detail = {
                "id": pkg_match["id"],
                "title": pkg_match["title"],
                "price": pkg_match["basePrice"],
                "currency": pkg_match.get("currency", "INR")
            }

    additional_services_detail = []
    for svc_id in selected_service_ids:
        if svc_id in included_service_ids:
            continue
        
        svc_match = next((s for s in individual_services if s["id"] == svc_id), None)
        if svc_match and svc_match.get("isAvailable", True):
            total_base_price += svc_match["price"]
            additional_services_detail.append({
                "id": svc_match["id"],
                "title": svc_match["title"],
                "price": svc_match["price"],
                "currency": svc_match.get("currency", "INR")
            })

    total_price = total_base_price * guest_count

    return {
        "success": True,
        "airport_code": airport_code,
        "currency": config.get("currency", "INR"),
        "guest_count": guest_count,
        "unit_total": total_base_price,
        "total_price": total_price,
        "package": package_detail,
        "additional_services": additional_services_detail,
        "overlapping_service_ids_ignored": [s for s in selected_service_ids if s in included_service_ids]
    }


@router.post(
    "/bookings/validate",
    status_code=status.HTTP_200_OK,
    summary="Validate Booking & Calculate Authoritative Price",
    description="Authoritative backend booking validation and price calculation API. Revalidates flight, journey type, airport coverage, package/service availability, time restrictions, duplicate services, and calculates final authoritative DB pricing."
)
@router.post(
    "/validate-booking",
    status_code=status.HTTP_200_OK,
    summary="Validate Booking & Calculate Authoritative Price (Alias)",
    description="Authoritative backend booking validation and price calculation API alias."
)
def validate_authoritative_booking_endpoint(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db)
):
    return ServiceConfigService.validate_authoritative_booking(db, payload)


@router.post(
    "/bookings/draft",
    status_code=status.HTTP_200_OK,
    summary="Save / Update Booking Draft with Passenger Details",
    description="Validates passenger fields (name, email, phone, guest count) and booking context, recalculates database total, and persists or updates booking draft record."
)
@router.post(
    "/draft",
    status_code=status.HTTP_200_OK,
    summary="Save / Update Booking Draft (Alias)",
    description="Alias endpoint for saving or updating a booking draft."
)
@router.post(
    "/save-draft",
    status_code=status.HTTP_200_OK,
    summary="Save / Update Booking Draft (Alias 2)",
    description="Alias endpoint for saving or updating a booking draft."
)
def save_booking_draft_endpoint(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db)
):
    return ServiceConfigService.save_booking_draft(db, payload)


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
