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
        notice_text="Domestic lounge enquiries require at least 12 hours' advance notice.",
    ),
    "International": CutoffRuleItem(
        notice_hours=24,
        grace_period_hours=1,
        notice_text="International lounge enquiries require at least 24 hours' advance notice.",
    ),
}

AIRPORT_TIMEZONES = {
    # India (Domestic & International Hubs)
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
    # Middle East
    "DXB": "Asia/Dubai",
    "DOH": "Asia/Qatar",
    "AUH": "Asia/Dubai",
    "RUH": "Asia/Riyadh",
    "JED": "Asia/Riyadh",
    # Europe
    "LHR": "Europe/London",
    "LGW": "Europe/London",
    "CDG": "Europe/Paris",
    "FRA": "Europe/Berlin",
    "AMS": "Europe/Amsterdam",
    # Asia-Pacific
    "SIN": "Asia/Singapore",
    "BKK": "Asia/Bangkok",
    "KUL": "Asia/Kuala_Lumpur",
    "HND": "Asia/Tokyo",
    "NRT": "Asia/Tokyo",
    "HKG": "Asia/Hong_Kong",
    "SYD": "Australia/Sydney",
    "MEL": "Australia/Melbourne",
    # North America
    "JFK": "America/New_York",
    "EWR": "America/New_York",
    "LAX": "America/Los_Angeles",
    "SFO": "America/Los_Angeles",
    "ORD": "America/Chicago",
}


def parse_travel_date_to_ymd(date_str: str) -> Tuple[int, int, int]:
    """Validate and parse YYYY-MM-DD into (year, month, day)."""
    raw = (date_str or "").strip()
    if not raw:
        raise ValueError("Travel date cannot be empty.")
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
        return dt.year, dt.month, dt.day
    except ValueError as e:
        raise ValueError(f"Invalid travel date '{date_str}': {e}. Expected YYYY-MM-DD format.")


def parse_preferred_time_to_hm(time_str: str) -> Tuple[int, int]:
    """
    Parses preferred time into (hour, minute).
    Supports named ranges (Morning, Afternoon, Evening, Night), 12h AM/PM, and 24h HH:MM.
    """
    raw = (time_str or "").strip()
    if not raw:
        raise ValueError("Preferred time cannot be empty.")

    raw_lower = raw.lower()
    if "morning" in raw_lower:
        return 6, 0
    if "afternoon" in raw_lower:
        return 12, 0
    if "evening" in raw_lower:
        return 18, 0
    if "night" in raw_lower:
        return 22, 0

    # 12-hour AM/PM: e.g. "8:30 AM" or "2:15 pm"
    m_12 = re.search(r"^(\d{1,2}):(\d{2})\s*(am|pm)$", raw_lower)
    if m_12:
        h = int(m_12.group(1))
        m = int(m_12.group(2))
        ampm = m_12.group(3)
        if not (1 <= h <= 12 and 0 <= m <= 59):
            raise ValueError(f"Invalid 12-hour time values in '{time_str}'.")
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return h, m

    # 24-hour HH:MM: e.g. "14:30" or "09:00"
    m_24 = re.search(r"^(\d{1,2}):(\d{2})$", raw)
    if m_24:
        h = int(m_24.group(1))
        m = int(m_24.group(2))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"Invalid 24-hour time values in '{time_str}' (hour must be 0-23, minute 0-59).")
        return h, m

    # Embedded HH:MM pattern fallback
    m_embedded = re.search(r"(\d{1,2}):(\d{2})", raw)
    if m_embedded:
        h = int(m_embedded.group(1))
        m = int(m_embedded.group(2))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m

    raise ValueError(
        f"Unrecognized preferred time format: '{time_str}'. "
        "Please select a standard time slot (Morning, Afternoon, Evening, Night) or specify HH:MM."
    )


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

        raw = airport_str.strip()
        # Direct IANA timezone check
        try:
            ZoneInfo(raw)
            return raw
        except Exception:
            pass

        # Look for 3-letter IATA code in parentheses or word boundary
        match = re.search(r"\b([A-Za-z]{3})\b", raw)
        code = match.group(1).upper() if match else raw[:3].upper()

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
        current_time: Optional[datetime] = None,
    ) -> LoungeEligibilityData:
        """
        Authoritative eligibility evaluation:
        - Domestic: 12h advance notice required; no grace period.
        - International: 24h advance notice required; no grace period.
        - Real-time calculation relative to server time in the airport's local timezone.
        - Boundary conditions handled:
            * server_time >= travel_datetime: in the past -> ineligible
            * server_time <= normal_cutoff: standard notice -> eligible, is_grace_period=False
            * Any time after normal_cutoff: ineligible; no grace period is allowed.
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

        # Current server time localized to airport timezone
        if current_time is None:
            server_utc = datetime.now(timezone.utc)
        elif current_time.tzinfo is None:
            server_utc = current_time.replace(tzinfo=timezone.utc)
        else:
            server_utc = current_time.astimezone(timezone.utc)

        server_dt_local = server_utc.astimezone(tz)
        server_datetime_str = server_dt_local.isoformat()

        notice_hours = 12 if category == "Domestic" else 24
        grace_period_hours = 1
        min_required_hours = notice_hours - grace_period_hours

        # 1. Parse and validate travel date
        try:
            year, month, day = parse_travel_date_to_ymd(payload.travel_date)
        except ValueError as err:
            return LoungeEligibilityData(
                eligible=False,
                is_grace_period=False,
                lounge_category=category,
                normal_cutoff_datetime=None,
                grace_period_end_datetime=None,
                server_datetime=server_datetime_str,
                travel_datetime=None,
                lead_time_hours=0.0,
                required_notice_hours=notice_hours,
                grace_period_hours=grace_period_hours,
                min_required_hours=min_required_hours,
                airport_timezone=tz_str,
                message=str(err),
                ineligible_reason=str(err),
                server_time=server_datetime_str,
                scheduled_time=payload.travel_date,
            )

        # 2. Parse and validate preferred time
        try:
            hour, minute = parse_preferred_time_to_hm(payload.preferred_time)
        except ValueError as err:
            return LoungeEligibilityData(
                eligible=False,
                is_grace_period=False,
                lounge_category=category,
                normal_cutoff_datetime=None,
                grace_period_end_datetime=None,
                server_datetime=server_datetime_str,
                travel_datetime=None,
                lead_time_hours=0.0,
                required_notice_hours=notice_hours,
                grace_period_hours=grace_period_hours,
                min_required_hours=min_required_hours,
                airport_timezone=tz_str,
                message=str(err),
                ineligible_reason=str(err),
                server_time=server_datetime_str,
                scheduled_time=payload.travel_date,
            )

        # 3. Construct localized travel datetime
        try:
            travel_dt = datetime(year, month, day, hour, minute, 0, tzinfo=tz)
        except Exception as exc:
            err_msg = f"Invalid travel date/time combination: {exc}"
            return LoungeEligibilityData(
                eligible=False,
                is_grace_period=False,
                lounge_category=category,
                normal_cutoff_datetime=None,
                grace_period_end_datetime=None,
                server_datetime=server_datetime_str,
                travel_datetime=None,
                lead_time_hours=0.0,
                required_notice_hours=notice_hours,
                grace_period_hours=grace_period_hours,
                min_required_hours=min_required_hours,
                airport_timezone=tz_str,
                message=err_msg,
                ineligible_reason=err_msg,
                server_time=server_datetime_str,
                scheduled_time=payload.travel_date,
            )

        travel_datetime_str = travel_dt.isoformat()

        # 4. Calculate cutoff datetimes in airport local timezone
        normal_cutoff_dt = travel_dt - timedelta(hours=notice_hours)
        grace_period_end_dt = normal_cutoff_dt + timedelta(hours=grace_period_hours)
        normal_cutoff_str = normal_cutoff_dt.isoformat()
        grace_period_end_str = grace_period_end_dt.isoformat()

        # 5. Lead time calculation
        lead_seconds = (travel_dt - server_dt_local).total_seconds()
        lead_hours = round(lead_seconds / 3600.0, 2)

        # 6. Authoritative cutoff evaluation with exact boundary conditions
        if server_dt_local >= travel_dt:
            eligible = False
            is_grace = False
            ineligible_reason = "Selected travel date and time is in the past."
            msg = "The selected travel date and time is in the past. Please select an upcoming date and time."
        elif server_dt_local <= normal_cutoff_dt:
            eligible = True
            is_grace = False
            ineligible_reason = None
            msg = f"Standard notice period met ({lead_hours:.1f}h notice in airport local timezone: {tz_str})."
        elif server_dt_local <= grace_period_end_dt:
            eligible = True
            is_grace = True
            ineligible_reason = None
            msg = f"Standard notice period met ({lead_hours:.1f}h notice in airport local timezone: {tz_str})."
        else:
            eligible = False
            is_grace = False
            ineligible_reason = (
                f"Cutoff window expired. {category} lounge enquiries require at least {notice_hours} hours advance notice."
            )
            msg = (
                f"Selected time is outside the allowed enquiry window. {category} lounge enquiries "
                f"require at least {notice_hours} hours' advance notice. "
                f"Current lead time is {max(0.0, lead_hours):.1f} hours."
            )

        return LoungeEligibilityData(
            eligible=eligible,
            is_grace_period=is_grace,
            lounge_category=category,
            normal_cutoff_datetime=normal_cutoff_str,
            grace_period_end_datetime=grace_period_end_str,
            server_datetime=server_datetime_str,
            travel_datetime=travel_datetime_str,
            lead_time_hours=lead_hours,
            required_notice_hours=notice_hours,
            grace_period_hours=grace_period_hours,
            min_required_hours=min_required_hours,
            airport_timezone=tz_str,
            message=msg,
            ineligible_reason=ineligible_reason,
            server_time=server_datetime_str,
            scheduled_time=travel_datetime_str,
        )

    @classmethod
    def create_enquiry(
        cls,
        db: Session,
        payload: LoungeEnquiryCreate,
        client_ip: Optional[str] = None,
        current_time: Optional[datetime] = None,
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

        # Validate authoritative eligibility window on server (never trusting client time)
        eligibility = cls.check_eligibility(
            LoungeEligibilityCheckRequest(
                lounge_category=payload.lounge_category or "Domestic",
                travel_date=payload.travel_date,
                preferred_time=payload.preferred_time,
                airport=payload.airport,
            ),
            db=db,
            current_time=current_time,
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
                # A lounge enquiry is a quotation request; acknowledge it to the guest
                # without ever claiming a confirmed booking.
                NotificationService.send_customer_whatsapp_enquiry_acknowledgement(db, {
                    **context,
                    "status": "PENDING",
                })
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
        Fetches the Lounge Service airport search list from data/airports.csv.
        Lounge configuration and enquiry persistence remain unchanged.
        """
        from app.flight.csv_airports import load_global_airports

        csv_airports, _large_airports, _by_code = load_global_airports()
        airports_list: List[AirportOptionItem] = [
            AirportOptionItem(
                code=airport["code"],
                name=airport["name"],
                city=airport["city"],
                country=airport["country"],
                timezone=AIRPORT_TIMEZONES.get(airport["code"], "Asia/Kolkata"),
            )
            for airport in sorted(csv_airports, key=lambda item: (item["code"], item["name"]))
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
