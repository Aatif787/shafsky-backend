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
    transit: Optional[str] = Query(None, description="Transit airport IATA code"),
    terminal: Optional[str] = Query(None, description="Terminal name or code"),
    db: Session = Depends(get_db)
):
    airport_code = airport
    if (service_type or "arrival").lower() in ("transit", "connection") and transit:
        airport_code = transit
    return ServiceConfigService.resolve_catalog_services(
        db=db,
        airport_code=airport_code,
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
def calculate_authoritative_price_endpoint(payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    airport_code = (payload.get("airport_code") or "").strip().upper()
    selected_package_id = payload.get("selected_package_id")
    selected_service_ids = payload.get("selected_service_ids") or []
    guest_count = max(1, int(payload.get("guest_count") or 1))

    config = ServiceConfigService.get_airport_configuration(
        airport_code,
        db=db,
        journey_type=payload.get("journey_type") or payload.get("service_type"),
        flight_type=payload.get("flight_type"),
    )
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
    # Legacy booking validation endpoint is deprecated in favor of the stepwise
    # `/flow/*` booking flow. Return 410 Gone to avoid duplicate booking flows
    # while providing guidance to clients.
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error": "Deprecated",
            "message": "This endpoint is deprecated. Use the stepwise booking flow under /api/airport/flow/* (init, flight-info, select-service, customer-details).",
            "migration_docs": "/docs#tag/Airport+Meet+%26+Assist"
        }
    )


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
    # Deprecated: save/update booking draft via legacy endpoints is removed.
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error": "Deprecated",
            "message": "Draft booking endpoints are deprecated. Please use the new /api/airport/flow/* endpoints and server-side draft management will be handled by the new flow.",
            "migration_docs": "/docs#tag/Airport+Meet+%26+Assist"
        }
    )


@router.post(
    "/bookings",
    response_model=AirportBookingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Airport Booking",
    description="Creates an Airport Meet & Assist booking, validates flight details, auto-initializes Workflow Instance, and records Timeline activity."
)
@router.post(
    "/flow/init",
    status_code=status.HTTP_200_OK,
    summary="Initialize Airport Service Booking Flow",
    description="Step 1: Select airport. Returns allowed service types for the airport."
)
def flow_init_endpoint(
    airport_code: str = Body(..., embed=True, description="IATA airport code e.g. DEL"),
    db: Session = Depends(get_db)
):
    code = (airport_code or "").strip().upper()
    if not code or len(code) != 3:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid airport code")

    config = ServiceConfigService.get_airport_configuration(code)
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Airport not supported")

    allowed = ["ARRIVAL", "DEPARTURE", "TRANSIT"]
    return {"success": True, "airport_code": code, "allowed_service_types": allowed, "airport": config.get("airport")}


@router.post(
    "/flow/flight-info",
    status_code=status.HTTP_200_OK,
    summary="Fetch Flight Information For Selected Airport and Service Type",
    description="Step 2-4: Accepts airport, service type, flight number and date; validates and returns flight info scoped to the selected airport."
)
def flow_flight_info_endpoint(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db)
):
    airport = (payload.get("airport_code") or "").strip().upper()
    service_type = (payload.get("service_type") or "").strip().upper()
    flight_number = (payload.get("flight_number") or "").strip().upper()
    date_str = payload.get("date")

    if not airport or len(airport) != 3:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or missing airport_code")
    if service_type not in {"ARRIVAL", "DEPARTURE", "TRANSIT"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid service_type")
    if not flight_number:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="flight_number is required")
    if not date_str:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="date is required")

    # Minimal scoped flight info: ensure the selected airport remains authoritative
    flight_info = {
        "flight_number": flight_number,
        "scheduled_date": date_str,
        "service_type": service_type,
        "selected_airport": airport,
        "status": "PENDING_VERIFICATION"
    }

    services = ServiceConfigService.resolve_catalog_services(
        db=db,
        airport_code=airport,
        journey_type=service_type.lower(),
        flight_type=None
    )

    return {"success": True, "flight_info": flight_info, "available_services": services}


@router.post(
    "/flow/select-service",
    status_code=status.HTTP_200_OK,
    summary="Select Airport Service for Booking",
    description="Step 6: Select a single airport service available for the chosen airport and service type."
)
def flow_select_service_endpoint(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    airport = (payload.get("airport_code") or "").strip().upper()
    service_type = (payload.get("service_type") or "").strip().upper()
    selected_service_id = payload.get("selected_service_id")

    if not selected_service_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="selected_service_id required")

    services = ServiceConfigService.resolve_catalog_services(db=db, airport_code=airport, journey_type=service_type.lower())
    valid_ids = set()
    for p in services.get("packages", []) or []:
        valid_ids.add(p.get("id"))
    for s in services.get("individualServices", []) or []:
        valid_ids.add(s.get("id"))

    if selected_service_id not in valid_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected service is not available for the chosen airport/service type")

    return {"success": True, "selected_service_id": selected_service_id}


@router.post(
    "/flow/customer-details",
    status_code=status.HTTP_200_OK,
    summary="Collect Customer Details and Create Booking",
    description="Step 7/8: Accept minimal customer details, flight and service selection, then create the booking and return booking reference and payment info."
)
def flow_customer_details_endpoint(
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_required_user)
):
    airport = (payload.get("airport_code") or "").strip().upper()
    service_type = (payload.get("service_type") or "").strip().upper()
    flight_number = (payload.get("flight_number") or "").strip().upper()
    date_str = payload.get("date")
    selected_service_id = payload.get("selected_service_id")
    customer = payload.get("customer") or {}

    if not all([airport, service_type, flight_number, date_str, selected_service_id]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing required booking context")

    if not (customer.get("name") and (customer.get("email") or customer.get("phone"))):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Customer name and email/phone required")

    passengers = [{
        "full_name": customer.get("name"),
        "contact_email": customer.get("email"),
        "contact_phone": customer.get("phone"),
        "is_primary": True
    }]

    airline = None
    if "-" in flight_number:
        airline = flight_number.split("-")[0]
    else:
        airline = flight_number[:2]

    from datetime import datetime
    try:
        scheduled_time = datetime.fromisoformat(date_str)
    except Exception:
        scheduled_time = datetime.fromisoformat(f"{date_str}T00:00:00+00:00") if "T" not in date_str else datetime.fromisoformat(date_str)

    if service_type == "ARRIVAL":
        arrival_airport = airport
        departure_airport = payload.get("origin") or "UNK"
    elif service_type == "DEPARTURE":
        departure_airport = airport
        arrival_airport = payload.get("destination") or "UNK"
    else:
        departure_airport = payload.get("origin") or airport
        arrival_airport = payload.get("destination") or airport

    flight_detail = {
        "airline": airline or "UNKNOWN",
        "flight_number": flight_number,
        "departure_airport": departure_airport,
        "arrival_airport": arrival_airport,
        "terminal": payload.get("terminal"),
        "scheduled_time": scheduled_time,
        "flight_type": service_type
    }

    customer_id = current_user.get("sub") or current_user.get("email") or "CUSTOMER"
    try:
        booking = AirportService.create_booking(
            db,
            customer_id=customer_id,
            service_package=selected_service_id,
            passengers_data=passengers,
            flight_detail_data=flight_detail,
            addons_data=None,
            special_instructions=payload.get("special_instructions"),
            actor_id=customer_id
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err

    return {
        "success": True,
        "booking_id": str(booking.id),
        "booking_reference": booking.booking_reference,
        "total_price": float(booking.total_price),
        "currency": booking.currency,
        "payment_required": True,
        "payment_url": f"/payments/checkout?booking_ref={booking.booking_reference}&entity=airport"
    }


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
