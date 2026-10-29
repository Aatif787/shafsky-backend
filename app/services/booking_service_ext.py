"""
Extended booking management, enquiry creation, cancellation, and admin
operations mixin for BookingService.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models.schema import Booking, BookingStatus


def _bs():
    import app.services.booking_service as bs_mod
    return bs_mod


class BookingServiceExtMixin:
    """Mixin providing service enquiries, lookup, cancellation, and admin methods for BookingService."""

    @classmethod
    def create_service_enquiry(
        cls,
        db: Session,
        *,
        passenger_name: str,
        passenger_email: str,
        passenger_phone: str,
        service_category: str,
        service_type: str,
        origin: Optional[str] = None,
        destination: Optional[str] = None,
        service_date: Optional[str] = None,
        notes: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Booking:
        """
        Persist a quote-style enquiry (hotel, transport, medical, travel, cargo)
        as a PENDING booking with zero charge — visible in the admin Bookings desk.
        Does not initiate payment or run airport catalog pricing.
        """
        bs = _bs()
        logger = bs.logger
        now = datetime.now(timezone.utc)

        p_name = (passenger_name or "").strip()
        if not p_name or len(p_name) < 2 or not re.match(r"^[A-Za-z\s]+$", p_name):
            raise HTTPException(
                status_code=422,
                detail="Please enter a valid passenger name using letters only."
            )

        email_ok, email_reason = bs.is_acceptable_customer_email(passenger_email or "")
        if not email_ok:
            detail = bs.REAL_EMAIL_HELP if email_reason == "reserved_or_placeholder" else "Invalid passenger email address."
            raise HTTPException(status_code=422, detail=detail)

        # Lightweight payload for category-specific validators (free-text routes, not IATA).
        options: Dict[str, Any] = dict(details or {})
        resolved_origin = (origin or options.get("pickup_location") or options.get("pickup") or "").strip() or None
        resolved_dest = (destination or options.get("dropoff_location") or options.get("dropoff") or "").strip() or None
        resolved_date = (service_date or options.get("service_date") or "").strip() or None

        if resolved_origin:
            options.setdefault("origin", resolved_origin)
            options.setdefault("pickup_location", resolved_origin)
            options.setdefault("pickup", resolved_origin)
        if resolved_dest:
            options.setdefault("destination", resolved_dest)
            options.setdefault("dropoff_location", resolved_dest)
            options.setdefault("dropoff", resolved_dest)
        if resolved_date:
            options.setdefault("service_date", resolved_date)
        if notes:
            options.setdefault("medical_notes", notes)
            options.setdefault("patient_condition", notes)

        class _EnquiryPayload:
            passenger_name: str = ""
            passenger_email: str = ""
            passenger_phone: str = ""
            service_category: str = ""
            service_type: str = ""
            origin_code: Optional[str] = None
            dest_code: Optional[str] = None
            flight_num: Optional[str] = None
            departure_time: Optional[datetime] = None
            arrival_time: Optional[datetime] = None
            notes: Optional[str] = None
            service_options: Dict[str, Any] = {}
            options: Dict[str, Any] = {}
            selected_services: Dict[str, Any] = {}
            metadata_json: Dict[str, Any] = {}
            metadata: Dict[str, Any] = {}

        payload = _EnquiryPayload()
        payload.passenger_name = passenger_name.strip()
        payload.passenger_email = passenger_email.strip().lower()
        payload.passenger_phone = passenger_phone.strip()
        payload.service_category = service_category
        payload.service_type = service_type
        payload.origin_code = resolved_origin
        payload.dest_code = resolved_dest
        payload.flight_num = None
        payload.departure_time = None
        payload.arrival_time = None
        payload.notes = notes
        payload.service_options = options
        payload.options = options
        payload.selected_services = {"enquiry": True, "service_type": service_type}
        payload.metadata_json = {}
        payload.metadata = {}

        resolved_category = bs.ServiceValidator.validate_booking(payload)
        if resolved_category == "Airport Assistance":
            raise HTTPException(
                status_code=400,
                detail="Airport Assistance must be booked through the airport booking flow, not as an enquiry.",
            )

        resolved_flight_num: Optional[str] = (
            (details or {}).get("outbound_flight")
            or (details or {}).get("flight_number")
            or (details or {}).get("flight_num")
            or options.get("flight_num")
            or None
        )
        if resolved_flight_num:
            resolved_flight_num = resolved_flight_num.strip().upper() or None

        parsed_departure_time: Optional[datetime] = None
        if resolved_date:
            try:
                clean_d = str(resolved_date).strip().replace("Z", "+00:00")
                if "T" in clean_d:
                    parsed_departure_time = datetime.fromisoformat(clean_d)
                elif " " in clean_d:
                    parsed_departure_time = datetime.strptime(clean_d, "%Y-%m-%d %H:%M")
                else:
                    parsed_departure_time = datetime.strptime(clean_d[:10], "%Y-%m-%d")
                if parsed_departure_time and parsed_departure_time.tzinfo is None:
                    parsed_departure_time = parsed_departure_time.replace(tzinfo=timezone.utc)
            except Exception:
                parsed_departure_time = None

        enquiry_details = dict(details or {})
        if notes and not enquiry_details.get("additional_requirements") and not enquiry_details.get("notes"):
            enquiry_details["additional_requirements"] = notes

        enquiry_pax = (
            enquiry_details.get("passenger_count")
            or enquiry_details.get("passengerCount")
            or options.get("passenger_count")
            or 1
        )
        try:
            enquiry_pax = int(enquiry_pax)
        except (ValueError, TypeError):
            enquiry_pax = 1

        metadata_json: Dict[str, Any] = {
            "enquiry": True,
            "quote_only": True,
            "source": "web_solutions",
            "origin_label": (resolved_origin or "").strip() or None,
            "destination_label": (resolved_dest or "").strip() or None,
            "service_date": (resolved_date or "").strip() or None,
            "passenger_count": enquiry_pax,
            "details": enquiry_details,
        }

        max_attempts = 5
        for attempt in range(max_attempts):
            booking_ref = cls.generate_booking_ref()
            while db.scalar(select(Booking).where(Booking.booking_ref == booking_ref)):
                booking_ref = cls.generate_booking_ref()

            new_booking = Booking(
                id=uuid.uuid4(),
                booking_ref=booking_ref,
                user_id=None,
                passenger_name=payload.passenger_name,
                passenger_email=payload.passenger_email,
                passenger_phone=payload.passenger_phone,
                service_category=resolved_category,
                flight_num=resolved_flight_num,
                origin_code=resolved_origin,
                dest_code=resolved_dest,
                departure_time=parsed_departure_time,
                arrival_time=None,
                service_type=service_type.strip(),
                selected_services=payload.selected_services,
                service_options=options,
                metadata_json=metadata_json,
                total_amount=0.0,
                currency="INR",
                status=BookingStatus.PENDING,
                version=1,
                notes=notes,
                created_at=now,
                updated_at=now,
            )

            try:
                db.add(new_booking)
                db.commit()
                db.refresh(new_booking)
                try:
                    from app.services.notification_service import NotificationService
                    NotificationService.notify_booking_created(db, {
                        "booking_ref": new_booking.booking_ref,
                        "passenger_name": new_booking.passenger_name,
                        "passenger_email": new_booking.passenger_email,
                        "passenger_phone": new_booking.passenger_phone,
                        "passenger_count": enquiry_pax,
                        "flight_num": resolved_flight_num,
                        "origin_code": resolved_origin,
                        "dest_code": resolved_dest,
                        "airport_code": None,
                        "journey_type": "ENQUIRY",
                        "service_category": resolved_category,
                        "service_type": new_booking.service_type,
                        "service_name": new_booking.service_type,
                        "departure_time": resolved_date,
                        "terminal": None,
                        "total_amount": 0.0,
                        "currency": "INR",
                        "status": "PENDING",
                        "notes": notes,
                        "details": enquiry_details,
                        "service_options": options,
                    })
                except Exception:
                    logger.exception(
                        "Enquiry persisted but notification dispatch failed for %s",
                        new_booking.booking_ref,
                    )
                return new_booking
            except IntegrityError as exc:
                db.rollback()
                if attempt == max_attempts - 1:
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to generate unique enquiry reference. Please try again.",
                    ) from exc

        raise HTTPException(status_code=500, detail="Failed to create enquiry. Please try again.")

    @classmethod
    def get_user_bookings(cls, db: Session, email: str, profile_id: Optional[uuid.UUID] = None) -> List[Booking]:
        conditions = [Booking.passenger_email == email]
        if profile_id:
            conditions.append(Booking.user_id == profile_id)
        stmt = select(Booking).where(
            or_(*conditions)
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
        ConcurrencyException = _bs().ConcurrencyException
        booking = cls.get_booking_by_ref_or_id(db, identifier)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found.")

        if not is_admin and booking.passenger_email != requester_email:
            raise HTTPException(status_code=403, detail="Access denied. You do not own this booking.")

        if booking.status in [BookingStatus.COMPLETED, BookingStatus.CANCELLED]:
            raise HTTPException(status_code=400, detail=f"Booking is already in '{booking.status}' status and cannot be cancelled.")
        if booking.status == BookingStatus.CONFIRMED:
            from app.models.payment import PaymentStatus, PaymentTransaction

            has_unrefunded_payment = db.scalar(
                select(PaymentTransaction.id).where(
                    PaymentTransaction.entity_id == booking.booking_ref,
                    PaymentTransaction.status.in_(
                        [PaymentStatus.SUCCESSFUL, PaymentStatus.PARTIALLY_REFUNDED]
                    ),
                    PaymentTransaction.is_duplicate.isnot(True),
                ).limit(1)
            )
            if has_unrefunded_payment:
                raise HTTPException(
                    status_code=409,
                    detail="Paid booking must be refunded before cancellation.",
                )

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
        search: Optional[str] = None,
        service_category: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[List[Booking], int]:
        stmt = select(Booking).where(Booking.deleted_at.is_(None))

        if status:
            try:
                status_enum = BookingStatus(status.upper())
                stmt = stmt.where(Booking.status == status_enum)
            except ValueError:
                pass

        if service_category and service_category.strip() and service_category.upper() != "ALL":
            # Canonical filters expand to WhatsApp / legacy category aliases so
            # operators can find desk work regardless of channel taxonomy.
            CATEGORY_FILTER_ALIASES: Dict[str, List[str]] = {
                "Airport Assistance": [
                    "Airport Assistance",
                    "Airport Services",
                    "Meet & Greet",
                ],
                "Ground Transport": [
                    "Ground Transport",
                    "Hotel & Transportation",
                    "Transportation Services",
                ],
                "Travel Support": [
                    "Travel Support",
                    "Travel Services",
                    "Hotel Services",
                    "Hotel Booking",
                ],
                "Medical Assistance": ["Medical Assistance", "Medical Services"],
                "Cargo & Logistics": ["Cargo & Logistics", "Cargo"],
                "Private Charter": ["Private Charter", "Charter"],
                "Meet & Greet": ["Meet & Greet", "Airport Assistance", "Airport Services"],
                "Lounge": ["Lounge", "Lounge Access", "Airport Lounge"],
            }
            raw_cat = service_category.strip()
            aliases = CATEGORY_FILTER_ALIASES.get(raw_cat, [raw_cat])
            stmt = stmt.where(
                or_(*[Booking.service_category.ilike(alias) for alias in aliases])
            )

        if date_from:
            try:
                start = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                travel_ts = func.coalesce(Booking.departure_time, Booking.created_at)
                stmt = stmt.where(travel_ts >= start)
            except ValueError:
                pass

        if date_to:
            try:
                end = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                if end.hour == 0 and end.minute == 0 and end.second == 0:
                    end = end.replace(hour=23, minute=59, second=59)
                travel_ts = func.coalesce(Booking.departure_time, Booking.created_at)
                stmt = stmt.where(travel_ts <= end)
            except ValueError:
                pass

        if search:
            search_pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Booking.booking_ref.ilike(search_pattern),
                    Booking.passenger_name.ilike(search_pattern),
                    Booking.passenger_email.ilike(search_pattern),
                    Booking.passenger_phone.ilike(search_pattern),
                    Booking.flight_num.ilike(search_pattern),
                    Booking.origin_code.ilike(search_pattern),
                    Booking.dest_code.ilike(search_pattern),
                )
            )

        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        page = max(1, page or 1)
        page_size = min(100, max(1, page_size or 25))
        stmt = stmt.order_by(desc(Booking.created_at)).offset((page - 1) * page_size).limit(page_size)
        return list(db.scalars(stmt).all()), total

    @classmethod
    def admin_update_status(
        cls,
        db: Session,
        identifier: str,
        new_status_str: str,
        expected_version: Optional[int] = None,
        force_confirm: bool = False,
        reason: Optional[str] = None,
        actor_email: Optional[str] = None,
    ) -> Booking:
        bs = _bs()
        logger = bs.logger
        ConcurrencyException = bs.ConcurrencyException

        booking = cls.get_booking_by_ref_or_id(db, identifier)
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found.")

        try:
            new_status = BookingStatus(new_status_str.upper())
        except ValueError:
            valid_statuses = [s.value for s in BookingStatus]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{new_status_str}'. Must be one of: {valid_statuses}"
            )

        if new_status == BookingStatus.CONFIRMED:
            from app.models.payment import PaymentStatus, PaymentTransaction

            has_successful_payment = db.scalar(
                select(PaymentTransaction.id).where(
                    PaymentTransaction.entity_id == booking.booking_ref,
                    PaymentTransaction.status == PaymentStatus.SUCCESSFUL,
                    PaymentTransaction.is_duplicate.isnot(True),
                ).limit(1)
            )
            if not has_successful_payment:
                if not force_confirm:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Cannot confirm unpaid booking without payment proof. "
                            "Pass force_confirm=true with a reason to override."
                        ),
                    )
                if not (reason or "").strip():
                    raise HTTPException(
                        status_code=400,
                        detail="force_confirm requires a non-empty reason for audit.",
                    )
                try:
                    from app.services.admin_service import AdminService
                    AdminService.log_audit_action(
                        db,
                        actor_email=actor_email or "admin@shafsky.com",
                        action="BOOKING_FORCE_CONFIRMED",
                        resource_type="BOOKING",
                        resource_id=booking.booking_ref,
                        details={"reason": reason, "prior_status": str(booking.status)},
                    )
                except Exception:
                    logger.exception("Failed to audit force_confirm for %s", booking.booking_ref)

        if new_status == BookingStatus.CANCELLED:
            from app.models.payment import PaymentStatus, PaymentTransaction

            has_unrefunded_payment = db.scalar(
                select(PaymentTransaction.id).where(
                    PaymentTransaction.entity_id == booking.booking_ref,
                    PaymentTransaction.status.in_(
                        [PaymentStatus.SUCCESSFUL, PaymentStatus.PARTIALLY_REFUNDED]
                    ),
                    PaymentTransaction.is_duplicate.isnot(True),
                ).limit(1)
            )
            if has_unrefunded_payment:
                raise HTTPException(
                    status_code=409,
                    detail="Paid booking must be refunded before cancellation.",
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
        meta = booking.metadata_json or {}
        return {
            "id": str(booking.id),
            "bookingRef": booking.booking_ref,
            "passengerName": booking.passenger_name,
            "passengerEmail": booking.passenger_email,
            "passengerPhone": booking.passenger_phone,
            "serviceCategory": getattr(booking, "service_category", "Airport Assistance"),
            "serviceType": booking.service_type,
            "flightNum": booking.flight_num,
            "flightNum2": getattr(booking, "flight_num_2", None) or meta.get("flight_number_2") or meta.get("connecting_flight_number"),
            "originCode": booking.origin_code,
            "destCode": booking.dest_code,
            "departureTime": booking.departure_time.isoformat() if booking.departure_time else None,
            "arrivalTime": booking.arrival_time.isoformat() if booking.arrival_time else None,
            "selectedServices": booking.selected_services or {},
            "serviceOptions": getattr(booking, "service_options", booking.selected_services or {}),
            "metadataJson": meta,
            "totalAmount": booking.total_amount,
            "currency": booking.currency,
            "status": booking.status.value if isinstance(booking.status, BookingStatus) else str(booking.status),
            "version": getattr(booking, "version", 1),
            "notes": booking.notes,
            "createdAt": booking.created_at.isoformat() if booking.created_at else None
        }
