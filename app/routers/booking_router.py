from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.booking import (
    BookingCreate,
    BookingApiResponse,
    BookingStatusUpdate
)
from app.services.booking_service import BookingService
from app.security.dependencies import (
    get_optional_user,
    get_required_user,
    get_required_admin
)

router = APIRouter(prefix="/api/bookings", tags=["Booking Engine"])

@router.post("", response_model=BookingApiResponse, status_code=201)
@router.post("/", response_model=BookingApiResponse, status_code=201)
async def create_booking(
    payload: BookingCreate,
    db: Session = Depends(get_db),
    user_context: Optional[Dict[str, Any]] = Depends(get_optional_user)
):
    profile_id = None
    if user_context:
        raw_id = user_context.get("user_id") or user_context.get("userId")
        if raw_id:
            try:
                import uuid
                profile_id = uuid.UUID(str(raw_id))
            except Exception:
                pass

    booking = BookingService.create_booking(db, payload, profile_id=profile_id)
    return BookingApiResponse(
        success=True,
        data=BookingService.format_booking_dict(booking)
    )

@router.get("/my-bookings", response_model=BookingApiResponse)
async def get_my_bookings(
    db: Session = Depends(get_db),
    user_context: Dict[str, Any] = Depends(get_required_user)
):
    email = user_context.get("sub") or user_context.get("email") or ""
    bookings = BookingService.get_user_bookings(db, email=email)
    formatted = [BookingService.format_booking_dict(b) for b in bookings]
    return BookingApiResponse(
        success=True,
        data=formatted
    )

@router.get("/admin/list", response_model=BookingApiResponse)
@router.get("/admin/all", response_model=BookingApiResponse)
async def admin_list_bookings(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    bookings = BookingService.admin_list_bookings(db, status=status, search=search)
    formatted = [BookingService.format_booking_dict(b) for b in bookings]
    return BookingApiResponse(
        success=True,
        data=formatted
    )

@router.get("/{identifier}", response_model=BookingApiResponse)
async def get_booking_details(
    identifier: str,
    db: Session = Depends(get_db),
    user_context: Dict[str, Any] = Depends(get_required_user)
):
    booking = BookingService.get_booking_by_ref_or_id(db, identifier)
    role = user_context.get("role")
    email = (user_context.get("sub") or user_context.get("email") or "").lower()
    user_id = str(user_context.get("user_id") or user_context.get("userId") or "")
    is_staff = role in (
        "SUPER_ADMIN", "ADMIN", "OPERATIONS_MANAGER", "DUTY_OFFICER",
        "DISPATCHER", "CONCIERGE_TEAM", "CUSTOMER_SUPPORT",
    )
    owns = False
    if email and (booking.passenger_email or "").lower() == email:
        owns = True
    if user_id and booking.user_id and str(booking.user_id) == user_id:
        owns = True
    if not is_staff and not owns:
        raise HTTPException(status_code=403, detail="Access denied.")
    return BookingApiResponse(
        success=True,
        data=BookingService.format_booking_dict(booking)
    )

@router.patch("/{identifier}/cancel", response_model=BookingApiResponse)
async def cancel_booking(
    identifier: str,
    version: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    user_context: Dict[str, Any] = Depends(get_required_user)
):
    email = user_context.get("sub") or user_context.get("email") or ""
    is_admin = user_context.get("role") in ["ADMIN", "SUPER_ADMIN", "DISPATCHER"]
    
    updated_booking = BookingService.cancel_booking(
        db,
        identifier,
        requester_email=email,
        is_admin=is_admin,
        expected_version=version
    )
    return BookingApiResponse(
        success=True,
        data=BookingService.format_booking_dict(updated_booking)
    )

@router.patch("/admin/{identifier}/status", response_model=BookingApiResponse)
async def admin_update_booking_status(
    identifier: str,
    payload: BookingStatusUpdate,
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    updated_booking = BookingService.admin_update_status(
        db,
        identifier,
        new_status_str=payload.status,
        expected_version=payload.version
    )
    return BookingApiResponse(
        success=True,
        data=BookingService.format_booking_dict(updated_booking)
    )

@router.post("/estimate-price", response_model=BookingApiResponse)
async def estimate_booking_price(
    payload: Dict[str, Any],
    db: Session = Depends(get_db)
):
    package_id = str(payload.get("package_id", payload.get("service_type", "silver"))).lower()
    airport_code = str(payload.get("airport_code", payload.get("origin_code", "DEL"))).upper()
    journey_type = str(payload.get("journey_type", "DEPARTURE")).upper()
    pax_adults = max(1, int(payload.get("pax_adults", 1)))

    total_calculated = BookingService.calculate_authoritative_price(
        db=db,
        airport_code=airport_code,
        service_tier_or_slug=package_id,
        journey_type=journey_type,
        flight_type="DOMESTIC",
        pax_count=pax_adults
    )

    base_price = round(total_calculated / pax_adults, 2)
    subtotal = total_calculated
    taxes = int(subtotal * 0.18)
    total_amount = subtotal + taxes

    return BookingApiResponse(
        success=True,
        data={
            "base_price": base_price,
            "passengers_total": subtotal,
            "add_ons_total": 0,
            "subtotal": subtotal,
            "taxes": taxes,
            "total_amount": total_amount,
            "currency": "INR"
        }
    )
