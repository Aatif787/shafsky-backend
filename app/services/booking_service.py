import uuid
import secrets
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy import select, or_, desc, func
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.schema import Booking, BookingStatus, Profile
from app.schemas.booking import BookingCreate
from app.booking.exceptions import ConcurrencyException
from app.booking.service_validator import ServiceValidator
from app.utils.customer_email import is_acceptable_customer_email, REAL_EMAIL_HELP

logger = logging.getLogger("shafsky.booking")

class BookingService:
    # Canonical package slugs used by the web catalog and WhatsApp menus.
    _PACKAGE_SLUG_ALIASES = {
        "gold-service": "gold",
        "silver-service": "silver",
        "elite-service": "elite",
        "elite-plus-service": "elite-plus",
        "eliteplus": "elite-plus",
        "platinum-service": "platinum",
        "bronze-service": "bronze",
        "diamond-service": "diamond",
    }

    @classmethod
    def normalize_package_slug(cls, service_tier_or_slug: Optional[str]) -> str:
        """Normalize a client package id/slug to the catalog slug (exact, no fuzzy upgrade)."""
        raw = (service_tier_or_slug or "silver").strip().lower()
        raw = raw.replace("_", "-").replace(" ", "-")
        # Strip accidental prefixes like "package-gold" / "tier-gold"
        for prefix in ("package-", "tier-", "pkg-"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        return cls._PACKAGE_SLUG_ALIASES.get(raw, raw)

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
        """
        Charge the same package unit price the catalog showed the customer.

        Resolves AirportService by exact service slug + journey + flight type.
        Never uses loose name matching (e.g. '%gold%') which can silently
        upgrade Silver/Gold to Elite Gold and inflate the Razorpay amount.
        """
        from app.models.journey_models import SupportedAirport, Service, AirportService

        code_clean = (airport_code or "").strip().upper()
        slug_clean = cls.normalize_package_slug(service_tier_or_slug)
        j_type_clean = (journey_type or "DEPARTURE").strip().upper()
        f_type_clean = (flight_type or "DOMESTIC").strip().upper()
        guests = max(1, pax_count or 1)

        airport = db.scalar(
            select(SupportedAirport).where(SupportedAirport.iata_code == code_clean)
        )
        if not airport:
            raise HTTPException(
                status_code=400,
                detail=f"Airport '{code_clean}' is not registered or supported in the database."
            )

        # Exact slug match only — do not ilike-match "gold" onto "Elite Gold".
        slug_candidates = {slug_clean, slug_clean.replace("-", "_")}
        service = db.scalar(
            select(Service).where(Service.slug.in_(list(slug_candidates)))
        )
        if not service:
            # Exact title match as a secondary path (e.g. admin renamed slug).
            human = slug_clean.replace("-", " ")
            service = db.scalar(
                select(Service).where(func.lower(Service.name) == human)
            )
        if not service:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Service package '{slug_clean}' was not found. "
                    "Please re-select a package from the catalog and try again."
                ),
            )

        mappings = list(
            db.scalars(
                select(AirportService).where(
                    AirportService.airport_id == airport.id,
                    AirportService.service_id == service.id,
                    AirportService.is_available == True,  # noqa: E712
                )
            ).all()
        )
        if not mappings:
            raise HTTPException(
                status_code=400,
                detail=f"Service tier '{slug_clean}' is not available at airport '{code_clean}'.",
            )

        exact_match = next(
            (
                m
                for m in mappings
                if (m.journey_type or "").upper() == j_type_clean
                and (m.flight_type or "").upper() == f_type_clean
                and m.is_available
            ),
            None,
        )
        # Prefer an ALL / wildcard flight_type row over failing hard when the
        # airport publishes one package price for both domestic and international.
        if not exact_match:
            exact_match = next(
                (
                    m
                    for m in mappings
                    if (m.journey_type or "").upper() == j_type_clean
                    and (m.flight_type or "").upper() in ("ALL", "*")
                    and m.is_available
                ),
                None,
            )

        if not exact_match:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Service package '{slug_clean}' is not available at airport "
                    f"'{code_clean}' for {j_type_clean} {f_type_clean}."
                ),
            )

        return round(float(exact_match.price) * guests, 2)

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

        email_ok, email_reason = is_acceptable_customer_email(payload.passenger_email or "")
        if not email_ok:
            detail = REAL_EMAIL_HELP if email_reason == "reserved_or_placeholder" else "Invalid passenger email address."
            raise HTTPException(status_code=422, detail=detail)

        from app.services.service_airport_rules import (
            normalize_flight_type,
            normalize_journey_type,
            resolve_service_airport_iata,
        )

        metadata_json = payload.metadata_json or payload.metadata or {}
        journey_type = normalize_journey_type(
            metadata_json.get("journey_type") or metadata_json.get("direction") or payload.service_category
        )
        service_airport = (
            metadata_json.get("service_airport")
            or metadata_json.get("airport_code")
            or ""
        )
        service_airport = str(service_airport).strip().upper()
        if journey_type == "ARRIVAL" and not payload.dest_code and service_airport:
            payload.dest_code = service_airport
        if journey_type == "DEPARTURE" and not payload.origin_code and service_airport:
            payload.origin_code = service_airport
        if journey_type == "TRANSIT" and not metadata_json.get("transit_code") and service_airport:
            metadata_json["transit_code"] = service_airport
            payload.metadata_json = metadata_json

        # 1. Dynamic Validation across all service categories
        service_category = ServiceValidator.validate_booking(payload)

        # 2. Resolve Service Options & Metadata
        service_options = payload.service_options or payload.options or payload.selected_services or {}
        selected_services = payload.selected_services or service_options
        metadata_json = payload.metadata_json or payload.metadata or {}

        # 3. Handle Flight Datetimes if present
        dep_time = payload.departure_time
        arr_time = payload.arrival_time
        if dep_time is not None and dep_time.tzinfo is None:
            dep_time = dep_time.replace(tzinfo=timezone.utc)
        if arr_time is not None and arr_time.tzinfo is None:
            arr_time = arr_time.replace(tzinfo=timezone.utc)

        early_meta = payload.metadata_json or payload.metadata or {}
        from app.services.service_airport_rules import normalize_journey_type as _norm_jt
        early_jt = _norm_jt(
            (early_meta or {}).get("journey_type")
            or (early_meta or {}).get("direction")
            or payload.service_category
        )

        # Checkout sends a single service clock. Map it onto the journey type
        # so arrival bookings are not rejected for a missing arrivalTime field.
        if early_jt == "ARRIVAL" and arr_time is None and dep_time is not None:
            arr_time = dep_time
        elif early_jt == "DEPARTURE" and dep_time is None and arr_time is not None:
            dep_time = arr_time

        service_clock = arr_time if early_jt == "ARRIVAL" else dep_time
        if early_jt == "TRANSIT":
            service_clock = dep_time or arr_time

        if service_clock is not None:
            if service_clock.tzinfo is None:
                service_clock = service_clock.replace(tzinfo=timezone.utc)
            now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
            if service_clock.astimezone(timezone.utc) < now_aware.astimezone(timezone.utc):
                raise HTTPException(
                    status_code=400,
                    detail="This flight time is in the past and cannot be booked."
                )

        if service_category == "Airport Assistance" and service_clock is not None:
            from app.services.booking_cutoff import (
                evaluate_booking_cutoff,
                lookup_airport_timezone,
                airport_min_notice_hours,
                INTERNATIONAL_MIN_HOURS,
            )
            from app.services.service_airport_rules import derive_flight_type_from_route
            now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
            derived_ft = None
            eff_origin = (
                (early_meta or {}).get("origin_iata")
                or payload.origin_code
                or (early_meta or {}).get("origin_code")
            )
            eff_dest = (
                (early_meta or {}).get("destination_iata")
                or payload.dest_code
                or (early_meta or {}).get("dest_code")
            )
            if early_jt != "TRANSIT":
                try:
                    derived_ft = derive_flight_type_from_route(
                        db, eff_origin, eff_dest, early_jt
                    )
                except (ValueError, Exception):
                    derived_ft = None

            # Strict consistency safeguard: check if requested travel type matches verified route
            selected_ft = (early_meta or {}).get("travel_type") or (early_meta or {}).get("flight_type")
            if (
                early_jt != "TRANSIT"
                and eff_origin
                and eff_dest
                and derived_ft in ("DOMESTIC", "INTERNATIONAL")
                and selected_ft
            ):
                from app.services.service_airport_rules import normalize_flight_type
                norm_selected = normalize_flight_type(selected_ft)
                if norm_selected in ("DOMESTIC", "INTERNATIONAL") and norm_selected != derived_ft:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Flight type mismatch: selected service type is {norm_selected}, "
                            f"but verified flight route is {derived_ft}."
                        ),
                    )

            if derived_ft not in ("DOMESTIC", "INTERNATIONAL"):
                notice = airport_min_notice_hours(
                    derived_ft
                    or (early_meta or {}).get("flight_type")
                    or (early_meta or {}).get("travel_type")
                    or (early_meta or {}).get("transit_type")
                )
                derived_ft = "INTERNATIONAL" if notice == INTERNATIONAL_MIN_HOURS else "DOMESTIC"
            svc_iata = service_airport or (
                payload.origin_code if early_jt == "DEPARTURE" else payload.dest_code
            )
            cutoff = evaluate_booking_cutoff(
                scheduled_dt=service_clock,
                now_utc=now_aware,
                airport_tz_name=lookup_airport_timezone(db, svc_iata),
                flight_type=derived_ft,
            )
            if not cutoff.allowed:
                raise HTTPException(status_code=400, detail=cutoff.customer_message)

        if dep_time is not None and arr_time is not None and arr_time < dep_time:
            raise HTTPException(
                status_code=400,
                detail="Flight arrival time must be after departure time."
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
        # Policy: Children & Infants are complimentary / free. Only Adults are billable.
        adults_count = 1
        for pax_key in ("pax_adults", "adults"):
            if metadata_json and pax_key in metadata_json:
                try:
                    adults_count = max(1, int(metadata_json.get(pax_key, 1)))
                    break
                except (ValueError, TypeError):
                    adults_count = 1

        children_count = 0
        for ch_key in ("pax_children", "children", "child_count"):
            if metadata_json and ch_key in metadata_json:
                try:
                    children_count = max(0, int(metadata_json.get(ch_key, 0)))
                    break
                except (ValueError, TypeError):
                    children_count = 0

        infants_count = 0
        for inf_key in ("pax_infants", "infants", "infant_count"):
            if metadata_json and inf_key in metadata_json:
                try:
                    infants_count = max(0, int(metadata_json.get(inf_key, 0)))
                    break
                except (ValueError, TypeError):
                    infants_count = 0

        total_guests = adults_count + children_count + infants_count
        raw_guest_count = (metadata_json or {}).get("guest_count") or (metadata_json or {}).get("passenger_count")
        if raw_guest_count:
            try:
                total_guests = max(total_guests, int(raw_guest_count))
            except (ValueError, TypeError):
                pass

        billable_pax = adults_count

        from app.models.journey_models import SupportedAirport
        from app.services.service_airport_rules import normalize_iata

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

        if not target_airport and payload.service_options:
            opts = payload.service_options if isinstance(payload.service_options, dict) else {}
            for opt_key in ("airport", "origin", "destination", "pickup_location", "dropoff_location"):
                opt_val = str(opts.get(opt_key) or "").strip()
                if opt_val:
                    if len(opt_val) == 3:
                        cand = normalize_iata(opt_val)
                        if cand:
                            target_airport = cand
                            break
                    import re
                    iata_match = re.search(r'\b([A-Z]{3})\b', opt_val.upper())
                    if iata_match:
                        cand = normalize_iata(iata_match.group(1))
                        if cand and len(cand) == 3:
                            target_airport = cand
                            break

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

        # Derive authoritative flight_type from actual route countries.
        # Client-supplied flight_type is NOT trusted for pricing/package selection.
        from app.services.service_airport_rules import derive_flight_type_from_route
        eff_origin = (
            (meta or {}).get("origin_iata")
            or payload.origin_code
            or (meta or {}).get("origin_code")
        )
        eff_dest = (
            (meta or {}).get("destination_iata")
            or payload.dest_code
            or (meta or {}).get("dest_code")
        )
        try:
            derived_ft = derive_flight_type_from_route(
                db, eff_origin, eff_dest, journey_type
            )
            if derived_ft is not None:
                # ARRIVAL / DEPARTURE — use backend-derived classification
                flight_type = derived_ft
            else:
                # TRANSIT — preserve existing compound type from client
                flight_type = normalize_flight_type(
                    meta.get("flight_type") or meta.get("travel_type")
                ) or "DOMESTIC"
        except ValueError as route_err:
            raise HTTPException(status_code=400, detail=str(route_err))

        # Strict consistency safeguard: reject mismatched travel type vs verified route for any booking
        client_ft = (meta or {}).get("travel_type") or (meta or {}).get("flight_type")
        if (
            journey_type != "TRANSIT"
            and eff_origin
            and eff_dest
            and derived_ft in ("DOMESTIC", "INTERNATIONAL")
            and client_ft
        ):
            norm_client = normalize_flight_type(client_ft)
            if norm_client in ("DOMESTIC", "INTERNATIONAL") and norm_client != derived_ft:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Flight type mismatch: selected service type is {norm_client}, "
                        f"but verified flight route is {derived_ft}."
                    ),
                )

        target_service = cls.normalize_package_slug(payload.service_type or "silver")

        authoritative_price = cls.calculate_authoritative_price(
            db=db,
            airport_code=target_airport,
            service_tier_or_slug=target_service,
            journey_type=journey_type,
            flight_type=flight_type,
            pax_count=billable_pax
        )

        # All catalog prices are GST-inclusive (do not add extra tax)
        subtotal = round(float(authoritative_price), 2)
        taxes = 0.0
        charge_amount = subtotal
        metadata_json = dict(metadata_json or {})
        metadata_json["subtotal"] = subtotal
        metadata_json["taxes"] = taxes
        metadata_json["tax_rate"] = 0.0
        metadata_json["journey_type"] = journey_type
        metadata_json["flight_type"] = flight_type
        metadata_json["service_airport"] = target_airport
        metadata_json["package"] = target_service
        metadata_json["pax_adults"] = adults_count
        metadata_json["pax_children"] = children_count
        metadata_json["pax_infants"] = infants_count
        metadata_json["guest_count"] = total_guests
        metadata_json["billable_pax"] = billable_pax
        # Unit price the catalog charged (so invoices / retries never re-inflate).
        metadata_json["unit_price"] = round(float(subtotal) / billable_pax, 2) if billable_pax else float(subtotal)

        # Guard: never silently charge more than the client showed without an
        # explicit catalog reason. Log when the trusted DB total differs from
        # the (untrusted) client total so support can diagnose catalog drift.
        client_total = float(payload.total_amount or 0)
        if client_total > 0 and abs(client_total - float(charge_amount)) > 0.009:
            logger.warning(
                "[Booking Price] Client total ₹%.2f vs authoritative ₹%.2f "
                "(airport=%s package=%s journey=%s flight_type=%s billable_pax=%s total_guests=%s)",
                client_total,
                charge_amount,
                target_airport,
                target_service,
                journey_type,
                flight_type,
                billable_pax,
                total_guests,
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
                service_type=target_service,
                selected_services=selected_services,
                service_options=service_options,
                metadata_json=metadata_json,
                total_amount=charge_amount,
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
                        "passenger_count": total_guests,
                        "flight_num": new_booking.flight_num,
                        "origin_code": new_booking.origin_code,
                        "dest_code": new_booking.dest_code,
                        "airport_code": meta.get("service_airport") or new_booking.origin_code or new_booking.dest_code,
                        "journey_type": meta.get("journey_type") or new_booking.service_type,
                        "service_type": new_booking.service_type,
                        "service_name": meta.get("package") or new_booking.service_type,
                        "departure_time": new_booking.departure_time.isoformat() if new_booking.departure_time else None,
                        "terminal": meta.get("terminal"),
                        "total_amount": new_booking.total_amount if new_booking.total_amount is not None else 0.0,
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

        raise HTTPException(
            status_code=500,
            detail="Failed to create booking. Please try again."
        )

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
        now = datetime.now(timezone.utc)

        email_ok, email_reason = is_acceptable_customer_email(passenger_email or "")
        if not email_ok:
            detail = REAL_EMAIL_HELP if email_reason == "reserved_or_placeholder" else "Invalid passenger email address."
            raise HTTPException(status_code=422, detail=detail)

        # Lightweight payload for category-specific validators (free-text routes, not IATA).
        options: Dict[str, Any] = dict(details or {})
        if origin:
            options.setdefault("origin", origin)
            options.setdefault("pickup_location", origin)
            options.setdefault("pickup", origin)
        if destination:
            options.setdefault("destination", destination)
            options.setdefault("dropoff_location", destination)
            options.setdefault("dropoff", destination)
        if notes:
            options.setdefault("medical_notes", notes)
            options.setdefault("patient_condition", notes)

        class _EnquiryPayload:
            pass

        payload = _EnquiryPayload()
        payload.passenger_name = passenger_name.strip()
        payload.passenger_email = passenger_email.strip().lower()
        payload.passenger_phone = passenger_phone.strip()
        payload.service_category = service_category
        payload.service_type = service_type
        payload.origin_code = None
        payload.dest_code = None
        payload.flight_num = None
        payload.departure_time = None
        payload.arrival_time = None
        payload.notes = notes
        payload.service_options = options
        payload.options = options
        payload.selected_services = {"enquiry": True, "service_type": service_type}
        payload.metadata_json = {}
        payload.metadata = {}

        resolved_category = ServiceValidator.validate_booking(payload)
        if resolved_category == "Airport Assistance":
            raise HTTPException(
                status_code=400,
                detail="Airport Assistance must be booked through the airport booking flow, not as an enquiry.",
            )

        metadata_json: Dict[str, Any] = {
            "enquiry": True,
            "quote_only": True,
            "source": "web_solutions",
            "origin_label": (origin or "").strip() or None,
            "destination_label": (destination or "").strip() or None,
            "service_date": (service_date or "").strip() or None,
            "details": details or {},
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
                flight_num=None,
                origin_code=None,
                dest_code=None,
                departure_time=None,
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
                        "passenger_count": 1,
                        "flight_num": None,
                        "origin_code": origin,
                        "dest_code": destination,
                        "airport_code": None,
                        "journey_type": "ENQUIRY",
                        "service_type": new_booking.service_type,
                        "service_name": new_booking.service_type,
                        "departure_time": service_date,
                        "terminal": None,
                        "total_amount": 0.0,
                        "currency": "INR",
                        "status": "PENDING",
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
            stmt = stmt.where(Booking.service_category.ilike(service_category.strip()))

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
        expected_version: Optional[int] = None
    ) -> Booking:
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
            "totalAmount": booking.total_amount,
            "currency": booking.currency,
            "status": booking.status.value if isinstance(booking.status, BookingStatus) else str(booking.status),
            "version": getattr(booking, "version", 1),
            "notes": booking.notes,
            "createdAt": booking.created_at.isoformat() if booking.created_at else None
        }
