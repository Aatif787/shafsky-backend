import uuid
import secrets
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy import select, or_, desc
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.schema import Booking, BookingStatus, Profile
from app.schemas.booking import BookingCreate
from app.booking.exceptions import ConcurrencyException
from app.booking.service_validator import ServiceValidator

logger = logging.getLogger("shafsky.booking")

class BookingService:
    @classmethod
    def calculate_authoritative_price(
        cls,
        db: Session,
        airport_code: str,
        service_tier_or_slug: str,
        journey_type: str = "DEPARTURE",
        flight_type: str = "DOMESTIC",
        pax_count: int = 1
    ) -> float:
        from app.models.journey_models import SupportedAirport, Service, AirportService

        code_clean = (airport_code or "DEL").strip().upper()
        slug_clean = (service_tier_or_slug or "silver").strip().lower()
        j_type_clean = (journey_type or "DEPARTURE").strip().upper()
        f_type_clean = (flight_type or "DOMESTIC").strip().upper()

        # 1. Lookup Airport
        airport = db.scalar(
            select(SupportedAirport).where(SupportedAirport.iata_code == code_clean)
        )
        if not airport:
            raise HTTPException(
                status_code=400,
                detail=f"Airport '{code_clean}' is not registered or supported in the database."
            )

        # 2. Lookup Service by slug or matching tier name
        service = db.scalar(
            select(Service).where(or_(Service.slug == slug_clean, Service.name.ilike(f"%{slug_clean}%")))
        )

        # 3. Lookup AirportService relationship in DB
        stmt = select(AirportService).where(
            AirportService.airport_id == airport.id,
            AirportService.is_available == True
        )
        if service:
            stmt = stmt.where(AirportService.service_id == service.id)

        mappings = list(db.scalars(stmt).all())

        if not mappings and service:
            # Check if any available mapping exists for this service_id at this airport
            mappings = list(db.scalars(
                select(AirportService).where(
                    AirportService.airport_id == airport.id,
                    AirportService.is_available == True,
                    AirportService.service_id == service.id
                )
            ).all())

        if not mappings:
            raise HTTPException(
                status_code=400,
                detail=f"Service tier '{slug_clean}' is not available at airport '{code_clean}'."
            )

        # Filter by journey_type and flight_type for exact match
        exact_match = None
        for m in mappings:
            if m.journey_type == j_type_clean and m.flight_type == f_type_clean and m.is_available:
                exact_match = m
                break

        if not exact_match:
            raise HTTPException(
                status_code=400,
                detail=f"Service package '{slug_clean}' is not available at airport '{code_clean}' for {j_type_clean} {f_type_clean}."
            )

        unit_price = float(exact_match.price)
        total = round(unit_price * max(1, pax_count), 2)
        return total

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

        # 1. Dynamic Validation across all service categories
        service_category = ServiceValidator.validate_booking(payload)

        # 2. Resolve Service Options & Metadata
        service_options = payload.service_options or payload.options or payload.selected_services or {}
        selected_services = payload.selected_services or service_options
        metadata_json = payload.metadata_json or payload.metadata or {}

        # 3. Handle Flight Datetimes if present
        dep_time = payload.departure_time
        arr_time = payload.arrival_time

        if dep_time is not None:
            if dep_time.tzinfo is None:
                dep_time = dep_time.replace(tzinfo=timezone.utc)

            if arr_time is not None:
                if arr_time.tzinfo is None:
                    arr_time = arr_time.replace(tzinfo=timezone.utc)
                if arr_time <= dep_time:
                    raise HTTPException(
                        status_code=400,
                        detail="Flight arrival time must be after departure time."
                    )

            if dep_time < now:
                raise HTTPException(
                    status_code=400,
                    detail="This flight has already departed. Past departures cannot be booked."
                )

            diff_seconds = (dep_time - now).total_seconds()
            diff_hours = diff_seconds / 3600.0
            if diff_hours < 6.0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Bookings require at least 6 hours advance notice. Departure is in {round(diff_hours, 1)} hours."
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

        # 5. Calculate server-side authoritative price from Database (Ignore untrusted client price)
        pax_count = 1
        if metadata_json and "pax_adults" in metadata_json:
            try:
                pax_count = max(1, int(metadata_json.get("pax_adults", 1)))
            except (ValueError, TypeError):
                pax_count = 1

        from app.models.journey_models import SupportedAirport
        from app.services.service_airport_rules import (
            normalize_flight_type,
            normalize_journey_type,
            resolve_service_airport_iata,
        )

        meta = metadata_json or {}
        journey_type = normalize_journey_type(
            meta.get("journey_type") or meta.get("direction") or payload.service_category
        )
        transit_code = meta.get("transit_code") or meta.get("transit") or meta.get("service_airport")
        target_airport = resolve_service_airport_iata(
            journey_type,
            origin=payload.origin_code,
            destination=payload.dest_code,
            transit=transit_code,
        )
        if not target_airport:
            target_airport = (meta.get("service_airport") or "").strip().upper()

        if not target_airport:
            raise HTTPException(
                status_code=400,
                detail="Unable to resolve a supported airport for this booking.",
            )

        supported_row = db.scalar(
            select(SupportedAirport).where(SupportedAirport.iata_code == target_airport)
        )
        if not supported_row or not supported_row.is_supported or not supported_row.is_active:
            raise HTTPException(
                status_code=400,
                detail=f"Shafsky does not currently operate at {target_airport}.",
            )

        flight_type = normalize_flight_type(
            meta.get("flight_type") or meta.get("travel_type")
        ) or "DOMESTIC"

        target_service = payload.service_type or "silver"

        authoritative_price = cls.calculate_authoritative_price(
            db=db,
            airport_code=target_airport,
            service_tier_or_slug=target_service,
            journey_type=journey_type,
            flight_type=flight_type,
            pax_count=pax_count
        )

        # 6. Generate unique booking reference with retry on concurrency collision
        max_attempts = 5
        for attempt in range(max_attempts):
            booking_ref = cls.generate_booking_ref()
            while db.scalar(select(Booking).where(Booking.booking_ref == booking_ref)):
                booking_ref = cls.generate_booking_ref()

            new_booking = Booking(
                id=uuid.uuid4(),
                booking_ref=booking_ref,
                user_id=valid_profile_id,
                passenger_name=payload.passenger_name,
                passenger_email=payload.passenger_email,
                passenger_phone=payload.passenger_phone,
                service_category=service_category,
                flight_num=payload.flight_num,
                origin_code=payload.origin_code,
                dest_code=payload.dest_code,
                departure_time=dep_time,
                arrival_time=arr_time,
                service_type=payload.service_type,
                selected_services=selected_services,
                service_options=service_options,
                metadata_json=metadata_json,
                total_amount=authoritative_price,
                currency=payload.currency or "INR",
                status=BookingStatus.PENDING,
                version=1,
                notes=payload.notes,
                created_at=now,
                updated_at=now
            )

            try:
                db.add(new_booking)
                db.commit()
                db.refresh(new_booking)
                try:
                    from app.services.notification_service import NotificationService
                    meta = new_booking.metadata_json or {}
                    NotificationService.notify_booking_created(db, {
                        "booking_ref": new_booking.booking_ref,
                        "passenger_name": new_booking.passenger_name,
                        "passenger_email": new_booking.passenger_email,
                        "passenger_phone": new_booking.passenger_phone,
                        "flight_num": new_booking.flight_num,
                        "origin_code": new_booking.origin_code,
                        "dest_code": new_booking.dest_code,
                        "airport_code": meta.get("service_airport") or new_booking.origin_code or new_booking.dest_code,
                        "journey_type": meta.get("journey_type") or new_booking.service_type,
                        "service_type": new_booking.service_type,
                        "service_name": meta.get("package") or new_booking.service_type,
                        "departure_time": new_booking.departure_time.isoformat() if new_booking.departure_time else None,
                        "terminal": meta.get("terminal"),
                        "total_amount": new_booking.total_amount,
                        "currency": new_booking.currency,
                        "status": new_booking.status.value if hasattr(new_booking.status, "value") else str(new_booking.status),
                    })
                except Exception:
                    logger.exception("Booking persisted but notification dispatch failed for %s", new_booking.booking_ref)
                return new_booking
            except IntegrityError as exc:
                db.rollback()
                if attempt == max_attempts - 1:
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to generate unique booking reference after multiple attempts. Please try again."
                    ) from exc

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
    def cancel_booking(
        cls,
        db: Session,
        identifier: str,
        requester_email: str,
        is_admin: bool = False,
        expected_version: Optional[int] = None
    ) -> Booking:
        booking = cls.get_booking_by_ref_or_id(db, identifier)

        if not is_admin and booking.passenger_email != requester_email:
            raise HTTPException(status_code=403, detail="Access denied. You do not own this booking.")

        if booking.status in [BookingStatus.COMPLETED, BookingStatus.CANCELLED]:
            raise HTTPException(status_code=400, detail=f"Booking is already in '{booking.status}' status and cannot be cancelled.")

        if expected_version is not None and booking.version != expected_version:
            raise ConcurrencyException(
                detail=f"Concurrency conflict: Booking version mismatch (expected version {expected_version}, but entity is at version {booking.version})."
            )

        booking.status = BookingStatus.CANCELLED
        booking.updated_at = datetime.now(timezone.utc)

        try:
            db.commit()
            db.refresh(booking)
            return booking
        except StaleDataError:
            db.rollback()
            raise ConcurrencyException()

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
    def admin_update_status(
        cls,
        db: Session,
        identifier: str,
        new_status_str: str,
        expected_version: Optional[int] = None
    ) -> Booking:
        booking = cls.get_booking_by_ref_or_id(db, identifier)
        
        try:
            new_status = BookingStatus(new_status_str.upper())
        except ValueError:
            valid_statuses = [s.value for s in BookingStatus]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{new_status_str}'. Must be one of: {valid_statuses}"
            )

        if expected_version is not None and booking.version != expected_version:
            raise ConcurrencyException(
                detail=f"Concurrency conflict: Booking version mismatch (expected version {expected_version}, but entity is at version {booking.version})."
            )

        booking.status = new_status
        booking.updated_at = datetime.now(timezone.utc)

        try:
            db.commit()
            db.refresh(booking)
            return booking
        except StaleDataError:
            db.rollback()
            raise ConcurrencyException()

    @classmethod
    def format_booking_dict(cls, booking: Booking) -> Dict[str, Any]:
        return {
            "id": str(booking.id),
            "bookingRef": booking.booking_ref,
            "passengerName": booking.passenger_name,
            "passengerEmail": booking.passenger_email,
            "passengerPhone": booking.passenger_phone,
            "serviceCategory": getattr(booking, "service_category", "Airport Assistance"),
            "serviceType": booking.service_type,
            "flightNum": booking.flight_num,
            "originCode": booking.origin_code,
            "destCode": booking.dest_code,
            "departureTime": booking.departure_time.isoformat() if booking.departure_time else None,
            "arrivalTime": booking.arrival_time.isoformat() if booking.arrival_time else None,
            "selectedServices": booking.selected_services or {},
            "serviceOptions": getattr(booking, "service_options", booking.selected_services or {}),
            "metadataJson": getattr(booking, "metadata_json", {}),
            "totalAmount": float(booking.total_amount),
            "currency": booking.currency,
            "status": booking.status.value if isinstance(booking.status, BookingStatus) else str(booking.status),
            "version": getattr(booking, "version", 1),
            "notes": booking.notes,
            "createdAt": booking.created_at.isoformat() if booking.created_at else None
        }
