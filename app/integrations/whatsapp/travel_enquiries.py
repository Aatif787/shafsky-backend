"""Field definitions for the WhatsApp Travel Services enquiry flows.

Every step defined here is a field of an enquiry form that already exists on the
Shafsky Aviation website. The WhatsApp bot collects those same fields one step at a
time and persists the enquiry through the same backend service the website uses
(``BookingService.create_service_enquiry`` — the handler behind
``POST /api/bookings/enquiries``). No new form field is invented here.

Reference implementations (read-only; this module never modifies them):

* Visa Assistance —
  ``shafsky-frontend-main/src/components/booking/workflows/visa/VisaWorkflow.tsx``
  with ``TravelPlanningStep`` (destination / passport / residence / behalf / purpose /
  travel date / applicant counters), ``ApplicantCard`` (first name, last name,
  passport number, passport expiry, date of birth) and the lead-contact block.
* Flight Ticketing —
  ``shafsky-frontend-main/src/components/booking/workflows/ticketing/``
  (``TicketingJourney`` itinerary, ``TicketingPassenger`` manifest and lead contact)
  with the enquiry mapping in ``shafsky-frontend-main/src/lib/bookings/core.ts``.

Validation rules deliberately mirror the website: names reject digits, phone numbers
reject letters, emails use the shared customer-email validator, dates must be future
dates, a return date can never precede the departure date, and passenger counters use
the same limits as the website inputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.utils.customer_email import (
    EMAIL_FORMAT_HELP,
    REAL_EMAIL_HELP,
    is_acceptable_customer_email,
)

VISA = "visa"
TICKETING = "ticketing"

#: The only two options the Travel Services menu may offer.
TRAVEL_SERVICE_OPTIONS: Tuple[Tuple[str, str], ...] = (
    (VISA, "Visa Assistance"),
    (TICKETING, "Flight Ticketing"),
)

SERVICE_TITLES: Dict[str, str] = {
    VISA: "Visa Assistance",
    TICKETING: "Flight Ticketing",
}

#: Website enquiry category for both forms (see lib/bookings/core.ts).
ENQUIRY_SERVICE_CATEGORY = "Travel Support"

#: Shafsky operates on IST; the website's date pickers are local dates.
_IST = timezone(timedelta(hours=5, minutes=30))

# Every validator shares one signature so it can be registered in _VALIDATORS below;
# each one uses only the arguments it needs.
# pylint: disable=unused-argument
_SKIP_WORDS = {"skip", "none", "n/a", "na", "-", "no", "nil"}


def _today() -> date:
    return datetime.now(_IST).date()


# ---------------------------------------------------------------------------
# Step model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One form field, asked as one WhatsApp message."""

    key: str
    prompt: str
    validator: str = "text"
    options: Tuple[Tuple[str, str], ...] = ()
    optional: bool = False
    max_length: int = 120
    skip_value: str = ""


def option_lines(step: Step) -> str:
    """Numbered option list for a choice step."""
    return "\n".join(f"{i}. {label}" for i, (_value, label) in enumerate(step.options, 1))


# ---------------------------------------------------------------------------
# Validators — (ok, value, error)
# ---------------------------------------------------------------------------

_LETTERS_ONLY = re.compile(r"^[A-Za-z][A-Za-z\s.'-]*$")
_CONTACT_NAME = re.compile(r"^[A-Za-z][A-Za-z\s]*$")
_COUNTRY_NAME = re.compile(r"^[A-Za-z][A-Za-z\s.'()\-/&,]*$")
_PASSPORT_NUMBER = re.compile(r"^[A-Z0-9]{5,20}$")

_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d")
_MONTH_FORMATS = ("%m/%Y", "%m-%Y", "%Y-%m", "%B %Y", "%b %Y")

_COUNT_LIMITS: Dict[str, Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]] = {
    # adults, children, infants — identical to the website counters
    VISA: ((1, 10), (0, 10), (0, 10)),
    TICKETING: ((1, 9), (0, 9), (0, 4)),
}


def _parse_date(raw: str) -> Optional[date]:
    clean = (raw or "").strip()
    if not clean:
        return None
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(clean, fmt)
        except (TypeError, ValueError):
            continue
        if 1900 <= parsed.year <= 2100:
            return parsed.date()
    return None


def _v_text(step, raw, answers, service, conv_phone):
    value = " ".join((raw or "").split())
    if len(value) < 2:
        return False, None, "Please reply with at least two characters."
    if len(value) > step.max_length:
        return False, None, f"Please keep this reply under {step.max_length} characters."
    return True, value, None


def _v_person_name(step, raw, answers, service, conv_phone):
    value = " ".join((raw or "").split())
    if len(value) > step.max_length:
        return False, None, f"Please keep this name under {step.max_length} characters."
    if not _LETTERS_ONLY.match(value):
        return False, None, "Please use letters only for the name (spaces, hyphens and apostrophes are fine)."
    return True, value, None


def _v_contact_name(step, raw, answers, service, conv_phone):
    value = " ".join((raw or "").split())
    if not _CONTACT_NAME.match(value):
        return False, None, "Please enter the name using letters and spaces only."
    if len(value) > 120:
        return False, None, "Please keep this name under 120 characters."
    return True, value, None


def _v_country(step, raw, answers, service, conv_phone):
    value = " ".join((raw or "").split())
    if not _COUNTRY_NAME.match(value) or len(value) > 80:
        return False, None, "Please reply with the country name in letters (e.g. *United Arab Emirates*)."
    return True, value, None


def _v_email(step, raw, answers, service, conv_phone):
    clean = (raw or "").strip()
    ok, reason = is_acceptable_customer_email(clean)
    if not ok:
        return False, None, EMAIL_FORMAT_HELP if reason == "invalid_syntax" else REAL_EMAIL_HELP
    return True, clean, None


def _v_phone(step, raw, answers, service, conv_phone):
    clean = (raw or "").strip()
    if clean.lower() in {"same", "same number", "this", "my number", "yes", "ok"}:
        digits = re.sub(r"\D", "", conv_phone or "")
        if len(digits) < 7:
            return False, None, "Please enter your contact phone number with country code."
        return True, digits, None
    if re.search(r"[A-Za-z]", clean):
        return False, None, "Phone numbers cannot contain letters. Please use digits only."
    digits = re.sub(r"\D", "", clean)
    if not 7 <= len(digits) <= 15:
        return False, None, "Please enter a valid phone number with 7 to 15 digits (e.g. *+91 98765 43210*)."
    return True, ("+" + digits) if clean.startswith("+") else digits, None


def _v_date(step, raw, answers, service, conv_phone):
    parsed = _parse_date(raw)
    if parsed is None:
        return False, None, "Please enter the date as *DD/MM/YYYY* (e.g. *25/12/2026*)."
    if parsed < _today():
        return False, None, "That date has already passed. Please send a future date as *DD/MM/YYYY*."
    return True, parsed.isoformat(), None


def _v_return_date(step, raw, answers, service, conv_phone):
    parsed = _parse_date(raw)
    if parsed is None:
        return False, None, "Please enter the return date as *DD/MM/YYYY* (e.g. *05/01/2027*)."
    depart_iso = answers.get("tkt_depart_date") or ""
    try:
        depart = datetime.strptime(depart_iso, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        depart = _today()
    if parsed < depart:
        return (
            False,
            None,
            f"The return date cannot be earlier than your departure date (*{depart.strftime('%d/%m/%Y')}*). "
            "Please send a valid return date.",
        )
    return True, parsed.isoformat(), None


def _v_past_date(step, raw, answers, service, conv_phone):
    parsed = _parse_date(raw)
    if parsed is None:
        return False, None, "Please enter the date as *DD/MM/YYYY* (e.g. *01/01/1990*)."
    if parsed >= _today():
        return False, None, "That date is in the future. Please send a date of birth in the past."
    return True, parsed.isoformat(), None


def _v_month(step, raw, answers, service, conv_phone):
    clean = " ".join((raw or "").split())
    for fmt in _MONTH_FORMATS:
        try:
            parsed = datetime.strptime(clean, fmt)
        except (TypeError, ValueError):
            continue
        if not 1900 <= parsed.year <= 2100:
            break
        value = f"{parsed.year:04d}-{parsed.month:02d}"
        now = _today()
        if (parsed.year, parsed.month) < (now.year, now.month):
            return False, None, "That month has already passed. Please send a future month as *MM/YYYY*."
        return True, value, None
    return False, None, "Please enter the travel month as *MM/YYYY* (e.g. *03/2027*)."


def _v_counts(step, raw, answers, service, conv_phone):
    numbers = [int(n) for n in re.findall(r"\d+", raw or "")]
    if not numbers:
        return False, None, "Please reply with the number of travellers, e.g. *2 0 0* for 2 adults, 0 children, 0 infants."
    if len(numbers) == 1:
        adults, children, infants = numbers[0], 0, 0
    elif len(numbers) == 3:
        adults, children, infants = numbers
    else:
        return False, None, "Please reply with adults, children and infants — e.g. *2 0 0*."
    limits = _COUNT_LIMITS.get(service, _COUNT_LIMITS[TICKETING])
    for label, value, (low, high) in (
        ("adults", adults, limits[0]),
        ("children", children, limits[1]),
        ("infants", infants, limits[2]),
    ):
        if not low <= value <= high:
            return (
                False,
                None,
                f"{label.title()} must be between {low} and {high}. "
                "Please reply with adults, children and infants — e.g. *2 0 0*.",
            )
    return True, {"adults": adults, "children": children, "infants": infants}, None


def _v_choice(step, raw, answers, service, conv_phone):
    clean = " ".join((raw or "").split()).lower()
    for index, (value, label) in enumerate(step.options, 1):
        if clean in {str(index), value.lower(), label.lower()}:
            return True, value, None
    if len(clean) >= 4:
        for value, label in step.options:
            if clean in label.lower():
                return True, value, None
    return False, None, "Please reply with the number of your choice."


def _v_passport(step, raw, answers, service, conv_phone):
    value = re.sub(r"\s+", "", (raw or "")).upper()
    if not _PASSPORT_NUMBER.match(value):
        return False, None, "Please send a valid passport number using 5-20 letters or digits (e.g. *A1234567*)."
    return True, value, None


_VALIDATORS: Dict[str, Callable[[Step, str, Dict[str, Any], str, str], Tuple[bool, Any, Optional[str]]]] = {
    "text": _v_text,
    "person_name": _v_person_name,
    "contact_name": _v_contact_name,
    "country": _v_country,
    "email": _v_email,
    "phone": _v_phone,
    "date": _v_date,
    "return_date": _v_return_date,
    "past_date": _v_past_date,
    "month": _v_month,
    "counts": _v_counts,
    "choice": _v_choice,
    "passport": _v_passport,
}


def validate_answer(
    step: Step,
    raw: str,
    answers: Dict[str, Any],
    service: str,
    conv_phone: str = "",
) -> Tuple[bool, Any, Optional[str]]:
    """Validate one reply for one step. Values returned are JSON-safe."""
    text = (raw or "").strip()
    if step.optional and text.lower() in _SKIP_WORDS:
        return True, step.skip_value, None
    validator = _VALIDATORS.get(step.validator, _v_text)
    return validator(step, text, answers, service, conv_phone)


# ---------------------------------------------------------------------------
# Visa Assistance steps — mirrors VisaWorkflow.tsx
# ---------------------------------------------------------------------------

_VISA_DESTINATION = Step(
    key="visa_destination",
    prompt="Which country are you applying to visit?\n\nExample: *United Arab Emirates*",
    validator="country",
    max_length=80,
)

_VISA_PASSPORT = Step(
    key="visa_passport",
    prompt="Which country issued your passport?\n\nExample: *India*",
    validator="country",
    max_length=80,
)

_VISA_RESIDENCE = Step(
    key="visa_residence",
    prompt="Which country do you currently reside in?\n\nType *Skip* if it is the same as your passport country.",
    validator="country",
    optional=True,
    max_length=80,
)

_VISA_BEHALF = Step(
    key="visa_behalf",
    prompt="Who is this visa request for?",
    validator="choice",
    options=(("self", "Myself (individual)"), ("family", "Family group"), ("corporate", "Corporate delegation")),
)

_VISA_COMPANY = Step(
    key="visa_company",
    prompt="Company or organisation name?\n\nType *Skip* to leave it blank.",
    validator="text",
    optional=True,
    max_length=160,
)

_VISA_PURPOSE = Step(
    key="visa_purpose",
    prompt="What is the purpose of the trip?",
    validator="choice",
    options=(
        ("tourism", "Holiday & tourism"),
        ("business", "Business meeting"),
        ("family_visit", "Family visit"),
        ("transit", "Transit / layover"),
        ("student", "Study & course"),
        ("medical", "Medical treatment"),
        ("other", "Other / not sure"),
    ),
)

_VISA_DATE_MODE = Step(
    key="visa_date_mode",
    prompt="Do you already have a fixed travel date?",
    validator="choice",
    options=(("fixed", "Yes — I have a date"), ("tentative", "Not yet — a tentative month")),
)

_VISA_TRAVEL_DATE = Step(
    key="visa_travel_date",
    prompt="Please enter your intended travel date in *DD/MM/YYYY* format (e.g. *25/12/2026*).",
    validator="date",
)

_VISA_TENTATIVE_MONTH = Step(
    key="visa_tentative_month",
    prompt="Which month do you plan to travel?\n\nReply as *MM/YYYY* (e.g. *03/2027*).",
    validator="month",
)

_VISA_TRAVELLERS = Step(
    key="visa_travellers",
    prompt=(
        "How many applicants are travelling?\n\n"
        "Reply with adults, children and infants — e.g. *2 0 0*\n"
        "• Adults: 12+ years\n"
        "• Children: 2–11 years\n"
        "• Infants: under 2 years"
    ),
    validator="counts",
)

_VISA_CONTACT_NAME = Step(
    key="visa_contact_name",
    prompt="Full name of the lead contact for this application:",
    validator="contact_name",
)

_VISA_CONTACT_EMAIL = Step(
    key="visa_contact_email",
    prompt="Email address for the visa desk's quotation and updates:",
    validator="email",
    max_length=200,
)

_VISA_CONTACT_PHONE = Step(
    key="visa_contact_phone",
    prompt="Contact phone number (or type *Same* to use this WhatsApp number):",
    validator="phone",
)

_VISA_NOTES = Step(
    key="visa_notes",
    prompt="Any special consular notes for our visa desk?\n\nType *None* if there are none.",
    validator="text",
    optional=True,
    max_length=1500,
)

_VISA_APPLICANT_FIELDS: Tuple[Tuple[str, str, str, bool], ...] = (
    ("first_name", "first name (as printed on the passport)", "person_name", False),
    ("last_name", "last name", "person_name", False),
    ("passport_number", "passport number (e.g. *A1234567*)", "passport", False),
    ("passport_expiry", "passport expiry date (*DD/MM/YYYY*), or type *Skip*", "date", True),
    ("date_of_birth", "date of birth (*DD/MM/YYYY*), or type *Skip*", "past_date", True),
)


def _visa_applicant_step(index: int, total: int, field_name: str, label: str, validator: str, optional: bool) -> Step:
    return Step(
        key=f"visa_applicant_{index}_{field_name}",
        prompt=f"Traveller {index} of {total} — {label}:",
        validator=validator,
        optional=optional,
        max_length=80 if field_name != "passport_number" else 20,
    )


def _counts(answers: Dict[str, Any], key: str) -> Dict[str, int]:
    raw = answers.get(key)
    if not isinstance(raw, dict):
        return {"adults": 0, "children": 0, "infants": 0}
    out = {}
    for name in ("adults", "children", "infants"):
        try:
            out[name] = int(raw.get(name) or 0)
        except (TypeError, ValueError):
            out[name] = 0
    return out


def applicant_count(answers: Dict[str, Any]) -> int:
    counts = _counts(answers, "visa_travellers")
    return max(0, counts["adults"] + counts["children"] + counts["infants"])


def passenger_count(answers: Dict[str, Any]) -> int:
    counts = _counts(answers, "tkt_travellers")
    return max(0, counts["adults"] + counts["children"] + counts["infants"])


def traveller_total(service: str, answers: Dict[str, Any]) -> int:
    return applicant_count(answers) if service == VISA else passenger_count(answers)


def _plan_visa(answers: Dict[str, Any]) -> List[Step]:
    steps: List[Step] = [_VISA_DESTINATION, _VISA_PASSPORT, _VISA_RESIDENCE, _VISA_BEHALF]
    if answers.get("visa_behalf") == "corporate":
        steps.append(_VISA_COMPANY)
    steps.extend([_VISA_PURPOSE, _VISA_DATE_MODE])
    if answers.get("visa_date_mode") == "tentative":
        steps.append(_VISA_TENTATIVE_MONTH)
    else:
        steps.append(_VISA_TRAVEL_DATE)
    steps.append(_VISA_TRAVELLERS)

    total = applicant_count(answers)
    for index in range(1, total + 1):
        for field_name, label, validator, optional in _VISA_APPLICANT_FIELDS:
            steps.append(_visa_applicant_step(index, total, field_name, label, validator, optional))

    steps.extend([_VISA_CONTACT_NAME, _VISA_CONTACT_EMAIL, _VISA_CONTACT_PHONE, _VISA_NOTES])
    return steps


# ---------------------------------------------------------------------------
# Flight Ticketing steps — mirrors TicketingJourney.tsx / TicketingPassenger.tsx
# ---------------------------------------------------------------------------

_TKT_TRIP_TYPE = Step(
    key="tkt_trip_type",
    prompt="Is this a one-way or a return journey?",
    validator="choice",
    options=(("one_way", "One-way"), ("round_trip", "Return")),
)

_TKT_FROM = Step(
    key="tkt_from",
    prompt="Departure airport or city?\n\nExample: *DEL*, *Delhi*, *Dubai*",
    validator="text",
    max_length=60,
)

_TKT_TO = Step(
    key="tkt_to",
    prompt="Arrival airport or city?\n\nExample: *LHR*, *London*",
    validator="text",
    max_length=60,
)

_TKT_DEPART_DATE = Step(
    key="tkt_depart_date",
    prompt="Departure date in *DD/MM/YYYY* format (e.g. *25/12/2026*):",
    validator="date",
)

_TKT_CABIN = Step(
    key="tkt_cabin",
    prompt="Which cabin class?",
    validator="choice",
    options=(
        ("First Class Suite", "First class suite"),
        ("Business Class Lie-Flat", "Business class lie-flat"),
        ("Premium Economy", "Premium economy"),
        ("Economy Class", "Economy class"),
    ),
)

_TKT_TRAVELLERS = Step(
    key="tkt_travellers",
    prompt=(
        "How many passengers are travelling?\n\n"
        "Reply with adults, children and infants — e.g. *2 0 0*\n"
        "• Adults: 12+ years (1–9)\n"
        "• Children: 2–11 years (0–9)\n"
        "• Infants: under 2 years (0–4)"
    ),
    validator="counts",
)

_TKT_ALLIANCE = Step(
    key="tkt_alliance",
    prompt=(
        "Preferred airline or alliance?\n\n"
        "Type *Skip* for no preference."
    ),
    validator="choice",
    optional=True,
    skip_value="Any Alliance",
    options=(
        ("Any Alliance", "Any premier airline"),
        ("Indian Carriers", "All Indian carriers"),
        ("Air India", "Air India / Air India Express"),
        ("IndiGo", "IndiGo"),
        ("Vistara", "Vistara / Akasa Air"),
        ("Star Alliance", "Star Alliance"),
        ("Oneworld", "Oneworld"),
        ("SkyTeam", "SkyTeam"),
    ),
)

_TKT_CONTACT_NAME = Step(
    key="tkt_contact_name",
    prompt="Lead contact full name:",
    validator="contact_name",
)

_TKT_CONTACT_EMAIL = Step(
    key="tkt_contact_email",
    prompt="Email address for the ticketing desk's quotation:",
    validator="email",
    max_length=200,
)

_TKT_CONTACT_PHONE = Step(
    key="tkt_contact_phone",
    prompt="Contact phone number (or type *Same* to use this WhatsApp number):",
    validator="phone",
)

_TKT_PAX_FIELDS: Tuple[Tuple[str, str, str, bool], ...] = (
    ("first_name", "first name (as printed on the passport)", "person_name", False),
    ("last_name", "last name", "person_name", False),
    ("gender", "gender", "choice", False),
    ("date_of_birth", "date of birth (*DD/MM/YYYY*)", "past_date", False),
    ("nationality", "nationality", "country", False),
)

_TKT_PASSPORT_FIELDS: Tuple[Tuple[str, str, str, bool], ...] = (
    ("passport_number", "passport number (e.g. *A1234567*)", "passport", False),
    ("passport_expiry", "passport expiry date (*DD/MM/YYYY*)", "date", False),
    ("passport_country", "passport issuing country", "country", False),
)

_GENDER_OPTIONS: Tuple[Tuple[str, str], ...] = (("Male", "Male"), ("Female", "Female"), ("Other", "Other"))

#: Exact list used by ``detectItineraryType`` in TicketingPassenger.tsx.
_INTL_MARKERS: Tuple[str, ...] = (
    "LHR", "LONDON",
    "DXB", "DUBAI",
    "SIN", "SINGAPORE",
    "JFK", "NEW YORK",
    "CDG", "PARIS",
    "DOH", "DOHA",
    "FRA", "FRANKFURT",
    "SYD", "SYDNEY",
    "HND", "TOKYO",
    "AUH", "ABU DHABI",
    "BKK", "BANGKOK",
    "HKG", "HONG KONG",
    "KUL", "KUALA LUMPUR",
    "ZRH", "ZURICH",
    "IST", "ISTANBUL",
    "UK", "USA", "UAE", "QATAR",
)


def _country_code(value: Optional[str]) -> str:
    upper = (value or "").upper()
    return "INTL" if any(marker in upper for marker in _INTL_MARKERS) else "IN"


def detect_itinerary_type(from_airport: Optional[str], to_airport: Optional[str]) -> str:
    """Port of ``detectItineraryType`` from the website ticketing form."""
    if not from_airport or not to_airport:
        return "DOMESTIC"
    if _country_code(from_airport) == "IN" and _country_code(to_airport) == "IN":
        return "DOMESTIC"
    return "INTERNATIONAL"


def itinerary_type(answers: Dict[str, Any]) -> str:
    return detect_itinerary_type(answers.get("tkt_from"), answers.get("tkt_to"))


def _tkt_pax_step(index: int, total: int, field_name: str, label: str, validator: str, optional: bool) -> Step:
    return Step(
        key=f"tkt_pax_{index}_{field_name}",
        prompt=f"Passenger {index} of {total} — {label}:",
        validator=validator,
        options=_GENDER_OPTIONS if validator == "choice" else (),
        optional=optional,
        max_length=80 if field_name != "passport_number" else 20,
    )


def _plan_ticketing(answers: Dict[str, Any]) -> List[Step]:
    steps: List[Step] = [_TKT_TRIP_TYPE, _TKT_FROM, _TKT_TO, _TKT_DEPART_DATE]

    if answers.get("tkt_trip_type") == "round_trip":
        depart_iso = answers.get("tkt_depart_date") or ""
        try:
            depart_label = datetime.strptime(depart_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
        except (TypeError, ValueError):
            depart_label = "your departure date"
        steps.append(
            Step(
                key="tkt_return_date",
                prompt=(
                    "Return date in *DD/MM/YYYY* format (e.g. *05/01/2027*).\n\n"
                    f"It cannot be earlier than your departure on *{depart_label}*."
                ),
                validator="return_date",
            )
        )

    steps.extend([_TKT_CABIN, _TKT_TRAVELLERS, _TKT_ALLIANCE])

    total = passenger_count(answers)
    international = itinerary_type(answers) == "INTERNATIONAL"
    for index in range(1, total + 1):
        for field_name, label, validator, optional in _TKT_PAX_FIELDS:
            steps.append(_tkt_pax_step(index, total, field_name, label, validator, optional))
        if international:
            for field_name, label, validator, optional in _TKT_PASSPORT_FIELDS:
                steps.append(_tkt_pax_step(index, total, field_name, label, validator, optional))

    steps.extend([_TKT_CONTACT_NAME, _TKT_CONTACT_EMAIL, _TKT_CONTACT_PHONE])
    return steps


def plan(service: str, answers: Dict[str, Any]) -> List[Step]:
    """Ordered steps for the current answers (recomputed every turn)."""
    if service == VISA:
        return _plan_visa(answers)
    if service == TICKETING:
        return _plan_ticketing(answers)
    return []


# ---------------------------------------------------------------------------
# Review + submission mapping
# ---------------------------------------------------------------------------

_PURPOSE_LABELS = dict(_VISA_PURPOSE.options)
_BEHALF_LABELS = dict(_VISA_BEHALF.options)
_CABIN_LABELS = dict(_TKT_CABIN.options)
_ALLIANCE_LABELS = dict(_TKT_ALLIANCE.options)


def _fmt_date(iso: Optional[str]) -> str:
    try:
        return datetime.strptime(str(iso), "%Y-%m-%d").strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return str(iso or "—")


def _fmt_month(value: Optional[str]) -> str:
    try:
        return datetime.strptime(str(value), "%Y-%m").strftime("%B %Y")
    except (TypeError, ValueError):
        return str(value or "—")


def _visa_applicants(answers: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for index in range(1, applicant_count(answers) + 1):
        out.append(
            {
                "first_name": str(answers.get(f"visa_applicant_{index}_first_name") or ""),
                "last_name": str(answers.get(f"visa_applicant_{index}_last_name") or ""),
                "passport_number": str(answers.get(f"visa_applicant_{index}_passport_number") or ""),
                "passport_expiry": str(answers.get(f"visa_applicant_{index}_passport_expiry") or ""),
                "date_of_birth": str(answers.get(f"visa_applicant_{index}_date_of_birth") or ""),
            }
        )
    return out


def _tkt_passengers(answers: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for index in range(1, passenger_count(answers) + 1):
        out.append(
            {
                "first_name": str(answers.get(f"tkt_pax_{index}_first_name") or ""),
                "last_name": str(answers.get(f"tkt_pax_{index}_last_name") or ""),
                "gender": str(answers.get(f"tkt_pax_{index}_gender") or ""),
                "date_of_birth": str(answers.get(f"tkt_pax_{index}_date_of_birth") or ""),
                "nationality": str(answers.get(f"tkt_pax_{index}_nationality") or ""),
                "passport_number": str(answers.get(f"tkt_pax_{index}_passport_number") or ""),
                "passport_expiry": str(answers.get(f"tkt_pax_{index}_passport_expiry") or ""),
                "passport_issuing_country": str(answers.get(f"tkt_pax_{index}_passport_country") or ""),
            }
        )
    return out


def _review_visa(answers: Dict[str, Any]) -> str:
    counts = _counts(answers, "visa_travellers")
    behalf = str(answers.get("visa_behalf") or "")
    date_mode = str(answers.get("visa_date_mode") or "fixed")
    lines = [
        "📋 *VISA ASSISTANCE — PLEASE REVIEW*",
        "",
        f"• *Destination*: {answers.get('visa_destination') or '—'}",
        f"• *Passport issued by*: {answers.get('visa_passport') or '—'}",
        f"• *Residence*: {answers.get('visa_residence') or '—'}",
        f"• *Request for*: {_BEHALF_LABELS.get(behalf, behalf or '—')}",
    ]
    if behalf == "corporate":
        lines.append(f"• *Company*: {answers.get('visa_company') or '—'}")
    lines.append(f"• *Purpose*: {_PURPOSE_LABELS.get(str(answers.get('visa_purpose') or ''), '—')}")
    if date_mode == "tentative":
        lines.append(f"• *Travel month*: {_fmt_month(answers.get('visa_tentative_month'))}")
    else:
        lines.append(f"• *Travel date*: {_fmt_date(answers.get('visa_travel_date'))}")
    lines.append(
        f"• *Applicants*: {counts['adults']} adult(s), {counts['children']} child(ren), {counts['infants']} infant(s)"
    )
    lines.append("")
    lines.append("*Travellers*")
    for index, applicant in enumerate(_visa_applicants(answers), 1):
        name = f"{applicant['first_name']} {applicant['last_name']}".strip()
        detail = [f"passport {applicant['passport_number'] or '—'}"]
        if applicant["passport_expiry"]:
            detail.append(f"expires {_fmt_date(applicant['passport_expiry'])}")
        if applicant["date_of_birth"]:
            detail.append(f"DOB {_fmt_date(applicant['date_of_birth'])}")
        lines.append(f"{index}. {name or 'Traveller'} — " + ", ".join(detail))
    lines.extend(
        [
            "",
            f"• *Contact*: {answers.get('visa_contact_name') or '—'}",
            f"• *Email*: {answers.get('visa_contact_email') or '—'}",
            f"• *Phone*: {answers.get('visa_contact_phone') or '—'}",
            f"• *Notes*: {answers.get('visa_notes') or 'None'}",
        ]
    )
    return "\n".join(lines)


def _review_ticketing(answers: Dict[str, Any]) -> str:
    counts = _counts(answers, "tkt_travellers")
    return_trip = answers.get("tkt_trip_type") == "round_trip"
    intent = itinerary_type(answers)
    lines = [
        "📋 *FLIGHT TICKETING — PLEASE REVIEW*",
        "",
        f"• *Journey*: {'Return' if return_trip else 'One-way'}",
        f"• *Route*: {answers.get('tkt_from') or '—'} → {answers.get('tkt_to') or '—'} ({intent.title()})",
        f"• *Departure*: {_fmt_date(answers.get('tkt_depart_date'))}",
    ]
    if return_trip:
        lines.append(f"• *Return*: {_fmt_date(answers.get('tkt_return_date'))}")
    lines.append(f"• *Cabin*: {_CABIN_LABELS.get(str(answers.get('tkt_cabin') or ''), '—')}")
    alliance = str(answers.get("tkt_alliance") or "Any Alliance")
    lines.append(f"• *Airline preference*: {_ALLIANCE_LABELS.get(alliance, alliance)}")
    lines.append(
        f"• *Passengers*: {counts['adults']} adult(s), {counts['children']} child(ren), {counts['infants']} infant(s)"
    )
    lines.append("")
    lines.append("*Travellers*")
    for index, pax in enumerate(_tkt_passengers(answers), 1):
        name = f"{pax['first_name']} {pax['last_name']}".strip()
        detail = [pax["gender"] or "—", _fmt_date(pax["date_of_birth"]), pax["nationality"] or "—"]
        if pax["passport_number"]:
            detail.append(f"passport {pax['passport_number']}")
        if pax["passport_expiry"]:
            detail.append(f"expires {_fmt_date(pax['passport_expiry'])}")
        if pax["passport_issuing_country"]:
            detail.append(f"issued by {pax['passport_issuing_country']}")
        lines.append(f"{index}. {name or 'Traveller'} — " + ", ".join(detail))
    lines.extend(
        [
            "",
            f"• *Contact*: {answers.get('tkt_contact_name') or '—'}",
            f"• *Email*: {answers.get('tkt_contact_email') or '—'}",
            f"• *Phone*: {answers.get('tkt_contact_phone') or '—'}",
        ]
    )
    return "\n".join(lines)


def build_review(service: str, answers: Dict[str, Any]) -> str:
    """Customer-facing summary before submission (Meta text limit is 4096)."""
    body = _review_visa(answers) if service == VISA else _review_ticketing(answers)
    if len(body) > 3500:
        body = body[:3480].rstrip() + "\n… (remaining travellers are on the submitted enquiry)"
    return body


def _visa_counts(answers: Dict[str, Any]) -> Dict[str, int]:
    return _counts(answers, "visa_travellers")


def build_submission(service: str, answers: Dict[str, Any]) -> Dict[str, Any]:
    """Keyword arguments for ``BookingService.create_service_enquiry``.

    The values mirror what the website forms post to ``/api/bookings/enquiries``
    (``serviceCategory: "Travel Support"``, ``serviceType`` per form, ``origin`` /
    ``destination`` / ``serviceDate`` / ``notes``), so the enquiry lands on the same
    admin desk with the same notification path.
    """
    if service == VISA:
        applicants = _visa_applicants(answers)
        counts = _visa_counts(answers)
        names = ", ".join(f"{a['first_name']} {a['last_name']}".strip() for a in applicants if a["first_name"])
        date_mode = str(answers.get("visa_date_mode") or "fixed")
        service_date = (
            str(answers.get("visa_tentative_month") or "")
            if date_mode == "tentative"
            else str(answers.get("visa_travel_date") or "")
        )
        notes = (
            f"Behalf: {str(answers.get('visa_behalf') or '').upper()} | "
            f"Purpose: {answers.get('visa_purpose') or ''} | "
            f"Applicants: {names} | "
            f"Notes: {answers.get('visa_notes') or ''}"
        )
        details = {
            "channel": "whatsapp",
            "passenger_count": len(applicants),
            "destination_country": answers.get("visa_destination"),
            "passport_country": answers.get("visa_passport"),
            "residence_country": answers.get("visa_residence") or None,
            "behalf_of": answers.get("visa_behalf"),
            "company": answers.get("visa_company") or "",
            "travel_purpose": answers.get("visa_purpose"),
            "date_mode": date_mode,
            "travel_date": answers.get("visa_travel_date") or None,
            "tentative_month": answers.get("visa_tentative_month") or None,
            # Mirrors VisaWorkflow.handleSubmit: pax_adults = applicants.length.
            "pax_adults": len(applicants),
            "pax_children": counts["children"],
            "pax_infants": counts["infants"],
            "applicants": applicants,
        }
        return {
            "passenger_name": str(answers.get("visa_contact_name") or ""),
            "passenger_email": str(answers.get("visa_contact_email") or ""),
            "passenger_phone": str(answers.get("visa_contact_phone") or ""),
            "service_category": ENQUIRY_SERVICE_CATEGORY,
            "service_type": SERVICE_TITLES[VISA],
            "origin": str(answers.get("visa_passport") or "") or None,
            "destination": str(answers.get("visa_destination") or "") or None,
            "service_date": service_date or None,
            "notes": notes,
            "details": details,
        }

    counts = _counts(answers, "tkt_travellers")
    passengers = _tkt_passengers(answers)
    names = ", ".join(f"{p['first_name']} {p['last_name']}".strip() for p in passengers if p["first_name"])
    return_trip = answers.get("tkt_trip_type") == "round_trip"
    alliance = str(answers.get("tkt_alliance") or "Any Alliance")
    notes = f"Alliance: {alliance}"
    if names:
        notes += f" | Passengers: {names}"
    details = {
        "channel": "whatsapp",
        "passenger_count": len(passengers),
        "trip_type": "round_trip" if return_trip else "one_way",
        "itinerary_type": itinerary_type(answers),
        "return_date": (answers.get("tkt_return_date") or None) if return_trip else None,
        "cabin_class": answers.get("tkt_cabin"),
        "preferred_alliance": alliance,
        "pax_adults": counts["adults"],
        "pax_children": counts["children"],
        "pax_infants": counts["infants"],
        "passengers": passengers,
    }
    return {
        "passenger_name": str(answers.get("tkt_contact_name") or ""),
        "passenger_email": str(answers.get("tkt_contact_email") or ""),
        "passenger_phone": str(answers.get("tkt_contact_phone") or ""),
        "service_category": ENQUIRY_SERVICE_CATEGORY,
        "service_type": SERVICE_TITLES[TICKETING],
        "origin": str(answers.get("tkt_from") or "") or None,
        "destination": str(answers.get("tkt_to") or "") or None,
        "service_date": str(answers.get("tkt_depart_date") or "") or None,
        "notes": notes,
        "details": details,
    }


def conversation_snapshot(service: str, answers: Dict[str, Any]) -> Dict[str, Any]:
    """Conversation columns kept in sync while the enquiry is collected."""
    total = traveller_total(service, answers)
    if service == VISA:
        date_mode = str(answers.get("visa_date_mode") or "fixed")
        label = (
            _fmt_month(answers.get("visa_tentative_month"))
            if date_mode == "tentative" and answers.get("visa_tentative_month")
            else (_fmt_date(answers.get("visa_travel_date")) if answers.get("visa_travel_date") else None)
        )
        return {
            "passenger_count": total or 1,
            "booking_date": label,
            "customer_name": answers.get("visa_contact_name"),
            "customer_email": answers.get("visa_contact_email"),
            "customer_phone": answers.get("visa_contact_phone"),
            "additional_requirements": answers.get("visa_notes"),
        }
    return {
        "passenger_count": total or 1,
        "booking_date": _fmt_date(answers.get("tkt_depart_date")) if answers.get("tkt_depart_date") else None,
        "customer_name": answers.get("tkt_contact_name"),
        "customer_email": answers.get("tkt_contact_email"),
        "customer_phone": answers.get("tkt_contact_phone"),
        "additional_requirements": None,
    }
