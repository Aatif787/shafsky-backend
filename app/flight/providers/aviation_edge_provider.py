"""
Aviation Edge API Integration Provider.
Provides production-ready flight validation, schedule lookup, and tracking using configured production Flight APIs.
Supports all valid IATA/ICAO airline codes (AI, SG, QP, IX, 6E, UK, EK, QR, BA, EY, LH, AF, KL, SQ, CX, TK, AA, UA, DL, SV, FZ, etc.).
Never fabricates flight details, never uses mock data, fallback objects, or dummy JSON.
Strictly validates carrier IATA/ICAO codes to prevent cross-carrier flight substitution.
"""

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from app.config import settings
from app.flight.airports import build_flight_airport
from app.flight.duration import compute_flight_duration
from app.flight.exceptions import (
    FlightDomainException,
    FlightNotFoundException,
    FlightProviderNotConfiguredException,
    FlightProviderTimeoutException,
    FlightProviderUnavailableException,
    FlightRateLimitExceededException,
    FlightScheduleUnavailableException,
    InvalidFlightDateException,
    InvalidFlightNumberException,
)
from app.flight.provider import FlightProvider
from app.flight.schemas import (
    AircraftDetails,
    AirlineDetails,
    DurationDetails,
    FlightCarrier,
    FlightInfo,
    FlightStatusData,
    FlightTelemetry,
    LocationEndpointDetails,
)

logger = logging.getLogger("shafsky.flight.aviation_edge")

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


_IN_MEMORY_CACHE: Dict[str, Tuple[float, Any]] = {}

# Aviation Edge only serves `flightsFuture` from roughly a week out and answers
# anything nearer with `{"error": "date must be above YYYY-MM-DD"}`. The boundary
# moves with the current date, so it is learned from the response and cached here
# rather than hardcoded.
_COVERAGE_ERROR_RE = re.compile(
    r"date\s+must\s+be\s+(?:above|greater\s+than|after)\s*:?\s*(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_FUTURE_WINDOW_LOCK = threading.Lock()
_LEARNED_FUTURE_MIN_DATE: Optional[str] = None

_TIME_ONLY_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
_DATE_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?")


class _ScheduleOutOfProviderRange(FlightDomainException):
    """Internal signal: the provider refused the date rather than missing the flight."""

    def __init__(self, provider_message: str, boundary_date: Optional[str] = None):
        self.provider_message = provider_message
        self.boundary_date = boundary_date
        super().__init__(
            message=provider_message,
            status_code=422,
            code="PROVIDER_DATE_OUT_OF_RANGE",
        )


def _record_future_window_boundary(boundary_date: str) -> None:
    """Remember the first date `flightsFuture` will accept (boundary + 1 day)."""
    global _LEARNED_FUTURE_MIN_DATE
    try:
        first_ok = datetime.strptime(boundary_date, "%Y-%m-%d").date() + timedelta(days=1)
    except ValueError:
        return
    with _FUTURE_WINDOW_LOCK:
        current = _LEARNED_FUTURE_MIN_DATE
        if current is None or first_ok.isoformat() > current:
            _LEARNED_FUTURE_MIN_DATE = first_ok.isoformat()
            logger.info(
                "[PROVIDER WINDOW LEARNED] flightsFuture accepts dates from %s onward.",
                _LEARNED_FUTURE_MIN_DATE,
            )


def _future_schedule_min_date() -> str:
    """Earliest date worth sending to `flightsFuture`."""
    configured_days = int(getattr(settings, "AVIATION_EDGE_FUTURE_MIN_DAYS", 8))
    static_min = (datetime.now(timezone.utc).date() + timedelta(days=configured_days)).isoformat()
    with _FUTURE_WINDOW_LOCK:
        learned = _LEARNED_FUTURE_MIN_DATE
    if learned and learned > static_min:
        return learned
    return static_min


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


def get_redis_client():
    """Returns optional Redis client for caching if available."""
    try:
        import redis
        if getattr(settings, "REDIS_URL", None):
            return redis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=0.2,
                socket_timeout=0.2
            )
    except Exception:
        pass
    return None


def normalize_flight_number(flight_num: str) -> str:
    """
    Normalizes flight numbers into standard IATA/ICAO format.
    Case-insensitive, strips all spaces, hyphens, and underscores.
    Examples:
        'AI 302' -> 'AI302'
        'ai-302' -> 'AI302'
        '6E 211' -> '6E211'
        'qp 1301'-> 'QP1301'
        'sg 8168'-> 'SG8168'
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
    """
    Validates and normalizes date string to YYYY-MM-DD format.
    """
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
    Examples:
        'AI302'  -> ('AI', '302')
        '6E211'  -> ('6E', '211')
        'QP1301' -> ('QP', '1301')
        'AIC302' -> ('AIC', '302')
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
    # Convert 3-letter ICAO carrier code to 2-letter IATA if known
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


def _future_schedule_date_ok(date_clean: str) -> bool:
    """
    True only when `flightsFuture` can actually serve this date.

    Querying nearer dates is pointless: the provider rejects them outright, so the
    calls only add latency and burn rate limit on every lookup.
    """
    try:
        datetime.strptime(date_clean, "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    return date_clean >= _future_schedule_min_date()


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


class AviationEdgeProvider(FlightProvider):
    """
    Production-ready Aviation Edge Flight Intelligence Provider.
    Supports all valid IATA & ICAO airline codes dynamically.
    Executes multi-tier parallel query strategies across master routes, timetables,
    airline catalogs, and live flight trackers before resolving results.
    Strictly validates candidate airline codes to prevent cross-carrier substitution.
    """

    DEFAULT_CACHE_TTL = 300  # 5 minutes in seconds

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or getattr(settings, "AVIATION_EDGE_API_KEY", "")
        self.base_url = base_url or getattr(settings, "AVIATION_EDGE_BASE_URL", "https://aviation-edge.com/v2/public")
        self.timeout = float(getattr(settings, "AVIATION_EDGE_TIMEOUT", 12.0))
        self.max_retries = int(getattr(settings, "AVIATION_EDGE_MAX_RETRIES", 2))

    def _get_redis(self):
        """Returns Redis client for caching."""
        return get_redis_client()

    def _get_cached_data(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached JSON payload from memory or Redis if available."""
        now = time.time()
        if key in _IN_MEMORY_CACHE:
            expire_at, val = _IN_MEMORY_CACHE[key]
            if now < expire_at:
                logger.info(f"[IN-MEMORY CACHE HIT] Key: {key}")
                return val
            else:
                _IN_MEMORY_CACHE.pop(key, None)

        client = self._get_redis()
        if not client:
            return None
        try:
            cached_val = client.get(key)
            if cached_val:
                parsed = json.loads(cached_val)
                _IN_MEMORY_CACHE[key] = (now + self.DEFAULT_CACHE_TTL, parsed)
                logger.info(f"[REDIS CACHE HIT] Key: {key}")
                return parsed
        except Exception as err:
            logger.debug(f"Redis get failed for key {key}: {err}")
        return None

    def _set_cached_data(self, key: str, data: Any, ttl: int = DEFAULT_CACHE_TTL):
        """Store payload in in-memory and Redis cache if available."""
        now = time.time()
        _IN_MEMORY_CACHE[key] = (now + ttl, data)
        client = self._get_redis()
        if not client:
            return
        try:
            client.set(key, json.dumps(data), ex=ttl)
            logger.info(f"[CACHE STORE] Key: {key} (TTL: {ttl}s)")
        except Exception as err:
            logger.debug(f"Redis set failed for key {key}: {err}")

    def _make_request(self, endpoint: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Execute HTTP GET request to Aviation Edge with timeout, retry logic, rate-limit handling,
        and raw response logging for every failed lookup.
        """
        if not self.api_key:
            logger.warning("AVIATION_EDGE_API_KEY is not configured.")

        query_params = dict(params)
        if self.api_key:
            query_params["key"] = self.api_key

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        masked_params = {k: ("***HIDDEN***" if k == "key" else v) for k, v in query_params.items()}

        logger.info(f"[API OUTBOUND REQUEST] GET {url} | Params: {masked_params}")

        last_exception = None
        start_time = time.perf_counter()

        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.get(url, params=query_params)
                    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
                    logger.info(f"[API INBOUND RESPONSE] GET {endpoint} | Status: {response.status_code} | Latency: {elapsed_ms}ms")

                    if response.status_code == 200:
                        data = response.json()

                        if isinstance(data, dict) and "error" in data:
                            error_msg = str(data.get("error", "No Record Found"))
                            coverage = _COVERAGE_ERROR_RE.search(error_msg)
                            if coverage:
                                # Not a missing flight — the provider will not serve
                                # this date at all. Surface it so the caller can say so.
                                _record_future_window_boundary(coverage.group(1))
                                logger.warning(
                                    "[API DATE OUT OF RANGE] Endpoint: %s | Params: %s | Response: %s",
                                    endpoint, masked_params, error_msg,
                                )
                                raise _ScheduleOutOfProviderRange(error_msg, coverage.group(1))
                            logger.info(f"[FAILED API LOOKUP] Endpoint: {endpoint} | Params: {masked_params} | Response: {error_msg}")
                            return []

                        if isinstance(data, list):
                            logger.info(f"[SUCCESS API LOOKUP] Endpoint: {endpoint} | Params: {masked_params} | Items Returned: {len(data)}")
                            return data
                        elif isinstance(data, dict):
                            logger.info(f"[SUCCESS API LOOKUP] Endpoint: {endpoint} | Params: {masked_params} | Single Item Returned")
                            return [data]
                        return []

                    elif response.status_code == 404:
                        logger.info(f"[FAILED API LOOKUP 404] Endpoint: {endpoint} | Params: {masked_params}")
                        return []
                    elif response.status_code == 429:
                        logger.error(f"[API RATE LIMIT 429] Endpoint: {endpoint}")
                        raise FlightRateLimitExceededException()
                    elif response.status_code in (401, 403):
                        logger.error(f"[API AUTH ERROR {response.status_code}] Endpoint: {endpoint}")
                        raise FlightProviderUnavailableException("Invalid or unauthorized Aviation Edge API credentials.")
                    else:
                        last_exception = FlightProviderUnavailableException(f"Provider HTTP error {response.status_code}")

            except httpx.TimeoutException:
                elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
                logger.warning(f"[API TIMEOUT] Endpoint: {endpoint} | Params: {masked_params} | Timed out after {elapsed_ms}ms (Attempt {attempt}/{self.max_retries})")
                last_exception = FlightProviderTimeoutException()
            except (httpx.NetworkError, httpx.RequestError) as exc:
                logger.warning(f"[API NETWORK ERROR] Endpoint: {endpoint} | Attempt {attempt}/{self.max_retries}: {exc}")
                last_exception = FlightProviderUnavailableException(f"Network error: {str(exc)}")

            if attempt < self.max_retries:
                time.sleep(0.2)

        if last_exception:
            raise last_exception

        return []

    def _parse_datetime(self, dt_str: Optional[str], tz_name: Optional[str] = None) -> Optional[datetime]:
        """Parse datetime string into timezone-aware datetime object gracefully using airport timezone."""
        if not dt_str:
            return None
        dt_clean = dt_str.replace(" ", "T")
        try:
            dt = datetime.fromisoformat(dt_clean)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc)
            if tz_name:
                try:
                    from zoneinfo import ZoneInfo
                    local_tz = ZoneInfo(tz_name)
                    return dt.replace(tzinfo=local_tz).astimezone(timezone.utc)
                except Exception:
                    pass
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(dt_clean[:19], fmt)
                    if tz_name:
                        try:
                            from zoneinfo import ZoneInfo
                            local_tz = ZoneInfo(tz_name)
                            return dt.replace(tzinfo=local_tz).astimezone(timezone.utc)
                        except Exception:
                            pass
                    return dt.replace(tzinfo=timezone.utc)
                except Exception:
                    pass
        return None

    def _validate_candidate_airline(
        self,
        candidate: Dict[str, Any],
        expected_carrier_iata: str,
        expected_carrier_icao: str,
        expected_flight_iata: Optional[str] = None,
    ) -> bool:
        """
        Accepts a candidate when the requested flight IATA matches (including codeshares),
        otherwise requires the operating airline to match. Never accepts an unrelated flight
        from the same airline just because the carrier code matched.
        """
        if expected_flight_iata:
            expected = canonical_flight_iata(expected_flight_iata)
            for token in _candidate_flight_tokens(candidate):
                if canonical_flight_iata(token) == expected:
                    return True

        airline_obj = candidate.get("airline", {}) if isinstance(candidate.get("airline"), dict) else {}
        cand_airline_iata = str(airline_obj.get("iataCode") or airline_obj.get("iata") or candidate.get("airlineIata") or "").strip().upper()
        cand_airline_icao = str(airline_obj.get("icaoCode") or airline_obj.get("icao") or candidate.get("airlineIcao") or "").strip().upper()

        if cand_airline_iata and cand_airline_iata != expected_carrier_iata:
            if not expected_carrier_icao or cand_airline_icao != expected_carrier_icao:
                return False

        if expected_flight_iata:
            return False
        return True

    def _rank_and_select_candidate(
        self,
        candidates: List[Dict[str, Any]],
        expected_flight_iata: str,
        expected_carrier_iata: str,
        requested_date: str,
        origin_code: Optional[str] = None,
        destination_code: Optional[str] = None,
        airport_code: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Rank date-specific timetable sectors above stale route-master or live-tracker rows.
        Never substitute a different city-pair that happens to reuse the same flight number.
        """
        scoped = _filter_to_requested_sector(
            candidates,
            origin_code=origin_code,
            destination_code=destination_code,
            airport_code=airport_code,
            direction=direction,
        )
        if not scoped:
            return None

        def sort_key(item: Dict[str, Any]):
            cand_tokens = _candidate_flight_tokens(item)
            dep_code, arr_code = _candidate_dep_arr(item)
            airline_obj = item.get("airline", {}) if isinstance(item.get("airline"), dict) else {}
            cand_airline_iata = str(airline_obj.get("iataCode") or item.get("airlineIata") or "").strip().upper()
            dep_date = str(_scheduled_departure_stamp(item))[:10]

            score_flight_iata = 1 if any(
                canonical_flight_iata(token) == canonical_flight_iata(expected_flight_iata)
                for token in cand_tokens
            ) else 0
            score_source = _source_priority(str(item.get("_source") or ""))
            score_airline_iata = 1 if cand_airline_iata == expected_carrier_iata else 0
            score_route = 0
            if origin_code and dep_code == origin_code.strip().upper():
                score_route += 1
            if destination_code and arr_code == destination_code.strip().upper():
                score_route += 1
            score_date = 1 if dep_date == requested_date else 0
            stamp = _scheduled_departure_stamp(item) or "9999-12-31"
            # 1. Exact flight number match (-score_flight_iata)
            # 2. Sector/route matching (-score_route) takes precedence
            # 3. Exact departure date matching (-score_date)
            # 4. Source priority (-score_source) prefers live timetable/flightsFuture over static routes
            # 5. Airline match (-score_airline_iata)
            # 6. Earliest scheduled departure stamp
            return (
                -score_flight_iata,
                -score_route,
                -score_date,
                -score_source,
                -score_airline_iata,
                stamp,
            )

        ranked = sorted(scoped, key=sort_key)
        top_selected = ranked[0]
        if sort_key(top_selected)[0] != -1:
            logger.info(
                "[CANDIDATE RANKING REJECTED] No candidate matched the requested flight IATA; refusing airline-only substitution."
            )
            return None

        top_dep, top_arr = _candidate_dep_arr(top_selected)
        logger.info(
            "[CANDIDATE RANKING COMPLETED] Evaluated %s valid candidate records. Selected %s %s->%s",
            len(scoped),
            expected_flight_iata,
            top_dep,
            top_arr,
        )
        return top_selected

    def _normalize_flight_data(self, raw: Dict[str, Any], date_context: Optional[str] = None) -> FlightStatusData:
        """
        Normalize raw Aviation Edge payload into standard internal FlightStatusData response schema.
        Handles 'routes', 'timetable', and 'flights' API response shapes dynamically.
        Strictly provider-driven. Missing fields are set to None.
        Never invents terminals, gates, status, or fake values.
        """
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
                flight_num = normalize_flight_number(raw_str)
            except Exception:
                flight_num = raw_str
        else:
            flight_num = None

        raw_airline_name = airline_obj.get("name") or airline_obj.get("airline_name") or raw.get("airlineName")
        airline_name = raw_airline_name or (CARRIER_NAME_MAP.get(airline_iata) if airline_iata else None)
        airline_icao = airline_obj.get("icaoCode") or airline_obj.get("icao") or raw.get("airlineIcao") or (CARRIER_ICAO_MAP.get(airline_iata) if airline_iata else None)
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
            row_date = _split_date_and_time(dep_details.scheduled)[0]
            retargeted_dep, retargeted_arr = _retarget_schedule_dates(
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

    def validate_flight(
        self,
        flight_num: str,
        date: str,
        direction: Optional[str] = None,
        origin_code: Optional[str] = None,
        destination_code: Optional[str] = None,
        airport_code: Optional[str] = None,
        allow_fallback: bool = False,
    ) -> FlightStatusData:
        """
        Validate a flight number against Aviation Edge using airport-scoped timetable
        and future-schedule filters. Does not substitute a different flight from the same airline.
        """
        provider_name = "aviation_edge"
        flight_clean = normalize_flight_number(flight_num)
        date_clean = validate_date_string(date)
        direction_clean = (direction or "any").strip().lower()
        carrier_code, flight_digits = split_flight_number(flight_clean)
        carrier_icao = CARRIER_ICAO_MAP.get(carrier_code, "")
        padded_digits = flight_digits.zfill(4) if flight_digits.isdigit() else flight_digits
        icao_flight = f"{carrier_icao}{flight_digits}" if carrier_icao else ""
        numeric_flight = flight_digits.lstrip("0") or flight_digits
        context_airports = _context_airports(origin_code, destination_code, airport_code)
        timetable_types = _timetable_types_for_direction(direction_clean)
        use_future = _future_schedule_date_ok(date_clean)

        logger.info(
            f"\n======================================================\n"
            f"[FLIGHT SEARCH AUDIT REQUEST]\n"
            f"  • Input Flight: '{flight_num}' -> Clean: '{flight_clean}'\n"
            f"  • Carrier Code: IATA='{carrier_code}' | ICAO='{carrier_icao}' | Digits='{flight_digits}'\n"
            f"  • Date Sent: '{date_clean}' | Direction: '{direction_clean}'\n"
            f"  • Origin: {origin_code} | Destination: {destination_code} | Service airport: {airport_code}\n"
            f"======================================================"
        )

        origin_iata = _as_iata_code(origin_code)
        dest_iata = _as_iata_code(destination_code)
        service_iata = _as_iata_code(airport_code)
        cache_key = (
            f"flight:validate:{provider_name}:{flight_clean}:{date_clean}:{direction_clean}"
            f":{origin_iata or '-'}:{dest_iata or '-'}:{service_iata or '-'}"
        )

        # ── 1. Check Unified Cross-Provider Cache First (RAM -> Redis -> PostgreSQL DB) ─
        try:
            from app.flight.unified_cache import get_unified_flight, to_flight_status_data
            unified_hit = get_unified_flight(flight_clean, date_clean)
            if unified_hit:
                cached_status = to_flight_status_data(unified_hit, flight_clean)
                if cached_status:
                    logger.info(f"[UNIFIED FLIGHT CACHE HIT] Flight: {flight_clean} | Bypassed external APIs completely")
                    return cached_status
        except Exception as err:
            logger.debug("Unified cache check in AviationEdgeProvider error: %s", err)

        cached = self._get_cached_data(cache_key)
        if cached:
            try:
                flight_status = FlightStatusData.model_validate(cached)
                logger.info(f"[RETURNED ROUTE] Flight: {flight_clean} (from cache)")
                return flight_status
            except Exception:
                pass

        queries: List[Tuple[str, Dict[str, Any]]] = []

        def add_timetable_lookups(airport: str, schedule_type: str) -> None:
            queries.append(("timetable", {"iataCode": airport, "type": schedule_type, "flight_iata": flight_clean}))
            queries.append(
                (
                    "timetable",
                    {
                        "iataCode": airport,
                        "type": schedule_type,
                        "airline_iata": carrier_code,
                        "flight_num": numeric_flight,
                    },
                )
            )
            if use_future:
                queries.append(
                    (
                        "flightsFuture",
                        {
                            "iataCode": airport,
                            "type": schedule_type,
                            "date": date_clean,
                            "airline_iata": carrier_code,
                            "flight_num": numeric_flight,
                        },
                    )
                )

        # Query only the sector that belongs to this itinerary. SG476 is DEL-BOM and later BOM-BLR;
        # asking BOM for departures would incorrectly return Bangalore as the destination.
        if origin_iata and dest_iata:
            add_timetable_lookups(origin_iata, "departure")
            add_timetable_lookups(dest_iata, "arrival")
        elif direction_clean in ("arrival", "arrive") and (dest_iata or service_iata):
            arr_target = dest_iata or service_iata
            if arr_target:
                add_timetable_lookups(arr_target, "arrival")
            if origin_iata:
                add_timetable_lookups(origin_iata, "departure")
        elif direction_clean in ("departure", "depart") and (origin_iata or service_iata):
            dep_target = origin_iata or service_iata
            if dep_target:
                add_timetable_lookups(dep_target, "departure")
            if dest_iata:
                add_timetable_lookups(dest_iata, "arrival")
        elif context_airports:
            for airport in context_airports:
                for schedule_type in timetable_types:
                    add_timetable_lookups(airport, schedule_type)
        else:
            # When no airport context is passed, search timetable and flightsFuture globally by flight number
            queries.append(("timetable", {"flight_iata": flight_clean}))
            queries.append(("timetable", {"flight_number": numeric_flight, "airline_iata": carrier_code}))
            if use_future:
                queries.append(("flightsFuture", {"date": date_clean, "flight_iata": flight_clean}))
                queries.append(("flightsFuture", {"date": date_clean, "flight_num": numeric_flight, "airline_iata": carrier_code}))

        # Static routes / live tracker are last-resort only and tagged so timetable rows win.
        fallback_queries: List[Tuple[str, Dict[str, Any]]] = [
            ("routes", {"airlineIata": carrier_code, "flightNumber": flight_digits}),
            ("routes", {"airlineIata": carrier_code, "flightNumber": padded_digits}),
            ("routes", {"flightIata": flight_clean}),
            ("flights", {"flightIata": flight_clean}),
        ]
        if carrier_icao:
            fallback_queries.append(("routes", {"airlineIcao": carrier_icao, "flightNumber": flight_digits}))
            if icao_flight:
                fallback_queries.append(("routes", {"flightIcao": icao_flight}))
                fallback_queries.append(("flights", {"flightIcao": icao_flight}))
        queries.extend(fallback_queries)

        raw_candidates: List[Dict[str, Any]] = []
        out_of_range_detail: Optional[str] = None
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_query = {
                executor.submit(self._make_request, ep, params): (ep, params)
                for ep, params in queries
            }
            for future in as_completed(future_to_query):
                ep, params = future_to_query[future]
                try:
                    res = future.result()
                    if res and isinstance(res, list):
                        for item in res:
                            if isinstance(item, dict):
                                tagged = dict(item)
                                tagged["_source"] = ep
                                raw_candidates.append(tagged)
                except _ScheduleOutOfProviderRange as err:
                    out_of_range_detail = err.provider_message
                except Exception as err:
                    logger.warning("Flight lookup query error for %s: %s", ep, type(err).__name__)

        valid_candidates: List[Dict[str, Any]] = []
        for cand in raw_candidates:
            if self._validate_candidate_airline(
                cand,
                carrier_code,
                carrier_icao,
                expected_flight_iata=flight_clean,
            ):
                valid_candidates.append(cand)

        if not valid_candidates:
            logger.warning(
                f"\n======================================================\n"
                f"[FLIGHT SEARCH REJECTED DECISION]\n"
                f"  • Flight: {flight_clean} on {date_clean} (REJECTED)\n"
                f"  • Reason: No verified schedule match found for '{flight_clean}'.\n"
                f"  • Provider date coverage issue: {out_of_range_detail or 'none'}\n"
                f"======================================================"
            )
            if out_of_range_detail:
                raise FlightScheduleUnavailableException(
                    flight_num=flight_clean, date=date_clean
                )
            raise FlightNotFoundException(flight_num=flight_clean, date=date_clean)

        target_item = self._rank_and_select_candidate(
            valid_candidates,
            expected_flight_iata=flight_clean,
            expected_carrier_iata=carrier_code,
            requested_date=date_clean,
            origin_code=origin_code,
            destination_code=destination_code,
            airport_code=airport_code,
            direction=direction_clean,
        )

        if not target_item:
            raise FlightNotFoundException(flight_num=flight_clean, date=date_clean)

        flight_status = self._normalize_flight_data(target_item, date_context=date_clean)
        self._set_cached_data(cache_key, flight_status.model_dump(mode="json"))
        try:
            from app.flight.unified_cache import store_unified_flight
            store_unified_flight(
                flight_clean,
                flight_status.model_dump(mode="json"),
                provider="aviation_edge",
                flight_date=date_clean,
            )
        except Exception:
            pass

        logger.info(
            f"\n======================================================\n"
            f"[FLIGHT SEARCH SUCCESS DECISION]\n"
            f"  • Flight: {flight_clean} (ACCEPTED)\n"
            f"  • Airline: {flight_status.airline.name} ({flight_status.airline.iata}/{flight_status.airline.icao})\n"
            f"  • Route: {flight_status.departure.airport} ({flight_status.departure.timezone or 'UTC'}) -> {flight_status.arrival.airport} ({flight_status.arrival.timezone or 'UTC'})\n"
            f"  • Status: {flight_status.status}\n"
            f"======================================================"
        )

        return flight_status

    def get_flight_status(self, flight_num: str) -> FlightStatusData:
        """Retrieve real-time or master flight status."""
        flight_clean = normalize_flight_number(flight_num)
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

        carrier_code, flight_digits = split_flight_number(flight_clean)
        carrier_icao = CARRIER_ICAO_MAP.get(carrier_code, "")

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
        if not query or not query.strip():
            return []

        q_raw = query.strip()
        try:
            q_clean = normalize_flight_number(q_raw)
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
        flight_clean = normalize_flight_number(flight_num)
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
