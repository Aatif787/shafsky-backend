"""
Extended carrier registries, flight schedule normalization helpers, and
status/search/telemetry mixin methods for AviationEdgeProvider.
"""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.flight.airports import build_flight_airport
from app.flight.duration import compute_flight_duration
from app.flight.exceptions import (
    FlightNotFoundException,
    InvalidFlightDateException,
    InvalidFlightNumberException,
)
from app.flight.schemas import (
    AircraftDetails,
    AirlineDetails,
    DurationDetails,
    FlightInfo,
    FlightStatusData,
    FlightTelemetry,
    LocationEndpointDetails,
)


def _ae():
    import app.flight.providers.aviation_edge_provider as ae_mod
    return ae_mod


# Master Carrier Registry (IATA -> Name & ICAO Code Mapping)
CARRIER_ICAO_MAP: Dict[str, str] = {
    "AI": "AIC",  # Air India
    "6E": "IGO",  # IndiGo
    "SG": "SEJ",  # SpiceJet
    "QP": "AKJ",  # Akasa Air
    "IX": "AXB",  # Air India Express
    "UK": "VTI",  # Vistara
    "I5": "IAD",  # AirAsia India / AIX Connect
    "9I": "LLR",  # Alliance Air
    "S5": "RSL",  # Star Air
    "EK": "UAE",  # Emirates
    "QR": "QTR",  # Qatar Airways
    "BA": "BAW",  # British Airways
    "EY": "ETD",  # Etihad Airways
    "LH": "DLH",  # Lufthansa
    "AF": "AFR",  # Air France
    "KL": "KLM",  # KLM
    "SQ": "SIA",  # Singapore Airlines
    "CX": "CPA",  # Cathay Pacific
    "TK": "THY",  # Turkish Airlines
    "AA": "AAL",  # American Airlines
    "UA": "UAL",  # United Airlines
    "DL": "DAL",  # Delta Air Lines
    "SV": "SVA",  # Saudia
    "FZ": "FDB",  # Flydubai
    "J9": "JZR",  # Jazeera Airways
    "WY": "OMA",  # Oman Air
    "GF": "GFA",  # Gulf Air
    "MH": "MAS",  # Malaysia Airlines
    "TG": "THA",  # Thai Airways
    "VS": "VIR",  # Virgin Atlantic
    "AC": "ACA",  # Air Canada
    "QF": "QFA",  # Qantas
}

CARRIER_NAME_MAP: Dict[str, str] = {
    "AI": "Air India",
    "6E": "IndiGo",
    "SG": "SpiceJet",
    "QP": "Akasa Air",
    "IX": "Air India Express",
    "UK": "Vistara",
    "I5": "AirAsia India",
    "9I": "Alliance Air",
    "S5": "Star Air",
    "EK": "Emirates",
    "QR": "Qatar Airways",
    "BA": "British Airways",
    "EY": "Etihad Airways",
    "LH": "Lufthansa",
    "AF": "Air France",
    "KL": "KLM",
    "SQ": "Singapore Airlines",
    "CX": "Cathay Pacific",
    "TK": "Turkish Airlines",
    "AA": "American Airlines",
    "UA": "United Airlines",
    "DL": "Delta Air Lines",
    "SV": "Saudia",
    "FZ": "Flydubai",
    "J9": "Jazeera Airways",
    "WY": "Oman Air",
    "GF": "Gulf Air",
    "MH": "Malaysia Airlines",
    "TG": "Thai Airways",
    "VS": "Virgin Atlantic",
    "AC": "Air Canada",
    "QF": "Qantas",
}

_TIME_ONLY_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
_DATE_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?")


def _split_date_and_time(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """Split a provider schedule value into (date, HH:MM:SS). Either part may be absent."""
    text = str(value or "").strip().replace(" ", "T")
    if not text:
        return None, None

    dated = _DATE_TIME_RE.match(text)
    if dated:
        hour, minute, second = dated.group(2), dated.group(3), dated.group(4) or "00"
        return dated.group(1), f"{int(hour):02d}:{minute}:{second}"

    time_only = _TIME_ONLY_RE.match(text)
    if time_only:
        hour, minute, second = time_only.group(1), time_only.group(2), time_only.group(3) or "00"
        return None, f"{int(hour):02d}:{minute}:{second}"

    return None, None


def _retarget_schedule_dates(
    dep_value: Any,
    arr_value: Any,
    target_date: Optional[str],
) -> Tuple[Any, Any]:
    """
    Force provider schedule strings onto the requested travel date.

    Two provider quirks make this necessary:
      * `timetable` ignores the requested date and always answers with today's rows.
      * `flightsFuture` returns time-only values such as '09:30' with no date at all.

    The time of day is correct in both cases, so only the date is rewritten. The
    original departure-to-arrival day gap is preserved so red-eye flights keep
    landing on the following day.
    """
    if not target_date:
        return dep_value, arr_value

    dep_date, dep_time = _split_date_and_time(dep_value)
    arr_date, arr_time = _split_date_and_time(arr_value)
    if dep_time is None and arr_time is None:
        return dep_value, arr_value

    try:
        target = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return dep_value, arr_value

    day_offset = 0
    if dep_date and arr_date:
        try:
            day_offset = max(
                0,
                (
                    datetime.strptime(arr_date, "%Y-%m-%d").date()
                    - datetime.strptime(dep_date, "%Y-%m-%d").date()
                ).days,
            )
        except ValueError:
            day_offset = 0
    elif dep_time and arr_time and arr_time < dep_time:
        day_offset = 1

    new_dep = f"{target.isoformat()}T{dep_time}" if dep_time else dep_value
    new_arr = (
        f"{(target + timedelta(days=day_offset)).isoformat()}T{arr_time}"
        if arr_time
        else arr_value
    )
    return new_dep, new_arr


def normalize_flight_number(flight_num: str) -> str:
    """
    Normalizes flight numbers into standard IATA/ICAO format.
    Case-insensitive, strips all spaces, hyphens, and underscores.
    """
    if not flight_num or not isinstance(flight_num, str):
        raise InvalidFlightNumberException(str(flight_num), "Flight number cannot be empty.")

    cleaned = re.sub(r"[\s\-_]+", "", flight_num).strip().upper()

    pattern = r"^(?:[A-Z]{2}|[A-Z][0-9]|[0-9][A-Z]|[A-Z]{3})\d{1,4}[A-Z]?$"
    if not re.match(pattern, cleaned):
        raise InvalidFlightNumberException(
            flight_num,
            "Expected format: Standard 2-3 char airline code followed by 1-4 numbers (e.g. AI302, EK504, 6E211, QP1301, SG8168)."
        )

    return cleaned


def validate_date_string(date_str: str) -> str:
    """Validates and normalizes date string to YYYY-MM-DD format."""
    if not date_str or not isinstance(date_str, str):
        raise InvalidFlightDateException(str(date_str), "Date cannot be empty.")

    cleaned = date_str.strip()[:10]
    try:
        dt = datetime.strptime(cleaned, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        raise InvalidFlightDateException(
            date_str,
            f"Invalid date format '{date_str}'. Expected ISO format 'YYYY-MM-DD'."
        )


def split_flight_number(flight_clean: str) -> Tuple[str, str]:
    """
    Splits normalized flight number into carrier IATA/ICAO code and numeric flight number digits.
    """
    match_icao = re.match(r"^([A-Z]{3})(\d{1,4}[A-Z]?)$", flight_clean)
    if match_icao:
        return match_icao.group(1), match_icao.group(2)

    match_iata = re.match(r"^([A-Z0-9]{2})(\d{1,4}[A-Z]?)$", flight_clean)
    if match_iata:
        return match_iata.group(1), match_iata.group(2)

    return flight_clean[:2], flight_clean[2:]


def canonical_flight_iata(value: str) -> str:
    """Compare flight IATA values ignoring spaces, case, and leading zeros in the numeric part, with ICAO support."""
    cleaned = re.sub(r"[\s\-_]+", "", value or "").strip().upper()
    if not cleaned:
        return ""
    match = re.match(r"^([A-Z]{3}|[A-Z0-9]{2})(\d+)([A-Z]?)$", cleaned)
    if not match:
        return cleaned
    carrier, digits, suffix = match.group(1), match.group(2), match.group(3)
    icao_to_iata = {v: k for k, v in CARRIER_ICAO_MAP.items()}
    if carrier in icao_to_iata:
        carrier = icao_to_iata[carrier]
    return f"{carrier}{digits.lstrip('0') or '0'}{suffix}"


def _as_iata_code(value: Optional[str]) -> Optional[str]:
    code = (value or "").strip().upper()
    if re.fullmatch(r"[A-Z]{3}", code):
        return code
    return None


def _candidate_dep_arr(item: Dict[str, Any]) -> Tuple[str, str]:
    dep_obj = item.get("departure") if isinstance(item.get("departure"), dict) else {}
    arr_obj = item.get("arrival") if isinstance(item.get("arrival"), dict) else {}
    dep = str(dep_obj.get("iataCode") or dep_obj.get("iata") or item.get("departureIata") or "").strip().upper()
    arr = str(arr_obj.get("iataCode") or arr_obj.get("iata") or item.get("arrivalIata") or "").strip().upper()
    return dep, arr


def _source_priority(source: Optional[str]) -> int:
    if source in ("timetable", "flightsFuture"):
        return 2
    if source == "flights":
        return 1
    return 0


def _scheduled_departure_stamp(item: Dict[str, Any]) -> str:
    dep_obj = item.get("departure") if isinstance(item.get("departure"), dict) else {}
    return str(dep_obj.get("scheduledTime") or item.get("departureTime") or "")


def _context_airports(
    origin_code: Optional[str],
    destination_code: Optional[str],
    airport_code: Optional[str],
) -> List[str]:
    seen = []
    for raw in (origin_code, destination_code, airport_code):
        code = _as_iata_code(raw)
        if code and code not in seen:
            seen.append(code)
    return seen


def _filter_to_requested_sector(
    candidates: List[Dict[str, Any]],
    *,
    origin_code: Optional[str],
    destination_code: Optional[str],
    airport_code: Optional[str],
    direction: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Same IATA flight numbers can have multiple sectors (e.g. SG476 DEL-BOM then BOM-BLR).
    Keep the sector that matches the requested itinerary / service role.
    """
    if not candidates:
        return []

    origin = _as_iata_code(origin_code)
    dest = _as_iata_code(destination_code)
    airport = _as_iata_code(airport_code)
    role = (direction or "").strip().lower()

    exact = []
    if origin and dest:
        exact = [c for c in candidates if _candidate_dep_arr(c) == (origin, dest)]
        if exact:
            return exact

    scoped: List[Dict[str, Any]] = []
    if role in ("arrival", "arrive"):
        target = dest or airport
        if target:
            scoped = [c for c in candidates if _candidate_dep_arr(c)[1] == target]
    elif role in ("departure", "depart"):
        target = origin or airport
        if target:
            scoped = [c for c in candidates if _candidate_dep_arr(c)[0] == target]
    elif role == "transit" and airport:
        scoped = [
            c for c in candidates
            if airport in _candidate_dep_arr(c)
        ]

    return scoped or candidates


def _timetable_types_for_direction(direction: Optional[str]) -> List[str]:
    d = (direction or "any").strip().lower()
    if d in ("departure", "depart"):
        return ["departure"]
    if d in ("arrival", "arrive"):
        return ["arrival"]
    return ["departure", "arrival"]


def _candidate_flight_tokens(candidate: Dict[str, Any]) -> List[str]:
    tokens: List[str] = []
    flight_obj = candidate.get("flight") if isinstance(candidate.get("flight"), dict) else {}
    airline_obj = candidate.get("airline") if isinstance(candidate.get("airline"), dict) else {}
    codeshared = candidate.get("codeshared") if isinstance(candidate.get("codeshared"), dict) else {}
    cs_flight = codeshared.get("flight") if isinstance(codeshared.get("flight"), dict) else {}

    for raw in (
        flight_obj.get("iataNumber"),
        flight_obj.get("icaoNumber"),
        flight_obj.get("number"),
        candidate.get("flight_iata"),
        candidate.get("flightIata"),
        candidate.get("flight_icao"),
        candidate.get("flightIcao"),
        candidate.get("flight_num"),
        candidate.get("flightNum"),
        candidate.get("flightNumber"),
        cs_flight.get("iataNumber"),
        cs_flight.get("icaoNumber"),
        cs_flight.get("number"),
    ):
        if raw:
            tokens.append(str(raw))

    airline_iata = str(
        airline_obj.get("iataCode") or airline_obj.get("iata") or candidate.get("airlineIata") or ""
    ).strip().upper()
    flight_number = str(flight_obj.get("number") or candidate.get("flightNumber") or "").strip().upper()
    if airline_iata and flight_number:
        if flight_number.startswith(airline_iata):
            tokens.append(flight_number)
        else:
            tokens.append(f"{airline_iata}{flight_number}")
    elif flight_number:
        tokens.append(flight_number)

    airline_icao = str(
        airline_obj.get("icaoCode") or airline_obj.get("icao") or candidate.get("airlineIcao") or ""
    ).strip().upper()
    if airline_icao and flight_number:
        if flight_number.startswith(airline_icao):
            tokens.append(flight_number)
        else:
            tokens.append(f"{airline_icao}{flight_number}")

    return tokens


class AviationEdgeExtMixin:
    """Mixin providing flight normalization, status lookup, search, and telemetry for AviationEdgeProvider."""

    def _normalize_flight_data(self, raw: Dict[str, Any], date_context: Optional[str] = None) -> FlightStatusData:
        """
        Normalize raw Aviation Edge payload into standard internal FlightStatusData response schema.
        Handles 'routes', 'timetable', and 'flights' API response shapes dynamically.
        Strictly provider-driven. Missing fields are set to None.
        Never invents terminals, gates, status, or fake values.
        """
        ae = _ae()
        logger = ae.logger

        flight_obj = raw.get("flight", {}) if isinstance(raw.get("flight"), dict) else {}
        dep_obj = raw.get("departure", {}) if isinstance(raw.get("departure"), dict) else {}
        arr_obj = raw.get("arrival", {}) if isinstance(raw.get("arrival"), dict) else {}
        airline_obj = raw.get("airline", {}) if isinstance(raw.get("airline"), dict) else {}
        aircraft_obj = raw.get("aircraft", {}) if isinstance(raw.get("aircraft"), dict) else {}

        airline_iata = (
            airline_obj.get("iataCode")
            or airline_obj.get("iata")
            or raw.get("airlineIata")
        )
        if airline_iata:
            airline_iata = airline_iata.strip().upper()

        raw_flight_num = (
            flight_obj.get("iataNumber")
            or raw.get("flight_iata")
            or raw.get("flightIata")
        )
        if not raw_flight_num and (raw.get("airlineIata") and raw.get("flightNumber")):
            raw_flight_num = f"{raw.get('airlineIata')}{raw.get('flightNumber')}"
        if not raw_flight_num:
            raw_flight_num = raw.get("flightNumber")

        if raw_flight_num:
            raw_str = str(raw_flight_num).strip().upper()
            if airline_iata and not raw_str.startswith(airline_iata):
                raw_str = f"{airline_iata}{raw_str}"
            try:
                flight_num = ae.normalize_flight_number(raw_str)
            except Exception:
                flight_num = raw_str
        else:
            flight_num = None

        raw_airline_name = airline_obj.get("name") or airline_obj.get("airline_name") or raw.get("airlineName")
        airline_name = raw_airline_name or (ae.CARRIER_NAME_MAP.get(airline_iata) if airline_iata else None)
        airline_icao = airline_obj.get("icaoCode") or airline_obj.get("icao") or raw.get("airlineIcao") or (ae.CARRIER_ICAO_MAP.get(airline_iata) if airline_iata else None)
        airline_logo = f"https://images.aviation-edge.com/airline-logos/{airline_iata}.png" if airline_iata else None

        airline_details = AirlineDetails(
            name=airline_name,
            iata=airline_iata,
            icao=airline_icao,
            logo=airline_logo
        )

        flight_info = FlightInfo(
            number=str(flight_obj.get("number") or raw.get("flightNumber") or ""),
            iata=flight_num,
            icao=flight_obj.get("icaoNumber") or raw.get("flight_icao") or raw.get("flightIcao"),
            codeshare=str(raw.get("codeshare") or raw.get("codeshares") or flight_obj.get("codeshare")) if (raw.get("codeshare") or raw.get("codeshares") or flight_obj.get("codeshare")) else None
        )

        dep_code = dep_obj.get("iataCode") or dep_obj.get("iata") or dep_obj.get("code") or raw.get("departureIata")
        dep_terminal = dep_obj.get("terminal") or raw.get("departureTerminal") or None
        dep_gate = dep_obj.get("gate") or raw.get("departureGate") or None

        dep_airport_obj = build_flight_airport(
            dep_code,
            raw_name=dep_obj.get("name") or dep_obj.get("airport"),
            raw_city=dep_obj.get("city"),
            raw_country=dep_obj.get("country"),
            terminal=dep_terminal,
            gate=dep_gate
        ) if dep_code else None

        dep_sched = dep_obj.get("scheduledTime") or dep_obj.get("scheduled")
        if not dep_sched and raw.get("departureTime"):
            d_time = str(raw["departureTime"]).strip()
            date_prefix = date_context or datetime.now().strftime("%Y-%m-%d")
            dep_sched = f"{date_prefix}T{d_time}"
        elif dep_sched:
            dep_sched = str(dep_sched).replace(" ", "T")

        dep_est = dep_obj.get("estimatedTime") or dep_obj.get("estimated")
        if dep_est:
            dep_est = str(dep_est).replace(" ", "T")

        dep_act = dep_obj.get("actualTime") or dep_obj.get("actual")
        if dep_act:
            dep_act = str(dep_act).replace(" ", "T")

        dep_details = LocationEndpointDetails(
            airport=dep_code.upper() if dep_code else None,
            airport_name=dep_airport_obj.name if dep_airport_obj else dep_obj.get("name"),
            city=dep_airport_obj.city if dep_airport_obj else dep_obj.get("city"),
            country=dep_airport_obj.country if dep_airport_obj else dep_obj.get("country"),
            terminal=dep_terminal,
            gate=dep_gate,
            scheduled=dep_sched,
            estimated=dep_est,
            actual=dep_act,
            delay=int(dep_obj.get("delay")) if dep_obj.get("delay") is not None else None,
            timezone=dep_airport_obj.timezone if dep_airport_obj else dep_obj.get("timezone")
        )

        arr_code = arr_obj.get("iataCode") or arr_obj.get("iata") or arr_obj.get("code") or raw.get("arrivalIata")
        arr_terminal = arr_obj.get("terminal") or raw.get("arrivalTerminal") or None
        arr_gate = arr_obj.get("gate") or raw.get("arrivalGate") or None

        arr_airport_obj = build_flight_airport(
            arr_code,
            raw_name=arr_obj.get("name") or arr_obj.get("airport"),
            raw_city=arr_obj.get("city"),
            raw_country=arr_obj.get("country"),
            terminal=arr_terminal,
            gate=arr_gate
        ) if arr_code else None

        arr_sched = arr_obj.get("scheduledTime") or arr_obj.get("scheduled")
        if not arr_sched and raw.get("arrivalTime"):
            a_time = str(raw["arrivalTime"]).strip()
            date_prefix = date_context or datetime.now().strftime("%Y-%m-%d")
            if raw.get("departureTime") and str(raw["arrivalTime"]) < str(raw["departureTime"]):
                try:
                    dt = datetime.strptime(date_prefix, "%Y-%m-%d") + timedelta(days=1)
                    date_prefix = dt.strftime("%Y-%m-%d")
                except Exception:
                    pass
            arr_sched = f"{date_prefix}T{a_time}"
        elif arr_sched:
            arr_sched = str(arr_sched).replace(" ", "T")

        arr_est = arr_obj.get("estimatedTime") or arr_obj.get("estimated")
        if arr_est:
            arr_est = str(arr_est).replace(" ", "T")

        arr_act = arr_obj.get("actualTime") or arr_obj.get("actual")
        if arr_act:
            arr_act = str(arr_act).replace(" ", "T")

        arr_details = LocationEndpointDetails(
            airport=arr_code.upper() if arr_code else None,
            airport_name=arr_airport_obj.name if arr_airport_obj else arr_obj.get("name"),
            city=arr_airport_obj.city if arr_airport_obj else arr_obj.get("city"),
            country=arr_airport_obj.country if arr_airport_obj else arr_obj.get("country"),
            terminal=arr_terminal,
            gate=arr_gate,
            scheduled=arr_sched,
            estimated=arr_est,
            actual=arr_act,
            delay=int(arr_obj.get("delay")) if arr_obj.get("delay") is not None else None,
            timezone=arr_airport_obj.timezone if arr_airport_obj else arr_obj.get("timezone")
        )

        if date_context:
            row_date = ae._split_date_and_time(dep_details.scheduled)[0]
            retargeted_dep, retargeted_arr = ae._retarget_schedule_dates(
                dep_details.scheduled, arr_details.scheduled, date_context
            )
            dep_update: Dict[str, Any] = {}
            arr_update: Dict[str, Any] = {}

            if retargeted_dep != dep_details.scheduled:
                dep_update["scheduled"] = retargeted_dep
            if retargeted_arr != arr_details.scheduled:
                arr_update["scheduled"] = retargeted_arr

            # `timetable` only serves today, so a future-dated booking arrives with
            # today's live actual/estimated/delay attached. Those describe a
            # different operating day: keeping them skews duration and cutoff.
            if row_date and row_date != date_context:
                for update in (dep_update, arr_update):
                    update["estimated"] = None
                    update["actual"] = None
                    update["delay"] = None

            if dep_update or arr_update:
                logger.info(
                    "[SCHEDULE RETARGETED] source=%s row_date=%s -> %s | dep %r -> %r | arr %r -> %r | live_fields_dropped=%s",
                    raw.get("_source") or "provider",
                    row_date,
                    date_context,
                    dep_details.scheduled,
                    retargeted_dep,
                    arr_details.scheduled,
                    retargeted_arr,
                    bool(row_date and row_date != date_context),
                )
                dep_details = dep_details.model_copy(update=dep_update)
                arr_details = arr_details.model_copy(update=arr_update)

        dep_tz = dep_details.timezone or (dep_airport_obj.timezone if dep_airport_obj else None)
        arr_tz = arr_details.timezone or (arr_airport_obj.timezone if arr_airport_obj else None)

        sched_dep = self._parse_datetime(dep_details.scheduled, dep_tz)
        act_dep = self._parse_datetime(dep_details.actual, dep_tz) or self._parse_datetime(dep_details.estimated, dep_tz) or sched_dep

        sched_arr = self._parse_datetime(arr_details.scheduled, arr_tz)
        act_arr = self._parse_datetime(arr_details.actual, arr_tz) or self._parse_datetime(arr_details.estimated, arr_tz) or sched_arr

        dur_mins, dur_text = compute_flight_duration(raw, act_dep, act_arr, flight_num or "")

        duration_details = DurationDetails(
            minutes=dur_mins,
            formatted=dur_text
        )

        aircraft_details = AircraftDetails(
            model=aircraft_obj.get("modelCode") or aircraft_obj.get("model") or aircraft_obj.get("name"),
            registration=aircraft_obj.get("regNumber") or aircraft_obj.get("registration"),
            icao=aircraft_obj.get("icaoCode") or aircraft_obj.get("icao"),
            type=aircraft_obj.get("type"),
            distance=float(raw["distance"]) if raw.get("distance") is not None else None
        )

        status_val = raw.get("status") or raw.get("flight_status") or "Scheduled"

        normalized = FlightStatusData(
            airline=airline_details,
            flight=flight_info,
            departure=dep_details,
            arrival=arr_details,
            duration=duration_details,
            aircraft=aircraft_details,
            status=str(status_val).capitalize() if status_val else "Scheduled"
        )

        return normalized

    def get_flight_status(self, flight_num: str) -> FlightStatusData:
        """Retrieve real-time or master flight status."""
        ae = _ae()
        logger = ae.logger
        flight_clean = ae.normalize_flight_number(flight_num)
        cache_key = f"flight:status:{flight_clean}"

        # ── 1. Check Unified Cross-Provider Cache First ───────────────────────
        try:
            from app.flight.unified_cache import get_unified_flight, to_flight_status_data
            unified_hit = get_unified_flight(flight_clean)
            if unified_hit:
                cached_status = to_flight_status_data(unified_hit, flight_clean)
                if cached_status:
                    logger.info(f"[UNIFIED CACHE HIT] Flight status: {flight_clean} (bypassed external APIs)")
                    return cached_status
        except Exception as err:
            logger.debug("Unified cache check in get_flight_status error: %s", err)

        cached = self._get_cached_data(cache_key)
        if cached:
            try:
                return FlightStatusData.model_validate(cached)
            except Exception:
                pass

        carrier_code, flight_digits = ae.split_flight_number(flight_clean)
        carrier_icao = ae.CARRIER_ICAO_MAP.get(carrier_code, "")

        results = self._make_request("routes", {"airlineIata": carrier_code, "flightNumber": flight_digits})
        if not results and carrier_icao:
            results = self._make_request("routes", {"airlineIcao": carrier_icao, "flightNumber": flight_digits})
        if not results:
            results = self._make_request("routes", {"flightIata": flight_clean})
        if not results:
            results = self._make_request("timetable", {"flight_iata": flight_clean})

        valid_results = [
            r for r in results
            if self._validate_candidate_airline(r, carrier_code, carrier_icao, expected_flight_iata=flight_clean)
        ]

        if not valid_results:
            today_str = datetime.now().strftime("%Y-%m-%d")
            raise FlightNotFoundException(flight_num=flight_clean, date=today_str)

        flight_status = self._normalize_flight_data(valid_results[0])
        self._set_cached_data(cache_key, flight_status.model_dump(mode="json"))
        try:
            from app.flight.unified_cache import store_unified_flight
            store_unified_flight(flight_clean, flight_status.model_dump(mode="json"), provider="aviation_edge")
        except Exception:
            pass
        return flight_status

    def search_flights(self, query: str) -> List[FlightStatusData]:
        """Search flights by flight number, carrier, or airport query."""
        ae = _ae()
        logger = ae.logger
        if not query or not query.strip():
            return []

        q_raw = query.strip()
        try:
            q_clean = ae.normalize_flight_number(q_raw)
        except InvalidFlightNumberException:
            q_clean = q_raw.upper()

        cache_key = f"flight:search:{q_clean}"

        cached = self._get_cached_data(cache_key)
        if cached and isinstance(cached, list):
            try:
                return [FlightStatusData.model_validate(item) for item in cached]
            except Exception:
                pass

        results = []
        if len(q_clean) == 3 and q_clean.isalpha():
            results = self._make_request("timetable", {"iataCode": q_clean, "type": "departure"})
        else:
            results = self._make_request("flights", {"flightIata": q_clean})
            if not results:
                results = self._make_request("timetable", {"flight_iata": q_clean})

        normalized_list = []
        for item in results:
            try:
                normalized_list.append(self._normalize_flight_data(item))
            except Exception as err:
                logger.warning(f"Skipping unparseable item in search results: {err}")

        if normalized_list:
            self._set_cached_data(cache_key, [item.model_dump(mode="json") for item in normalized_list])

        return normalized_list

    def get_live_telemetry(self, flight_num: str) -> FlightTelemetry:
        """Retrieve live GPS coordinates and positional data."""
        ae = _ae()
        flight_clean = ae.normalize_flight_number(flight_num)
        cache_key = f"flight:telemetry:{flight_clean}"

        cached = self._get_cached_data(cache_key)
        if cached:
            try:
                return FlightTelemetry.model_validate(cached)
            except Exception:
                pass

        results = self._make_request("flights", {"flightIata": flight_clean})
        if not results or not isinstance(results, list):
            raise FlightNotFoundException(flight_num=flight_clean, date=datetime.now().strftime("%Y-%m-%d"))

        target = results[0]
        geography = target.get("geography") if isinstance(target.get("geography"), dict) else {}
        speed_obj = target.get("speed") if isinstance(target.get("speed"), dict) else {}

        def _to_float(val: Any) -> float:
            if val is None or isinstance(val, (dict, list)):
                return 0.0
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0

        lat = _to_float(geography.get("latitude") if geography.get("latitude") is not None else target.get("latitude"))
        lng = _to_float(geography.get("longitude") if geography.get("longitude") is not None else target.get("longitude"))
        alt = _to_float(geography.get("altitude") if geography.get("altitude") is not None else target.get("altitude"))
        heading = _to_float(geography.get("direction") if geography.get("direction") is not None else target.get("heading"))
        speed = _to_float(speed_obj.get("horizontal") if speed_obj.get("horizontal") is not None else target.get("speed"))

        telemetry = FlightTelemetry(
            latitude=lat,
            longitude=lng,
            altitude=alt,
            heading=heading,
            speed=speed
        )
        self._set_cached_data(cache_key, telemetry.model_dump(mode="json"), ttl=60)
        return telemetry
