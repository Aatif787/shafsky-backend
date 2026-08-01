"""
FastAPI Router for Air Ticketing Domain Foundation.
Exposes REST APIs for Ticket Bookings, Passenger Roster, State Transitions, and Search.
"""

import uuid
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, Query, status, Header
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
from app.security.dependencies import get_required_admin, get_required_user

router = APIRouter(prefix="/api/ticketing", tags=["Air Ticketing Engine"])


def _to_booking_response(b) -> dict:
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
                "passport_number": p.passport_number,
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
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    cust_id = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        try:
            decoded = AuthService.decode_access_token(token)
            uid = decoded.get("user_id")
            if uid:
                cust_id = uuid.UUID(uid)
        except Exception:
            pass

    booking = TicketingService.create_booking(db, payload, customer_id=cust_id)
    return AirTicketApiResponse(success=True, data=_to_booking_response(booking))


@router.get("/bookings", response_model=AirTicketApiResponse)
async def list_ticket_bookings(
    search: Optional[str] = Query(None, description="Search by ref, pnr, passenger, email"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    bookings = TicketingService.list_bookings(db, search=search, limit=limit, offset=offset)
    data = [_to_booking_response(b) for b in bookings]
    return AirTicketApiResponse(success=True, data=data)


@router.get("/bookings/{booking_id}", response_model=AirTicketApiResponse)
async def get_ticket_booking_details(
    booking_id: str,
    db: Session = Depends(get_db)
):
    try:
        b_uuid = uuid.UUID(booking_id)
    except ValueError:
        return AirTicketApiResponse(success=False, error="Invalid booking ID UUID format.")

    booking = TicketingService.get_booking(db, b_uuid)
    return AirTicketApiResponse(success=True, data=_to_booking_response(booking))


@router.post("/bookings/{booking_id}/passengers", response_model=AirTicketApiResponse)
async def add_passenger_to_booking(
    booking_id: str,
    payload: AirTicketPassengerCreate,
    db: Session = Depends(get_db)
):
    try:
        b_uuid = uuid.UUID(booking_id)
    except ValueError:
        return AirTicketApiResponse(success=False, error="Invalid booking ID UUID format.")

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
    db: Session = Depends(get_db)
):
    try:
        b_uuid = uuid.UUID(booking_id)
    except ValueError:
        return AirTicketApiResponse(success=False, error="Invalid booking ID UUID format.")

    booking = TicketingService.transition_booking(db, b_uuid, payload)
    return AirTicketApiResponse(success=True, data=_to_booking_response(booking))
