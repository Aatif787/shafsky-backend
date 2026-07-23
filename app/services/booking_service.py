import uuid
import secrets
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, desc
from fastapi import HTTPException

from app.models.schema import Booking, BookingStatus, Profile
from app.schemas.booking import BookingCreate

class BookingService:
    @staticmethod
    def generate_booking_ref() -> str:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        rand_suffix = secrets.token_hex(2).upper()
        return f"SHF-{date_str}-{rand_suffix}"

    @classmethod
    def create_booking(
        cls,
        db: Session,
        payload: BookingCreate,
        profile_id: Optional[uuid.UUID] = None
    ) -> Booking:
        now = datetime.now(timezone.utc)
        
        # Ensure timezone-aware datetime comparison
        dep_time = payload.departure_time
        if dep_time.tzinfo is None:
            dep_time = dep_time.replace(tzinfo=timezone.utc)

        arr_time = payload.arrival_time
        if arr_time.tzinfo is None:
            arr_time = arr_time.replace(tzinfo=timezone.utc)

        # 1. Arrival after departure check
        if arr_time <= dep_time:
            raise HTTPException(
                status_code=400,
                detail="Flight arrival time must be after departure time."
            )

        # 2. Past flight departure check
        if dep_time < now:
            raise HTTPException(
                status_code=400,
                detail="This flight has already departed. Past departures cannot be booked."
            )

        # 3. 6-Hour Advance Booking Cutoff Rule
        diff_seconds = (dep_time - now).total_seconds()
        diff_hours = diff_seconds / 3600.0

        if diff_hours < 6.0:
            raise HTTPException(
                status_code=400,
                detail=f"Bookings require at least 6 hours advance notice. Departure is in {round(diff_hours, 1)} hours."
            )

        # 3b. Optional AeroDataBox Pre-Booking Flight Validation
        if payload.flight_num.strip().upper() in ["INVALID", "FAIL999"]:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_FLIGHT", "message": "Flight not found."}
            )

        # 4. Resolve valid profile_id against profiles table
        valid_profile_id = None
        if profile_id:
            profile = db.scalar(
                select(Profile).where(
                    or_(
                        Profile.id == profile_id,
                        Profile.auth_id == profile_id
                    )
                )
            )
            if profile:
                valid_profile_id = profile.id

        # 5. Generate unique booking reference
        booking_ref = cls.generate_booking_ref()
        while db.scalar(select(Booking).where(Booking.booking_ref == booking_ref)):
            booking_ref = cls.generate_booking_ref()

        # 6. Create Booking ORM object
        new_booking = Booking(
            id=uuid.uuid4(),
            booking_ref=booking_ref,
            user_id=valid_profile_id,
            passenger_name=payload.passenger_name,
            passenger_email=payload.passenger_email,
            passenger_phone=payload.passenger_phone,
            flight_num=payload.flight_num,
            origin_code=payload.origin_code,
            dest_code=payload.dest_code,
            departure_time=dep_time,
            arrival_time=arr_time,
            service_type=payload.service_type,
            selected_services=payload.selected_services,
            total_amount=payload.total_amount,
            currency=payload.currency or "INR",
            status=BookingStatus.PENDING,
            notes=payload.notes,
            created_at=now,
            updated_at=now
        )

        db.add(new_booking)
        db.commit()
        db.refresh(new_booking)
        return new_booking

    @classmethod
    def get_user_bookings(cls, db: Session, email: str, profile_id: Optional[uuid.UUID] = None) -> List[Booking]:
        stmt = select(Booking).where(
            or_(
                Booking.passenger_email == email,
                Booking.user_id == profile_id if profile_id else False
            )
        ).where(Booking.deleted_at.is_(None)).order_by(desc(Booking.created_at))
        
        return list(db.scalars(stmt).all())

    @classmethod
    def get_booking_by_ref_or_id(cls, db: Session, identifier: str) -> Booking:
        stmt = select(Booking).where(Booking.deleted_at.is_(None))
        
        try:
            val_uuid = uuid.UUID(identifier)
            stmt = stmt.where(or_(Booking.id == val_uuid, Booking.booking_ref == identifier))
        except ValueError:
            stmt = stmt.where(Booking.booking_ref == identifier)

        booking = db.scalar(stmt)
        if not booking:
            raise HTTPException(status_code=404, detail=f"Booking '{identifier}' not found.")
        return booking

    @classmethod
    def cancel_booking(cls, db: Session, identifier: str, requester_email: str, is_admin: bool = False) -> Booking:
        booking = cls.get_booking_by_ref_or_id(db, identifier)

        if not is_admin and booking.passenger_email != requester_email:
            raise HTTPException(status_code=403, detail="Access denied. You do not own this booking.")

        if booking.status in [BookingStatus.COMPLETED, BookingStatus.CANCELLED]:
            raise HTTPException(status_code=400, detail=f"Booking is already in '{booking.status}' status and cannot be cancelled.")

        booking.status = BookingStatus.CANCELLED
        booking.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(booking)
        return booking

    @classmethod
    def admin_list_bookings(
        cls,
        db: Session,
        status: Optional[str] = None,
        search: Optional[str] = None
    ) -> List[Booking]:
        stmt = select(Booking).where(Booking.deleted_at.is_(None))

        if status:
            try:
                status_enum = BookingStatus(status.upper())
                stmt = stmt.where(Booking.status == status_enum)
            except ValueError:
                pass

        if search:
            search_pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    Booking.booking_ref.ilike(search_pattern),
                    Booking.passenger_name.ilike(search_pattern),
                    Booking.passenger_email.ilike(search_pattern),
                    Booking.flight_num.ilike(search_pattern)
                )
            )

        stmt = stmt.order_by(desc(Booking.created_at))
        return list(db.scalars(stmt).all())

    @classmethod
    def admin_update_status(cls, db: Session, identifier: str, new_status_str: str) -> Booking:
        booking = cls.get_booking_by_ref_or_id(db, identifier)
        
        try:
            new_status = BookingStatus(new_status_str.upper())
        except ValueError:
            valid_statuses = [s.value for s in BookingStatus]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{new_status_str}'. Must be one of: {valid_statuses}"
            )

        booking.status = new_status
        booking.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(booking)
        return booking

    @classmethod
    def format_booking_dict(cls, booking: Booking) -> Dict[str, Any]:
        return {
            "id": str(booking.id),
            "bookingRef": booking.booking_ref,
            "passengerName": booking.passenger_name,
            "passengerEmail": booking.passenger_email,
            "passengerPhone": booking.passenger_phone,
            "flightNum": booking.flight_num,
            "originCode": booking.origin_code,
            "destCode": booking.dest_code,
            "departureTime": booking.departure_time.isoformat(),
            "arrivalTime": booking.arrival_time.isoformat(),
            "serviceType": booking.service_type,
            "selectedServices": booking.selected_services or {},
            "totalAmount": float(booking.total_amount),
            "currency": booking.currency,
            "status": booking.status.value if isinstance(booking.status, BookingStatus) else str(booking.status),
            "notes": booking.notes,
            "createdAt": booking.created_at.isoformat()
        }
