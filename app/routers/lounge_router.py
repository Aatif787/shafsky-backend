import logging
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.lounge import (
    LoungeEnquiryCreate,
    LoungeEnquiryApiResponse,
    LoungeEnquiryListApiResponse,
    LoungeEnquiryAdminUpdate,
    LoungeOptionsApiResponse,
)
from app.services.lounge_service import LoungeService
from app.security.dependencies import get_required_admin, get_optional_user, STAFF_OR_ADMIN_ROLES

logger = logging.getLogger("shafsky.lounge_router")

router = APIRouter(tags=["Lounge Service Engine"])


def _format_enquiry_data(enquiry) -> Dict[str, Any]:
    return {
        "id": str(enquiry.id),
        "enquiry_reference": enquiry.enquiry_reference,
        "airport": enquiry.airport,
        "lounge_type": enquiry.lounge_type,
        "travel_date": enquiry.travel_date,
        "preferred_time": enquiry.preferred_time,
        "number_of_passengers": enquiry.number_of_passengers,
        "flight_number": enquiry.flight_number,
        "full_name": enquiry.full_name,
        "mobile_number": enquiry.mobile_number,
        "email_address": enquiry.email_address,
        "whatsapp_number": enquiry.whatsapp_number,
        "additional_requirements": enquiry.additional_requirements,
        "status": enquiry.status.value if hasattr(enquiry.status, "value") else str(enquiry.status),
        "assigned_staff_id": enquiry.assigned_staff_id,
        "assigned_staff_name": enquiry.assigned_staff_name,
        "internal_notes": enquiry.internal_notes,
        "created_at": enquiry.created_at.isoformat() if enquiry.created_at else "",
        "updated_at": enquiry.updated_at.isoformat() if enquiry.updated_at else "",
    }


# ─────────────────────────────────────────────────────────────
# 1. PUBLIC LOUNGE ENQUIRY ENDPOINTS (ENQUIRY ONLY, NO PAYMENT)
# ─────────────────────────────────────────────────────────────

@router.post(
    "/api/v1/lounges/enquiries",
    response_model=LoungeEnquiryApiResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit Lounge Enquiry",
    description="Submit an enquiry-only request for airport lounge access. Never initiates payment or confirmed booking.",
)
@router.post(
    "/api/lounges/enquiries",
    response_model=LoungeEnquiryApiResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def submit_lounge_enquiry_endpoint(
    payload: LoungeEnquiryCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else None
    enquiry, is_dup = LoungeService.create_enquiry(db, payload, client_ip=client_ip)

    msg = (
        "Lounge enquiry received. We have noted your request."
        if is_dup
        else "Lounge enquiry received successfully. Our concierge desk will contact you shortly with a quotation."
    )

    return LoungeEnquiryApiResponse(
        success=True,
        message=msg,
        data=_format_enquiry_data(enquiry),
    )


@router.get(
    "/api/v1/lounges/options",
    response_model=LoungeOptionsApiResponse,
    summary="Get Active Airport & Lounge Type Options",
    description="Retrieves active airports and verified lounge categories from verified database and system records.",
)
@router.get(
    "/api/lounges/options",
    response_model=LoungeOptionsApiResponse,
    include_in_schema=False,
)
async def get_lounge_options_endpoint(
    db: Session = Depends(get_db),
):
    options = LoungeService.get_options(db)
    return LoungeOptionsApiResponse(success=True, data=options)


@router.get(
    "/api/v1/lounges/enquiries/{reference}",
    response_model=Dict[str, Any],
    summary="Lookup Lounge Enquiry Reference",
    description="Reference lookup for submitted lounge enquiries with PII masking for public clients.",
)
@router.get(
    "/api/lounges/enquiries/{reference}",
    response_model=Dict[str, Any],
    include_in_schema=False,
)
async def lookup_lounge_enquiry_endpoint(
    reference: str,
    current_user: Optional[Dict[str, Any]] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    enquiry = LoungeService.get_by_reference(db, reference)
    if not enquiry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lounge enquiry not found.",
        )

    is_staff = bool(current_user and current_user.get("role") in STAFF_OR_ADMIN_ROLES)
    user_email = ((current_user.get("sub") or current_user.get("email") or "") if current_user else "").lower()
    is_owner = bool(user_email and enquiry.email_address and enquiry.email_address.lower() == user_email)
    show_full_pii = is_staff or is_owner

    masked_name = None
    if enquiry.full_name:
        parts = enquiry.full_name.strip().split()
        masked_name = " ".join([p[0] + "***" for p in parts]) if parts else "***"

    return {
        "success": True,
        "data": {
            "enquiry_reference": enquiry.enquiry_reference,
            "status": enquiry.status.value if hasattr(enquiry.status, "value") else str(enquiry.status),
            "airport": enquiry.airport,
            "lounge_type": enquiry.lounge_type,
            "travel_date": enquiry.travel_date,
            "preferred_time": enquiry.preferred_time,
            "number_of_passengers": enquiry.number_of_passengers,
            "flight_number": enquiry.flight_number,
            "full_name": enquiry.full_name if show_full_pii else masked_name,
            "email_address": enquiry.email_address if show_full_pii else "***@***",
            "created_at": enquiry.created_at.isoformat() if enquiry.created_at else "",
        },
    }


# ─────────────────────────────────────────────────────────────
# 2. ADMIN PORTAL LOUNGE ENQUIRY MANAGEMENT (ROLE-BASED AUTH)
# ─────────────────────────────────────────────────────────────

@router.get(
    "/api/v1/admin/lounges/enquiries",
    response_model=LoungeEnquiryListApiResponse,
    summary="List Lounge Enquiries (Admin)",
    description="Authorized administrators can list, filter by status or airport, and paginate lounge enquiries.",
)
@router.get(
    "/api/admin/lounges/enquiries",
    response_model=LoungeEnquiryListApiResponse,
    include_in_schema=False,
)
async def admin_list_lounge_enquiries_endpoint(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, CONTACTED, etc."),
    airport: Optional[str] = Query(None, description="Filter by airport code or name"),
    search: Optional[str] = Query(None, description="Search term for passenger name, email, ref, or flight"),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_required_admin),
):
    items, total = LoungeService.list_enquiries(
        db, status=status, airport=airport, search=search, skip=skip, limit=limit
    )

    formatted_items = [_format_enquiry_data(item) for item in items]
    return LoungeEnquiryListApiResponse(
        success=True,
        data={
            "items": formatted_items,
            "total": total,
            "skip": skip,
            "limit": limit,
        },
    )


@router.get(
    "/api/v1/admin/lounges/enquiries/{enquiry_id}",
    response_model=Dict[str, Any],
    summary="Get Lounge Enquiry Details (Admin)",
    description="Authorized administrators can view complete details of a specific lounge enquiry.",
)
@router.get(
    "/api/admin/lounges/enquiries/{enquiry_id}",
    response_model=Dict[str, Any],
    include_in_schema=False,
)
async def admin_get_lounge_enquiry_endpoint(
    enquiry_id: str,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_required_admin),
):
    enquiry = LoungeService.get_by_reference(db, enquiry_id)
    if not enquiry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lounge enquiry not found.",
        )

    return {
        "success": True,
        "data": _format_enquiry_data(enquiry),
    }


@router.patch(
    "/api/v1/admin/lounges/enquiries/{enquiry_id}",
    response_model=Dict[str, Any],
    summary="Update Lounge Enquiry Status, Assignment, and Notes (Admin)",
    description="Authorized administrators can update lifecycle status, assign staff, and record internal notes.",
)
@router.patch(
    "/api/admin/lounges/enquiries/{enquiry_id}",
    response_model=Dict[str, Any],
    include_in_schema=False,
)
async def admin_update_lounge_enquiry_endpoint(
    enquiry_id: str,
    payload: LoungeEnquiryAdminUpdate,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_required_admin),
):
    updated = LoungeService.update_enquiry(db, enquiry_id, payload)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lounge enquiry not found.",
        )

    return {
        "success": True,
        "message": "Lounge enquiry updated successfully.",
        "data": _format_enquiry_data(updated),
    }
