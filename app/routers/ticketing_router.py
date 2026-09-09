"""
FastAPI Router for Air Ticketing Domain Foundation.
Exposes REST APIs for Ticket Bookings, Passenger Roster, State Transitions, and Search.
Secured with role-based access control, ownership verification, and PII masking.
"""

import uuid
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, Query, status, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.ticketing import (
    AirTicketBookingCreateRequest,
    AirTicketBookingResponse,
    AirTicketPassengerCreate,
    AirTicketPassengerResponse,
    AirTicketTransitionRequest,
    AirTicketApiResponse,
)
from app.services.ticketing_service import TicketingService
from app.services.auth_service import AuthService
from app.security.dependencies import (
    get_optional_user,
    get_required_user,
    get_required_staff_or_admin,
    STAFF_OR_ADMIN_ROLES,
)

router = APIRouter(prefix="/api/ticketing", tags=["Air Ticketing Engine"])


def _mask_pii(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    val_str = str(val).strip()
    if len(val_str) <= 4:
        return "****"
    return val_str[:2] + ("*" * (len(val_str) - 4)) + val_str[-2:]


def _is_staff_or_admin(user: Dict[str, Any]) -> bool:
    return user.get("role") in STAFF_OR_ADMIN_ROLES


def _check_booking_access(booking: Any, current_user: Dict[str, Any]) -> None:
    if _is_staff_or_admin(current_user):
        return
    user_id = current_user.get("user_id")
    user_email = (current_user.get("sub") or current_user.get("email") or "").lower()
    b_cust_id = str(booking.customer_id) if booking.customer_id else None
    b_email = (booking.contact_email or "").lower()

    if (user_id and b_cust_id == user_id) or (user_email and b_email == user_email):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access denied. You do not have permission to access this booking."
    )


def _to_booking_response(b, mask_pii: bool = False) -> dict:
    return {
        "id": str(b.id),
        "booking_ref": b.booking_ref,
        "pnr_code": b.pnr_code,
        "customer_id": str(b.customer_id) if b.customer_id else None,
        "contact_name": b.contact_name,
        "contact_email": b.contact_email,
        "contact_phone": b.contact_phone,
        "airline_name": b.airline_name,
        "flight_number": b.flight_number,
        "cabin_class": b.cabin_class,
        "origin_iata": b.origin_iata,
        "destination_iata": b.destination_iata,
        "departure_time": b.departure_time.isoformat() if b.departure_time else None,
        "arrival_time": b.arrival_time.isoformat() if b.arrival_time else None,
        "passenger_count": b.passenger_count,
        "base_fare": float(b.base_fare),
        "taxes_amount": float(b.taxes_amount),
        "total_fare": float(b.total_fare),
        "currency": b.currency,
        "status": b.status.value if hasattr(b.status, "value") else str(b.status),
        "notes": b.notes,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
        "passengers": [
            {
                "id": str(p.id),
                "ticket_booking_id": str(p.ticket_booking_id),
                "passenger_type": p.passenger_type.value if hasattr(p.passenger_type, "value") else str(p.passenger_type),
                "title": p.title,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "dob": p.dob,
                "gender": p.gender,
                "nationality": p.nationality,
                "passport_number": _mask_pii(p.passport_number) if mask_pii else p.passport_number,
                "e_ticket_number": p.e_ticket_number,
                "seat_number": p.seat_number,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in (b.passengers or [])
        ],
    }


@router.post("/bookings", response_model=AirTicketApiResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket_booking(
    payload: AirTicketBookingCreateRequest,
    current_user: Optional[Dict[str, Any]] = Depends(get_optional_user),
    db: Session = Depends(get_db)
):
    cust_id = None
    if current_user and current_user.get("user_id"):
        try:
            cust_id = uuid.UUID(current_user["user_id"])
        except ValueError:
            pass

    booking = TicketingService.create_booking(db, payload, customer_id=cust_id)
    return AirTicketApiResponse(success=True, data=_to_booking_response(booking))


@router.get("/bookings", response_model=AirTicketApiResponse)
async def list_ticket_bookings(
    search: Optional[str] = Query(None, description="Search by ref, pnr, passenger, email"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(get_required_staff_or_admin),
    db: Session = Depends(get_db)
):
    """Staff/Admin only: List all air ticket bookings."""
    bookings = TicketingService.list_bookings(db, search=search, limit=limit, offset=offset)
    data = [_to_booking_response(b, mask_pii=False) for b in bookings]
    return AirTicketApiResponse(success=True, data=data)


@router.get("/my-bookings", response_model=AirTicketApiResponse)
async def list_my_ticket_bookings(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    """Customer: List bookings owned by the authenticated user."""
    user_id_str = current_user.get("user_id")
    user_email = (current_user.get("sub") or current_user.get("email") or "").strip()
    u_uuid = None
    if user_id_str:
        try:
            u_uuid = uuid.UUID(user_id_str)
        except ValueError:
            pass

    bookings = TicketingService.list_bookings(
        db,
        customer_id=u_uuid,
        customer_email=user_email,
        limit=limit,
        offset=offset
    )
    data = [_to_booking_response(b, mask_pii=False) for b in bookings]
    return AirTicketApiResponse(success=True, data=data)


@router.get("/bookings/{booking_id}", response_model=AirTicketApiResponse)
async def get_ticket_booking_details(
    booking_id: str,
    current_user: Dict[str, Any] = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    """Retrieve ticket booking details. Requires ownership or staff/admin role."""
    try:
        b_uuid = uuid.UUID(booking_id)
    except ValueError:
        return AirTicketApiResponse(success=False, error="Invalid booking ID UUID format.")

    booking = TicketingService.get_booking(db, b_uuid)
    _check_booking_access(booking, current_user)

    is_staff = _is_staff_or_admin(current_user)
    return AirTicketApiResponse(success=True, data=_to_booking_response(booking, mask_pii=not is_staff))


@router.post("/bookings/{booking_id}/passengers", response_model=AirTicketApiResponse)
async def add_passenger_to_booking(
    booking_id: str,
    payload: AirTicketPassengerCreate,
    current_user: Dict[str, Any] = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    """Add a passenger to a ticket booking. Requires ownership or staff/admin role."""
    try:
        b_uuid = uuid.UUID(booking_id)
    except ValueError:
        return AirTicketApiResponse(success=False, error="Invalid booking ID UUID format.")

    booking = TicketingService.get_booking(db, b_uuid)
    _check_booking_access(booking, current_user)

    passenger = TicketingService.add_passenger(db, b_uuid, payload)
    return AirTicketApiResponse(
        success=True,
        data={
            "id": str(passenger.id),
            "ticket_booking_id": str(passenger.ticket_booking_id),
            "first_name": passenger.first_name,
            "last_name": passenger.last_name,
            "seat_number": passenger.seat_number,
        }
    )


@router.post("/bookings/{booking_id}/transition", response_model=AirTicketApiResponse)
async def transition_ticket_booking_state(
    booking_id: str,
    payload: AirTicketTransitionRequest,
    current_user: Dict[str, Any] = Depends(get_required_staff_or_admin),
    db: Session = Depends(get_db)
):
    """Transition ticket booking lifecycle state. Staff or Admin privileges required."""
    try:
        b_uuid = uuid.UUID(booking_id)
    except ValueError:
        return AirTicketApiResponse(success=False, error="Invalid booking ID UUID format.")

    booking = TicketingService.transition_booking(db, b_uuid, payload)
    return AirTicketApiResponse(success=True, data=_to_booking_response(booking, mask_pii=False))
