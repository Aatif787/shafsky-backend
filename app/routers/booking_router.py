from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any

from app.database import get_db
from app.schemas.booking import (
    BookingCreate,
    BookingApiResponse,
    BookingStatusUpdate,
    BookingAssign
)
from app.services.booking_service import BookingService
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/bookings", tags=["Booking Engine"])

def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[Dict[str, Any]]:
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        try:
            return AuthService.decode_access_token(token)
        except Exception:
            pass
    return None

def get_required_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization token.")
    token = authorization.split(" ")[1]
    try:
        return AuthService.decode_access_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Token expired or invalid.")

def get_required_admin(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    user = get_required_user(authorization)
    if user.get("role") not in ["ADMIN", "SUPER_ADMIN", "DISPATCHER"]:
        raise HTTPException(status_code=403, detail="Access denied. Insufficient administrative privileges.")
    return user

@router.post("", response_model=BookingApiResponse)
@router.post("/", response_model=BookingApiResponse)
async def create_booking(
    payload: BookingCreate,
    db: Session = Depends(get_db),
    user_context: Optional[Dict[str, Any]] = Depends(get_optional_user)
):
    profile_id = None
    if user_context and user_context.get("userId"):
        try:
            import uuid
            profile_id = uuid.UUID(user_context.get("userId"))
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
    email = user_context.get("email", "")
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
    admin_context: Dict[str, Any] = Depends(get_required_admin)
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
    user_context: Optional[Dict[str, Any]] = Depends(get_optional_user)
):
    booking = BookingService.get_booking_by_ref_or_id(db, identifier)
    return BookingApiResponse(
        success=True,
        data=BookingService.format_booking_dict(booking)
    )

@router.patch("/{identifier}/cancel", response_model=BookingApiResponse)
async def cancel_booking(
    identifier: str,
    db: Session = Depends(get_db),
    user_context: Dict[str, Any] = Depends(get_required_user)
):
    email = user_context.get("email", "")
    is_admin = user_context.get("role") in ["ADMIN", "SUPER_ADMIN", "DISPATCHER"]
    
    updated_booking = BookingService.cancel_booking(db, identifier, requester_email=email, is_admin=is_admin)
    return BookingApiResponse(
        success=True,
        data=BookingService.format_booking_dict(updated_booking)
    )

@router.patch("/admin/{identifier}/status", response_model=BookingApiResponse)
async def admin_update_booking_status(
    identifier: str,
    payload: BookingStatusUpdate,
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    updated_booking = BookingService.admin_update_status(db, identifier, payload.status)
    return BookingApiResponse(
        success=True,
        data=BookingService.format_booking_dict(updated_booking)
    )
