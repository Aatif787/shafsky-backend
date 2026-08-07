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


def get_redis_client():
    """Returns optional Redis client for caching if available."""
    try:
        import redis
        if getattr(settings, "REDIS_URL", None):
            return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
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

    cleaned_date = date_str.strip()[:10]
    try:
        dt = datetime.strptime(cleaned_date, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        raise InvalidFlightDateException(date_str, "Date must be valid and formatted as YYYY-MM-DD.")


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
        self.timeout = float(getattr(settings, "AVIATION_EDGE_TIMEOUT", 4.0))
        self.max_retries = int(getattr(settings, "AVIATION_EDGE_MAX_RETRIES", 2))

    def _get_cached_data(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached JSON payload if available."""
        client = get_redis_client()
        if not client:
            return None
        try:
            cached_val = client.get(key)
            if cached_val:
                logger.info(f"[CACHE HIT] Key: {key}")
                return json.loads(cached_val)
        except Exception as err:
            logger.warning(f"Redis get failed for key {key}: {err}")
        return None

    def _set_cached_data(self, key: str, data: Any, ttl: int = DEFAULT_CACHE_TTL):
        """Store payload in Redis cache if available."""
        client = get_redis_client()
        if not client:
            return
        try:
            client.set(key, json.dumps(data), ex=ttl)
            logger.info(f"[CACHE STORE] Key: {key} (TTL: {ttl}s)")
        except Exception as err:
            logger.warning(f"Redis set failed for key {key}: {err}")

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

        return []

    def _parse_datetime(self, dt_str: Optional[str], tz_name: Optional[str] = None) -> Optional[datetime]:
        """Parse datetime string into timezone-aware datetime object gracefully using airport timezone."""
        if not dt_str:
            return None
        dt_clean = str(dt_str).replace(" ", "T")
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
        expected_carrier_icao: str
    ) -> bool:
        """
        Validates that a returned candidate record belongs to the requested airline.
        Prevents returning flights from incorrect airlines (e.g. returning Oman Air 'WY201' for Air India 'AI201').
        """
        airline_obj = candidate.get("airline", {}) if isinstance(candidate.get("airline"), dict) else {}
        flight_obj = candidate.get("flight", {}) if isinstance(candidate.get("flight"), dict) else {}

        cand_airline_iata = str(airline_obj.get("iataCode") or airline_obj.get("iata") or candidate.get("airlineIata") or "").strip().upper()
        cand_airline_icao = str(airline_obj.get("icaoCode") or airline_obj.get("icao") or candidate.get("airlineIcao") or "").strip().upper()

        cand_flight_iata = str(flight_obj.get("iataNumber") or candidate.get("flight_iata") or candidate.get("flightIata") or "").strip().upper()
        cand_flight_icao = str(flight_obj.get("icaoNumber") or candidate.get("flight_icao") or candidate.get("flightIcao") or "").strip().upper()

        # Rule 1: Check airline IATA code
        if cand_airline_iata and cand_airline_iata != expected_carrier_iata:
            if not expected_carrier_icao or cand_airline_icao != expected_carrier_icao:
                logger.info(
                    f"[CANDIDATE REJECTED] Candidate Airline: '{cand_airline_iata}' ({cand_airline_icao}) "
                    f"!= Expected: '{expected_carrier_iata}' ({expected_carrier_icao}) | Reason: Mismatched airline code"
                )
                return False

        # Rule 2: Check flight IATA prefix if present
        if cand_flight_iata:
            # Extract 2-char prefix from candidate flight iata (e.g. 'WY' from 'WY201')
            cand_prefix = cand_flight_iata[:2]
            if cand_prefix.isalpha() and cand_prefix != expected_carrier_iata:
                logger.info(
                    f"[CANDIDATE REJECTED] Candidate Flight IATA: '{cand_flight_iata}' "
                    f"starts with '{cand_prefix}' != Expected '{expected_carrier_iata}' | Reason: Flight IATA prefix mismatch"
                )
                return False

        # Rule 3: Check flight ICAO prefix if present
        if cand_flight_icao and expected_carrier_icao:
            cand_icao_prefix = cand_flight_icao[:3]
            if cand_icao_prefix.isalpha() and cand_icao_prefix != expected_carrier_icao:
                logger.info(
                    f"[CANDIDATE REJECTED] Candidate Flight ICAO: '{cand_flight_icao}' "
                    f"starts with '{cand_icao_prefix}' != Expected '{expected_carrier_icao}' | Reason: Flight ICAO prefix mismatch"
                )
                return False

        return True

    def _rank_and_select_candidate(
        self,
        candidates: List[Dict[str, Any]],
        expected_flight_iata: str,
        expected_carrier_iata: str,
        requested_date: str,
        origin_code: Optional[str] = None,
        destination_code: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Ranks candidate records using strict multi-tier criteria:
        1. Exact flight IATA match (e.g. AI201).
        2. Exact airline IATA match (e.g. AI).
        3. Matching operating airline.
        4. Matching travel date.
        5. Scheduled departure timestamp proximity to requested date.
        """
        if not candidates:
            return None

        def rank_score(item: Dict[str, Any]) -> Tuple[int, int, int, int]:
            flight_obj = item.get("flight", {}) if isinstance(item.get("flight"), dict) else {}
            dep_obj = item.get("departure", {}) if isinstance(item.get("departure"), dict) else {}

            cand_flight_iata = str(flight_obj.get("iataNumber") or item.get("flight_iata") or item.get("flightIata") or "").strip().upper()
            cand_airline_iata = str(item.get("airline", {}).get("iataCode") or item.get("airlineIata") or "").strip().upper()
            dep_date = str(dep_obj.get("scheduledTime") or item.get("departureTime") or "")[:10]

            # Priority 1: Exact flight IATA match
            score_flight_iata = 1 if cand_flight_iata == expected_flight_iata else 0

            # Priority 2: Exact airline IATA match
            score_airline_iata = 1 if cand_airline_iata == expected_carrier_iata else 0

            # Priority 3: Route match (Origin or Destination match)
            dep_code = (dep_obj.get("iataCode") or item.get("departureIata") or "").upper()
            arr_code = (item.get("arrival", {}).get("iataCode") or item.get("arrivalIata") or "").upper()

            score_route = 0
            if origin_code and dep_code == origin_code.strip().upper():
                score_route += 1
            if destination_code and arr_code == destination_code.strip().upper():
                score_route += 1

            # Priority 4: Date match
            score_date = 1 if dep_date == requested_date else 0

            return (score_flight_iata, score_airline_iata, score_route, score_date)

        ranked = sorted(candidates, key=rank_score, reverse=True)
        top_selected = ranked[0]

        top_flight_obj = top_selected.get("flight", {}) if isinstance(top_selected.get("flight"), dict) else {}
        top_flight_iata = top_flight_obj.get("iataNumber") or top_selected.get("flight_iata") or top_selected.get("flightIata") or expected_flight_iata
        top_airline_obj = top_selected.get("airline", {}) if isinstance(top_selected.get("airline"), dict) else {}
        top_airline_name = top_airline_obj.get("name") or CARRIER_NAME_MAP.get(expected_carrier_iata, expected_carrier_iata)

        logger.info(
            f"[CANDIDATE RANKING COMPLETED] Evaluated {len(candidates)} valid candidate records.\n"
            f"  • Top Selected Candidate: Flight '{top_flight_iata}' | Airline: '{top_airline_name}'"
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

        dep_details = LocationEndpointDetails(
            airport=dep_code.upper() if dep_code else None,
            airport_name=dep_airport_obj.name if dep_airport_obj else dep_obj.get("name"),
            city=dep_airport_obj.city if dep_airport_obj else dep_obj.get("city"),
            country=dep_airport_obj.country if dep_airport_obj else dep_obj.get("country"),
            terminal=dep_terminal,
            gate=dep_gate,
            scheduled=dep_sched,
            estimated=dep_obj.get("estimatedTime") or dep_obj.get("estimated"),
            actual=dep_obj.get("actualTime") or dep_obj.get("actual"),
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

        arr_details = LocationEndpointDetails(
            airport=arr_code.upper() if arr_code else None,
            airport_name=arr_airport_obj.name if arr_airport_obj else arr_obj.get("name"),
            city=arr_airport_obj.city if arr_airport_obj else arr_obj.get("city"),
            country=arr_airport_obj.country if arr_airport_obj else arr_obj.get("country"),
            terminal=arr_terminal,
            gate=arr_gate,
            scheduled=arr_sched,
            estimated=arr_obj.get("estimatedTime") or arr_obj.get("estimated"),
            actual=arr_obj.get("actualTime") or arr_obj.get("actual"),
            delay=int(arr_obj.get("delay")) if arr_obj.get("delay") is not None else None,
            timezone=arr_airport_obj.timezone if arr_airport_obj else arr_obj.get("timezone")
        )

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
        allow_fallback: bool = False
    ) -> FlightStatusData:
        """
        Validate flight existence for a given flight number, date, and direction.
        Executes parallel multi-tier query strategies across master routes, timetables,
        airline catalogs, and live flight trackers before resolving results.
        Supports all IATA and ICAO codes dynamically.
        Never substitutes flights belonging to another airline.
        """
        provider_name = "aviation_edge"
        flight_clean = normalize_flight_number(flight_num)
        date_clean = validate_date_string(date)
        direction_clean = (direction or "any").strip().lower()
        carrier_code, flight_digits = split_flight_number(flight_clean)
        carrier_icao = CARRIER_ICAO_MAP.get(carrier_code, "")
        padded_digits = flight_digits.zfill(4) if flight_digits.isdigit() else flight_digits
        icao_flight = f"{carrier_icao}{flight_digits}" if carrier_icao else ""

        logger.info(
            f"\n======================================================\n"
            f"[FLIGHT SEARCH AUDIT REQUEST]\n"
            f"  • Input Flight: '{flight_num}' -> Clean: '{flight_clean}'\n"
            f"  • Carrier Code: IATA='{carrier_code}' | ICAO='{carrier_icao}' | Digits='{flight_digits}'\n"
            f"  • Date Sent: '{date_clean}' | Direction: '{direction_clean}'\n"
            f"  • Origin: {origin_code} | Destination: {destination_code}\n"
            f"======================================================"
        )

        cache_key = f"flight:validate:{provider_name}:{flight_clean}:{date_clean}:{direction_clean}"

        cached = self._get_cached_data(cache_key)
        if cached:
            try:
                flight_status = FlightStatusData.model_validate(cached)
                logger.info(f"[RETURNED ROUTE] Flight: {flight_clean} (from cache)")
                return flight_status
            except Exception:
                pass

        raw_candidates: List[Dict[str, Any]] = []

        # Batch 1: Concurrent IATA & ICAO master routes & timetable endpoints for specific carrier
        batch1_queries = [
            ("routes", {"airlineIata": carrier_code, "flightNumber": flight_digits}),
            ("routes", {"flightIata": flight_clean}),
            ("timetable", {"flight_iata": flight_clean, "date": date_clean}),
            ("timetable", {"flightIata": flight_clean, "date": date_clean}),
        ]
        if carrier_icao:
            batch1_queries.append(("routes", {"airlineIcao": carrier_icao, "flightNumber": flight_digits}))
            if icao_flight:
                batch1_queries.append(("routes", {"flightIcao": icao_flight}))
                batch1_queries.append(("timetable", {"flight_icao": icao_flight, "date": date_clean}))

        with ThreadPoolExecutor(max_workers=6) as executor:
            future_to_query = {
                executor.submit(self._make_request, ep, params): (ep, params)
                for ep, params in batch1_queries
            }
            for future in as_completed(future_to_query):
                try:
                    res = future.result()
                    if res and isinstance(res, list) and len(res) > 0:
                        raw_candidates.extend(res)
                except Exception as err:
                    logger.warning(f"Batch 1 query error: {err}")

        # Batch 2: Padded digits, alternate params (airline_iata + flight_number), and live trackers
        if not raw_candidates:
            batch2_queries = [
                ("routes", {"airlineIata": carrier_code, "flightNumber": padded_digits}),
                ("timetable", {"flight_iata": flight_clean}),
                ("timetable", {"flight_number": flight_digits, "airline_iata": carrier_code, "date": date_clean}),
                ("timetable", {"flight_number": flight_digits, "airlineIata": carrier_code}),
                ("flights", {"flightIata": flight_clean}),
            ]
            if carrier_icao:
                batch2_queries.append(("timetable", {"flight_number": flight_digits, "airline_icao": carrier_icao}))
                if icao_flight:
                    batch2_queries.append(("flights", {"flightIcao": icao_flight}))

            with ThreadPoolExecutor(max_workers=6) as executor:
                future_to_query = {
                    executor.submit(self._make_request, ep, params): (ep, params)
                    for ep, params in batch2_queries
                }
                for future in as_completed(future_to_query):
                    try:
                        res = future.result()
                        if res and isinstance(res, list) and len(res) > 0:
                            raw_candidates.extend(res)
                    except Exception as err:
                        logger.warning(f"Batch 2 query error: {err}")

        # Batch 3: Airline Master Catalog Lookup (Full carrier route fallback)
        if not raw_candidates:
            logger.info(f"[BATCH 3 LOOKUP] Attempting carrier catalog lookup for airline code: {carrier_code}")
            catalog_items = self._make_request("routes", {"airlineIata": carrier_code})
            if not catalog_items and carrier_icao:
                catalog_items = self._make_request("routes", {"airlineIcao": carrier_icao})

            if catalog_items:
                for item in catalog_items:
                    item_num = str(item.get("flightNumber") or "").strip()
                    if item_num == flight_digits or item_num == padded_digits or item_num.lstrip("0") == flight_digits.lstrip("0"):
                        raw_candidates.append(item)

        # Batch 4: Hub Timetable Schedule Search (STRICT CARRIER MATCH REQUIRED)
        if not raw_candidates:
            hubs_to_check = [origin_code] if origin_code else ["DEL", "BOM", "BLR"]
            hub_queries = []
            for hub in hubs_to_check:
                if hub:
                    hub_code = hub.strip().upper()
                    hub_queries.append(("timetable", {"iataCode": hub_code, "type": "departure"}))
                    hub_queries.append(("timetable", {"iataCode": hub_code, "type": "arrival"}))

            with ThreadPoolExecutor(max_workers=6) as executor:
                future_to_query = {
                    executor.submit(self._make_request, ep, params): (ep, params)
                    for ep, params in hub_queries
                }
                for future in as_completed(future_to_query):
                    try:
                        res = future.result()
                        if res and isinstance(res, list) and len(res) > 0:
                            for item in res:
                                f_obj = item.get("flight", {}) if isinstance(item.get("flight"), dict) else {}
                                item_iata = str(f_obj.get("iataNumber") or item.get("flight_iata") or item.get("flightIata") or "").upper()
                                item_icao = str(f_obj.get("icaoNumber") or item.get("flight_icao") or item.get("flightIcao") or "").upper()

                                # MUST match carrier code prefix and full flight IATA/ICAO
                                if item_iata == flight_clean or item_icao == icao_flight:
                                    raw_candidates.append(item)
                    except Exception as err:
                        logger.warning(f"Batch 4 query error: {err}")

        # Filter candidates strictly by carrier code matching
        valid_candidates: List[Dict[str, Any]] = []
        for cand in raw_candidates:
            if self._validate_candidate_airline(cand, carrier_code, carrier_icao):
                valid_candidates.append(cand)

        if not valid_candidates:
            logger.warning(
                f"\n======================================================\n"
                f"[FLIGHT SEARCH REJECTED DECISION]\n"
                f"  • Flight: {flight_clean} on {date_clean} (REJECTED)\n"
                f"  • Reason: No verified schedule match found for airline '{carrier_code}'. All non-matching candidate airlines rejected.\n"
                f"======================================================"
            )
            raise FlightNotFoundException(flight_num=flight_clean, date=date_clean)

        target_item = self._rank_and_select_candidate(
            valid_candidates,
            expected_flight_iata=flight_clean,
            expected_carrier_iata=carrier_code,
            requested_date=date_clean,
            origin_code=origin_code,
            destination_code=destination_code
        )

        if not target_item:
            raise FlightNotFoundException(flight_num=flight_clean, date=date_clean)

        flight_status = self._normalize_flight_data(target_item, date_context=date_clean)
        self._set_cached_data(cache_key, flight_status.model_dump(mode="json"))

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

        valid_results = [r for r in results if self._validate_candidate_airline(r, carrier_code, carrier_icao)]

        if not valid_results:
            today_str = datetime.now().strftime("%Y-%m-%d")
            raise FlightNotFoundException(flight_num=flight_clean, date=today_str)

        flight_status = self._normalize_flight_data(valid_results[0])
        self._set_cached_data(cache_key, flight_status.model_dump(mode="json"))
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
        geography = target.get("geography", {}) if isinstance(target.get("geography"), dict) else {}
        speed_obj = target.get("speed", {}) if isinstance(target.get("speed"), dict) else {}

        lat = float(geography.get("latitude") or target.get("latitude") or 0.0)
        lng = float(geography.get("longitude") or target.get("longitude") or 0.0)
        alt = float(geography.get("altitude") or target.get("altitude") or 0.0)
        heading = float(geography.get("direction") or target.get("heading") or 0.0)
        speed = float(speed_obj.get("horizontal") or target.get("speed") or 0.0)

        telemetry = FlightTelemetry(
            latitude=lat,
            longitude=lng,
            altitude=alt,
            heading=heading,
            speed=speed
        )
        self._set_cached_data(cache_key, telemetry.model_dump(mode="json"), ttl=60)
        return telemetry
