from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.charter import (
    PrivateCharterRequestCreate,
    PrivateCharterRequestResponse,
    PrivateCharterAdminUpdate,
    PrivateCharterAdminListItem,
)
from app.services.charter_service import CharterService
from app.security.dependencies import get_required_admin, get_optional_user

router = APIRouter(tags=["Private Charter Engine"])


# ─────────────────────────────────────────────────────────────
# 1. PUBLIC CHARTER ENQUIRY ENDPOINTS (NO PAYMENT, 100% FREE)
# ─────────────────────────────────────────────────────────────

@router.post(
    "/api/v1/charter/requests",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Submit Private Charter Enquiry",
    description="Submit a tailored private aviation charter enquiry. Does not initiate payment or require flight validation.",
)
@router.post("/api/charter/requests", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_charter_request_endpoint(
    payload: PrivateCharterRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else None
    try:
        charter_req = CharterService.create_charter_request(db, payload, client_ip=client_ip)
        return {
            "success": True,
            "message": "Private charter request received successfully. Our charter flight desk will contact you shortly.",
            "data": {
                "request_reference": charter_req.request_reference,
                "status": charter_req.status.value,
                "customer_name": charter_req.customer_name,
                "origin": charter_req.origin,
                "destination": charter_req.destination,
                "departure_date": charter_req.departure_date,
                "departure_time": charter_req.departure_time,
                "return_date": charter_req.return_date,
                "passengers": charter_req.passengers,
                "aircraft_preference": charter_req.aircraft_preference,
                "travel_requirements": charter_req.travel_requirements,
                "created_at": charter_req.created_at.isoformat(),
            },
        }
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit charter request. Please try again later.",
        )


@router.get(
    "/api/v1/charter/requests/{reference}",
    response_model=Dict[str, Any],
    summary="Lookup Private Charter Request Status",
    description="Public reference lookup for submitted charter requests.",
)
@router.get("/api/charter/requests/{reference}", response_model=Dict[str, Any], include_in_schema=False)
async def get_charter_request_by_ref_endpoint(
    reference: str,
    db: Session = Depends(get_db),
):
    charter_req = CharterService.get_by_reference(db, reference)
    if not charter_req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Charter request not found.")

    return {
        "success": True,
        "data": {
            "request_reference": charter_req.request_reference,
            "status": charter_req.status.value,
            "customer_name": charter_req.customer_name,
            "trip_type": charter_req.trip_type,
            "origin": charter_req.origin,
            "destination": charter_req.destination,
            "departure_date": charter_req.departure_date,
            "departure_time": charter_req.departure_time,
            "return_date": charter_req.return_date,
            "itinerary": charter_req.itinerary,
            "passengers": charter_req.passengers,
            "aircraft_preference": charter_req.aircraft_preference,
            "travel_requirements": charter_req.travel_requirements,
            "created_at": charter_req.created_at.isoformat(),
        },
    }


# ─────────────────────────────────────────────────────────────
# 2. ADMIN CHARTER MANAGEMENT ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.get(
    "/api/v1/admin/charter/requests",
    response_model=Dict[str, Any],
    summary="List Charter Enquiries (Admin)",
    description="Lists all private charter requests with filtering and search.",
)
@router.get("/api/admin/charter/requests", response_model=Dict[str, Any], include_in_schema=False)
async def list_admin_charter_requests_endpoint(
    status: Optional[str] = Query(None, description="Filter by CharterRequestStatus"),
    search: Optional[str] = Query(None, description="Search term across name, ref, email, route"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin=Depends(get_required_admin),
):
    requests, total = CharterService.list_charter_requests(
        db, status=status, search=search, skip=skip, limit=limit
    )

    items = [
        {
            "id": str(r.id),
            "request_reference": r.request_reference,
            "customer_name": r.customer_name,
            "country_code": r.country_code,
            "phone": r.phone,
            "email": r.email,
            "company": r.company,
            "preferred_contact_method": r.preferred_contact_method,
            "trip_type": r.trip_type,
            "origin": r.origin,
            "destination": r.destination,
            "departure_date": r.departure_date,
            "departure_time": r.departure_time,
            "return_date": r.return_date,
            "return_time": r.return_time,
            "itinerary": r.itinerary,
            "passengers": r.passengers,
            "aircraft_preference": r.aircraft_preference,
            "travel_requirements": r.travel_requirements,
            "special_requests": r.special_requests,
            "status": r.status.value,
            "assigned_staff_id": r.assigned_staff_id,
            "assigned_staff_name": r.assigned_staff_name,
            "internal_notes": r.internal_notes,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in requests
    ]

    return {
        "success": True,
        "data": {
            "items": items,
            "total": total,
            "skip": skip,
            "limit": limit,
        },
    }


@router.get(
    "/api/v1/admin/charter/requests/{request_id}",
    response_model=Dict[str, Any],
    summary="Get Charter Request Details (Admin)",
)
@router.get("/api/admin/charter/requests/{request_id}", response_model=Dict[str, Any], include_in_schema=False)
async def get_admin_charter_request_endpoint(
    request_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(get_required_admin),
):
    charter_req = CharterService.get_by_id(db, request_id)
    if not charter_req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Charter request not found.")

    return {
        "success": True,
        "data": {
            "id": str(charter_req.id),
            "request_reference": charter_req.request_reference,
            "customer_name": charter_req.customer_name,
            "country_code": charter_req.country_code,
            "phone": charter_req.phone,
            "email": charter_req.email,
            "company": charter_req.company,
            "preferred_contact_method": charter_req.preferred_contact_method,
            "trip_type": charter_req.trip_type,
            "origin": charter_req.origin,
            "destination": charter_req.destination,
            "departure_date": charter_req.departure_date,
            "departure_time": charter_req.departure_time,
            "return_date": charter_req.return_date,
            "return_time": charter_req.return_time,
            "itinerary": charter_req.itinerary,
            "passengers": charter_req.passengers,
            "aircraft_preference": charter_req.aircraft_preference,
            "travel_requirements": charter_req.travel_requirements,
            "special_requests": charter_req.special_requests,
            "status": charter_req.status.value,
            "assigned_staff_id": charter_req.assigned_staff_id,
            "assigned_staff_name": charter_req.assigned_staff_name,
            "internal_notes": charter_req.internal_notes,
            "client_ip": charter_req.client_ip,
            "created_at": charter_req.created_at.isoformat(),
            "updated_at": charter_req.updated_at.isoformat(),
        },
    }


@router.patch(
    "/api/v1/admin/charter/requests/{request_id}",
    response_model=Dict[str, Any],
    summary="Update Charter Request Status / Assignment / Notes (Admin)",
)
@router.patch("/api/admin/charter/requests/{request_id}", response_model=Dict[str, Any], include_in_schema=False)
async def update_admin_charter_request_endpoint(
    request_id: str,
    payload: PrivateCharterAdminUpdate,
    db: Session = Depends(get_db),
    _admin=Depends(get_required_admin),
):
    charter_req = CharterService.update_charter_request(db, request_id, payload)
    if not charter_req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Charter request not found.")

    return {
        "success": True,
        "message": "Charter request updated successfully.",
        "data": {
            "id": str(charter_req.id),
            "request_reference": charter_req.request_reference,
            "status": charter_req.status.value,
            "assigned_staff_id": charter_req.assigned_staff_id,
            "assigned_staff_name": charter_req.assigned_staff_name,
            "internal_notes": charter_req.internal_notes,
            "updated_at": charter_req.updated_at.isoformat(),
        },
    }
