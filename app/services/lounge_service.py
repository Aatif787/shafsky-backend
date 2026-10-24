import logging
import random
import re
import string
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple, Dict, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, func, or_, desc
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.lounge_models import LoungeEnquiry, LoungeEnquiryStatus
from app.models.journey_models import SupportedAirport
from app.schemas.lounge import (
    LoungeEnquiryCreate,
    LoungeEnquiryAdminUpdate,
    LoungeOptionsData,
    AirportOptionItem,
    CutoffRuleItem,
    LoungeEligibilityCheckRequest,
    LoungeEligibilityData,
)
from app.utils.customer_email import is_acceptable_customer_email, REAL_EMAIL_HELP
from app.services.notification_service import NotificationService

logger = logging.getLogger("shafsky.lounge")

VERIFIED_LOUNGE_TYPES = [
    "VIP / First Class Lounge",
    "Business Class Lounge",
    "Executive / Plaza Premium Lounge",
    "Arrival Lounge",
    "Domestic Departure Lounge",
    "International Departure Lounge",
]

DOMESTIC_LOUNGE_TYPES = [
    "Domestic Departure Lounge",
    "Executive / Plaza Premium Lounge",
    "Business Class Lounge",
    "VIP / First Class Lounge",
    "Arrival Lounge",
]

INTERNATIONAL_LOUNGE_TYPES = [
    "International Departure Lounge",
    "VIP / First Class Lounge",
    "Business Class Lounge",
    "Executive / Plaza Premium Lounge",
    "Arrival Lounge",
]

VERIFIED_PREFERRED_TIMES = [
    "Morning (06:00 – 12:00)",
    "Afternoon (12:00 – 18:00)",
    "Evening (18:00 – 22:00)",
    "Night / Early Morning (22:00 – 06:00)",
]

CUTOFF_RULES = {
    "Domestic": CutoffRuleItem(
        notice_hours=12,
        grace_period_hours=1,
        notice_text="Domestic lounge enquiries require at least 12 hours' notice. A 1-hour grace period may apply, subject to availability.",
    ),
    "International": CutoffRuleItem(
        notice_hours=24,
        grace_period_hours=1,
        notice_text="International lounge enquiries require at least 24 hours' notice. A 1-hour grace period may apply, subject to availability.",
    ),
}

AIRPORT_TIMEZONES = {
    "DEL": "Asia/Kolkata",
    "BOM": "Asia/Kolkata",
    "BLR": "Asia/Kolkata",
    "HYD": "Asia/Kolkata",
    "MAA": "Asia/Kolkata",
    "CCU": "Asia/Kolkata",
    "GOI": "Asia/Kolkata",
    "GOX": "Asia/Kolkata",
    "COK": "Asia/Kolkata",
    "AMD": "Asia/Kolkata",
    "DXB": "Asia/Dubai",
    "LHR": "Europe/London",
    "SIN": "Asia/Singapore",
    "DOH": "Asia/Qatar",
    "BKK": "Asia/Bangkok",
}


class LoungeService:
    @classmethod
    def generate_reference(cls) -> str:
        """Generate human-readable unique reference: SHAF-LNG-XXXXXX"""
        chars = string.ascii_uppercase + string.digits
        rnd = "".join(random.choices(chars, k=6))
        return f"SHAF-LNG-{rnd}"

    @classmethod
    def resolve_airport_timezone(cls, airport_str: Optional[str], db: Optional[Session] = None) -> str:
        """Resolves IANA timezone string for an airport, defaulting to Asia/Kolkata."""
        if not airport_str:
            return "Asia/Kolkata"

        raw = airport_str.strip().upper()
        # Look for 3-letter IATA code in parentheses or start
        match = re.search(r"\b([A-Z]{3})\b", raw)
        code = match.group(1) if match else raw[:3]

        if code in AIRPORT_TIMEZONES:
            return AIRPORT_TIMEZONES[code]

        if db:
            try:
                ap = db.scalar(select(SupportedAirport).where(SupportedAirport.iata_code == code))
                if ap and ap.timezone:
                    return ap.timezone
            except Exception:
                pass

        return "Asia/Kolkata"

    @classmethod
    def check_eligibility(
        cls,
        payload: LoungeEligibilityCheckRequest,
        db: Optional[Session] = None,
    ) -> LoungeEligibilityData:
        """
        Authoritative eligibility evaluation:
        - Domestic: 12h notice required. 1h grace period (min 11h).
        - International: 24h notice required. 1h grace period (min 23h).
        - Real-time calculation relative to server UTC time in the airport's local timezone.
        """
        category = (payload.lounge_category or "Domestic").strip().capitalize()
        if category not in ("Domestic", "International"):
            category = "Domestic"

        tz_str = cls.resolve_airport_timezone(payload.airport, db=db)
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = ZoneInfo("Asia/Kolkata")
            tz_str = "Asia/Kolkata"

        # Parse travel date (YYYY-MM-DD)
        try:
            dt_parts = [int(p) for p in payload.travel_date.strip().split("-")]
            year, month, day = dt_parts[0], dt_parts[1], dt_parts[2]
        except Exception:
            return LoungeEligibilityData(
                eligible=False,
                is_grace_period=False,
                lead_time_hours=0.0,
                required_notice_hours=12 if category == "Domestic" else 24,
                grace_period_hours=1,
                min_required_hours=11 if category == "Domestic" else 23,
                airport_timezone=tz_str,
                server_time=datetime.now(timezone.utc).isoformat(),
                scheduled_time=payload.travel_date,
                message="Invalid travel date format. Expected YYYY-MM-DD.",
            )

        # Parse preferred time
        time_str = payload.preferred_time.strip()
        hour = 6
        minute = 0
        if "Morning" in time_str:
            hour = 6
        elif "Afternoon" in time_str:
            hour = 12
        elif "Evening" in time_str:
            hour = 18
        elif "Night" in time_str:
            hour = 22
        else:
            m = re.search(r"(\d{1,2}):(\d{2})", time_str)
            if m:
                hour = int(m.group(1))
                minute = int(m.group(2))

        try:
            scheduled_dt = datetime(year, month, day, hour, minute, 0, tzinfo=tz)
        except Exception as exc:
            return LoungeEligibilityData(
                eligible=False,
                is_grace_period=False,
                lead_time_hours=0.0,
                required_notice_hours=12 if category == "Domestic" else 24,
                grace_period_hours=1,
                min_required_hours=11 if category == "Domestic" else 23,
                airport_timezone=tz_str,
                server_time=datetime.now(timezone.utc).isoformat(),
                scheduled_time=payload.travel_date,
                message=f"Invalid date/time combination: {exc}",
            )

        server_dt = datetime.now(timezone.utc)
        lead_seconds = (scheduled_dt - server_dt).total_seconds()
        lead_hours = round(lead_seconds / 3600.0, 2)

        notice_hours = 12 if category == "Domestic" else 24
        grace_period_hours = 1
        min_required_hours = notice_hours - grace_period_hours  # 11 or 23

        if lead_seconds <= 0:
            eligible = False
            is_grace = False
            msg = "The selected travel date and time is in the past. Please select an upcoming date and time."
        elif lead_hours >= notice_hours:
            eligible = True
            is_grace = False
            msg = f"Standard notice period met ({lead_hours:.1f}h notice in airport local timezone: {tz_str})."
        elif lead_hours >= min_required_hours:
            eligible = True
            is_grace = True
            msg = f"Within 1-hour grace period ({lead_hours:.1f}h notice). Subject to airport lounge operational availability."
        else:
            eligible = False
            is_grace = False
            msg = (
                f"Selected time is outside the allowed enquiry window. {category} lounge enquiries "
                f"require at least {notice_hours} hours' notice (minimum {min_required_hours} hours with grace period). "
                f"Current lead time is {max(0.0, lead_hours):.1f} hours."
            )

        return LoungeEligibilityData(
            eligible=eligible,
            is_grace_period=is_grace,
            lead_time_hours=lead_hours,
            required_notice_hours=notice_hours,
            grace_period_hours=grace_period_hours,
            min_required_hours=min_required_hours,
            airport_timezone=tz_str,
            server_time=server_dt.isoformat(),
            scheduled_time=scheduled_dt.isoformat(),
            message=msg,
        )

    @classmethod
    def create_enquiry(
        cls,
        db: Session,
        payload: LoungeEnquiryCreate,
        client_ip: Optional[str] = None,
    ) -> Tuple[LoungeEnquiry, bool]:
        """
        Validates, deduplicates, persists and dispatches notifications for a Lounge enquiry.
        Never marks an enquiry as confirmed booking (always PENDING).
        Returns: (enquiry_record, is_duplicate)
        """
        # Validate customer email against placeholder / disposable domains
        email_str = payload.email_address.strip().lower()
        email_ok, email_reason = is_acceptable_customer_email(email_str)
        if not email_ok:
            detail = REAL_EMAIL_HELP if email_reason == "reserved_or_placeholder" else "Invalid passenger email address."
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)

        # Validate authoritative eligibility window
        eligibility = cls.check_eligibility(
            LoungeEligibilityCheckRequest(
                lounge_category=payload.lounge_category or "Domestic",
                travel_date=payload.travel_date,
                preferred_time=payload.preferred_time,
                airport=payload.airport,
            ),
            db=db,
        )
        if not eligibility.eligible:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=eligibility.message,
            )

        # Duplicate submission check within 10 minutes
        ten_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
        existing = db.scalar(
            select(LoungeEnquiry).where(
                LoungeEnquiry.email_address == email_str,
                LoungeEnquiry.airport == payload.airport.strip(),
                LoungeEnquiry.lounge_type == payload.lounge_type.strip(),
                LoungeEnquiry.travel_date == payload.travel_date.strip(),
                LoungeEnquiry.created_at >= ten_mins_ago,
            )
        )
        if existing:
            logger.info(
                "Duplicate Lounge enquiry detected for email %s within 10 minutes. Returning existing enquiry %s.",
                email_str,
                existing.enquiry_reference,
            )
            return existing, True

        # Generate unique enquiry reference
        max_attempts = 5
        ref = cls.generate_reference()
        for _ in range(max_attempts):
            collision = db.scalar(select(LoungeEnquiry).where(LoungeEnquiry.enquiry_reference == ref))
            if not collision:
                break
            ref = cls.generate_reference()

        now = datetime.now(timezone.utc)
        enquiry = LoungeEnquiry(
            id=uuid.uuid4(),
            enquiry_reference=ref,
            airport=payload.airport.strip(),
            lounge_category=(payload.lounge_category or "Domestic").strip().capitalize(),
            lounge_type=payload.lounge_type.strip(),
            travel_date=payload.travel_date.strip(),
            preferred_time=payload.preferred_time.strip(),
            number_of_passengers=payload.number_of_passengers,
            flight_number=payload.flight_number.strip().upper() if payload.flight_number else None,
            full_name=payload.full_name.strip(),
            mobile_number=payload.mobile_number.strip(),
            email_address=email_str,
            whatsapp_number=payload.whatsapp_number.strip() if payload.whatsapp_number else None,
            additional_requirements=payload.additional_requirements.strip() if payload.additional_requirements else None,
            status=LoungeEnquiryStatus.PENDING,
            client_ip=client_ip,
            created_at=now,
            updated_at=now,
        )

        db.add(enquiry)
        db.commit()
        db.refresh(enquiry)

        # Notify configured administrators safely (notification failure must never lose enquiry)
        try:
            cls._notify_admin_lounge_enquiry(enquiry)
        except Exception:
            logger.exception(
                "Lounge enquiry %s saved successfully, but admin notification dispatch failed.",
                enquiry.enquiry_reference,
            )

        return enquiry, False

    @classmethod
    def _notify_admin_lounge_enquiry(cls, enquiry: LoungeEnquiry) -> None:
        """
        Dispatches admin notification using existing notification infrastructure.
        Catches all errors to ensure enquiry persistence is resilient.
        """
        context = {
            "booking_ref": enquiry.enquiry_reference,
            "passenger_name": enquiry.full_name,
            "passenger_email": enquiry.email_address,
            "passenger_phone": enquiry.mobile_number,
            "passenger_count": enquiry.number_of_passengers,
            "flight_num": enquiry.flight_number,
            "origin_code": enquiry.airport,
            "journey_type": "ENQUIRY",
            "service_category": "Lounge",
            "service_type": enquiry.lounge_type,
            "service_name": f"Lounge ({enquiry.lounge_type})",
            "departure_time": f"{enquiry.travel_date} ({enquiry.preferred_time})",
            "notes": enquiry.additional_requirements,
            "details": {
                "lounge_category": getattr(enquiry, "lounge_category", "Domestic"),
                "whatsapp_number": enquiry.whatsapp_number,
                "preferred_time": enquiry.preferred_time,
                "airport": enquiry.airport,
                "lounge_type": enquiry.lounge_type,
            },
        }
        try:
            from app.database import SessionLocal
            with SessionLocal() as db:
                NotificationService.notify_booking_created(db, context)
        except Exception as e:
            logger.warning("NotificationService dispatch for lounge failed: %s", e)

    @classmethod
    def get_by_reference(cls, db: Session, ref_or_id: str) -> Optional[LoungeEnquiry]:
        """Look up by enquiry reference or UUID"""
        try:
            parsed_uuid = uuid.UUID(ref_or_id)
            item = db.scalar(select(LoungeEnquiry).where(LoungeEnquiry.id == parsed_uuid))
            if item:
                return item
        except (ValueError, TypeError):
            pass

        return db.scalar(
            select(LoungeEnquiry).where(
                or_(
                    LoungeEnquiry.enquiry_reference == ref_or_id.strip(),
                    LoungeEnquiry.enquiry_reference == ref_or_id.strip().upper(),
                )
            )
        )

    @classmethod
    def list_enquiries(
        cls,
        db: Session,
        status: Optional[str] = None,
        airport: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 25,
    ) -> Tuple[List[LoungeEnquiry], int]:
        """List enquiries with filtering for admin portal"""
        stmt = select(LoungeEnquiry)

        if status and status.strip() and status.strip().upper() != "ALL":
            try:
                st_enum = LoungeEnquiryStatus(status.strip().upper())
                stmt = stmt.where(LoungeEnquiry.status == st_enum)
            except ValueError:
                pass

        if airport and airport.strip() and airport.strip().upper() != "ALL":
            stmt = stmt.where(LoungeEnquiry.airport.ilike(f"%{airport.strip()}%"))

        if search and search.strip():
            term = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    LoungeEnquiry.enquiry_reference.ilike(term),
                    LoungeEnquiry.full_name.ilike(term),
                    LoungeEnquiry.email_address.ilike(term),
                    LoungeEnquiry.mobile_number.ilike(term),
                    LoungeEnquiry.flight_number.ilike(term),
                )
            )

        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        results = db.scalars(
            stmt.order_by(desc(LoungeEnquiry.created_at)).offset(skip).limit(limit)
        ).all()

        return list(results), total

    @classmethod
    def update_enquiry(
        cls,
        db: Session,
        enquiry_id: str,
        payload: LoungeEnquiryAdminUpdate,
    ) -> Optional[LoungeEnquiry]:
        """Admin update of status, staff assignment, and internal notes"""
        enquiry = cls.get_by_reference(db, enquiry_id)
        if not enquiry:
            return None

        if payload.status is not None:
            enquiry.status = payload.status
        if payload.assigned_staff_id is not None:
            enquiry.assigned_staff_id = payload.assigned_staff_id
        if payload.assigned_staff_name is not None:
            enquiry.assigned_staff_name = payload.assigned_staff_name
        if payload.internal_notes is not None:
            enquiry.internal_notes = payload.internal_notes

        enquiry.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(enquiry)
        return enquiry

    @classmethod
    def get_options(cls, db: Session) -> LoungeOptionsData:
        """
        Fetches active airports from database and verified lounge configuration.
        Does not invent availability.
        """
        active_airports = db.scalars(
            select(SupportedAirport)
            .where(SupportedAirport.is_active.is_(True))
            .order_by(SupportedAirport.iata_code)
        ).all()

        airports_list: List[AirportOptionItem] = [
            AirportOptionItem(
                code=ap.iata_code,
                name=ap.airport_name,
                city=ap.city,
                country=ap.country,
                timezone=ap.timezone or AIRPORT_TIMEZONES.get(ap.iata_code, "Asia/Kolkata"),
            )
            for ap in active_airports
        ]

        server_now = datetime.now(timezone.utc).isoformat()

        return LoungeOptionsData(
            airports=airports_list,
            lounge_types=VERIFIED_LOUNGE_TYPES,
            domestic_lounge_types=DOMESTIC_LOUNGE_TYPES,
            international_lounge_types=INTERNATIONAL_LOUNGE_TYPES,
            preferred_times=VERIFIED_PREFERRED_TIMES,
            server_time=server_now,
            cutoff_rules=CUTOFF_RULES,
        )
