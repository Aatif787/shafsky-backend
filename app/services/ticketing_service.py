"""
Business Service for Air Ticketing Domain Foundation.
Handles Booking Creation, Passenger Management, Workflow Transitions,
and Shared Domain Integrations (Timeline, Audit Logs, Staff Assignments).
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, desc
from fastapi import HTTPException, status

from app.models.ticketing import AirTicketBooking, AirTicketPassenger, AirTicketStatus, AirTicketPassengerType
from app.schemas.ticketing import AirTicketBookingCreateRequest, AirTicketPassengerCreate, AirTicketTransitionRequest
from app.services.timeline_service import TimelineService
from app.services.admin_service import AdminService
from app.workflow.engine import WorkflowEngine


class TicketingService:

    @classmethod
    def generate_booking_ref(cls) -> str:
        unique_suffix = uuid.uuid4().hex[:6].upper()
        return f"TKT-{unique_suffix}"

    @classmethod
    def create_booking(
        cls,
        db: Session,
        payload: AirTicketBookingCreateRequest,
        customer_id: Optional[uuid.UUID] = None
    ) -> AirTicketBooking:
        ref = cls.generate_booking_ref()

        base = float(payload.base_fare)
        taxes = float(payload.taxes_amount)
        total = base + taxes

        booking = AirTicketBooking(
            id=uuid.uuid4(),
            booking_ref=ref,
            customer_id=customer_id,
            contact_name=payload.contact_name,
            contact_email=payload.contact_email,
            contact_phone=payload.contact_phone,
            airline_name=payload.airline_name,
            flight_number=payload.flight_number,
            cabin_class=payload.cabin_class.upper(),
            origin_iata=payload.origin_iata.upper(),
            destination_iata=payload.destination_iata.upper(),
            departure_time=payload.departure_time,
            arrival_time=payload.arrival_time,
            passenger_count=len(payload.passengers) if payload.passengers else 1,
            base_fare=base,
            taxes_amount=taxes,
            total_fare=total,
            currency=payload.currency.upper(),
            status=AirTicketStatus.NEW_BOOKING,
            notes=payload.notes,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db.add(booking)
        db.flush()

        if payload.passengers:
            for p in payload.passengers:
                pass_rec = AirTicketPassenger(
                    id=uuid.uuid4(),
                    ticket_booking_id=booking.id,
                    passenger_type=p.passenger_type,
                    title=p.title.upper(),
                    first_name=p.first_name,
                    last_name=p.last_name,
                    dob=p.dob,
                    gender=p.gender,
                    nationality=p.nationality,
                    passport_number=p.passport_number,
                    e_ticket_number=p.e_ticket_number,
                    seat_number=p.seat_number,
                    created_at=datetime.now(timezone.utc)
                )
                db.add(pass_rec)

        db.commit()
        db.refresh(booking)

        # Timeline Integration
        TimelineService.add_entry(
            db,
            entity_type="TICKET_BOOKING",
            entity_id=str(booking.id),
            event_type="BOOKING_CREATED",
            title=f"Air Ticket Booking {ref} Created",
            details={
                "airline": booking.airline_name,
                "flightNumber": booking.flight_number,
                "route": f"{booking.origin_iata}-{booking.destination_iata}",
                "totalFare": booking.total_fare,
            }
        )

        # Audit Integration
        AdminService.log_audit_action(
            db,
            actor_email=payload.contact_email,
            action="TICKET_BOOKING_CREATED",
            resource_type="TICKET_BOOKING",
            resource_id=str(booking.id),
            details={"booking_ref": ref, "origin": booking.origin_iata, "dest": booking.destination_iata}
        )

        return booking

    @classmethod
    def get_booking(cls, db: Session, booking_id: uuid.UUID) -> AirTicketBooking:
        booking = db.scalar(select(AirTicketBooking).where(AirTicketBooking.id == booking_id))
        if not booking:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Ticket booking '{booking_id}' not found."
            )
        return booking

    @classmethod
    def list_bookings(
        cls,
        db: Session,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[AirTicketBooking]:
        stmt = select(AirTicketBooking).order_by(desc(AirTicketBooking.created_at))

        if search:
            q = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    AirTicketBooking.booking_ref.ilike(q),
                    AirTicketBooking.pnr_code.ilike(q),
                    AirTicketBooking.contact_name.ilike(q),
                    AirTicketBooking.contact_email.ilike(q),
                    AirTicketBooking.flight_number.ilike(q),
                )
            )

        stmt = stmt.limit(limit).offset(offset)
        return list(db.scalars(stmt).all())

    @classmethod
    def add_passenger(
        cls,
        db: Session,
        booking_id: uuid.UUID,
        payload: AirTicketPassengerCreate
    ) -> AirTicketPassenger:
        booking = cls.get_booking(db, booking_id)

        passenger = AirTicketPassenger(
            id=uuid.uuid4(),
            ticket_booking_id=booking.id,
            passenger_type=payload.passenger_type,
            title=payload.title.upper(),
            first_name=payload.first_name,
            last_name=payload.last_name,
            dob=payload.dob,
            gender=payload.gender,
            nationality=payload.nationality,
            passport_number=payload.passport_number,
            e_ticket_number=payload.e_ticket_number,
            seat_number=payload.seat_number,
            created_at=datetime.now(timezone.utc)
        )
        db.add(passenger)

        booking.passenger_count = len(booking.passengers) + 1
        booking.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(passenger)

        TimelineService.add_entry(
            db,
            entity_type="TICKET_BOOKING",
            entity_id=str(booking.id),
            event_type="PASSENGER_ADDED",
            title=f"Passenger {payload.first_name} {payload.last_name} Added",
            details={"passenger_id": str(passenger.id), "seat": payload.seat_number}
        )

        return passenger

    @classmethod
    def transition_booking(
        cls,
        db: Session,
        booking_id: uuid.UUID,
        payload: AirTicketTransitionRequest,
        actor_email: str = "admin@shafskyaviation.com"
    ) -> AirTicketBooking:
        booking = cls.get_booking(db, booking_id)
        old_status = booking.status.value

        booking.status = payload.target_state
        if payload.pnr_code:
            booking.pnr_code = payload.pnr_code.upper().strip()

        booking.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(booking)

        TimelineService.add_entry(
            db,
            entity_type="TICKET_BOOKING",
            entity_id=str(booking.id),
            event_type="STATUS_TRANSITION",
            title=f"Status changed from {old_status} to {booking.status.value}",
            details={"reason": payload.reason, "pnr": booking.pnr_code}
        )

        AdminService.log_audit_action(
            db,
            actor_email=actor_email,
            action="TICKET_BOOKING_TRANSITION",
            resource_type="TICKET_BOOKING",
            resource_id=str(booking.id),
            details={"old_status": old_status, "new_status": booking.status.value, "reason": payload.reason}
        )

        return booking
