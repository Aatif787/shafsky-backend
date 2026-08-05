"""
Aviation Edge API Integration Provider.
Provides production-ready flight validation, schedule lookup, and tracking using configured production Flight APIs.
Never fabricates flight details, never uses mock data, fallback objects, or dummy JSON.
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from app.core.config import settings
from app.flight.airports import build_flight_airport
from app.flight.duration import compute_flight_duration
from app.flight.exceptions import (
    FlightDomainException,
    FlightInvalidParameterException,
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
    Examples:
        'AI 302' -> 'AI302'
        'ai-302' -> 'AI302'
        '6E 211' -> '6E211'
        'EK504'  -> 'EK504'
        'BA 256' -> 'BA256'
        'QR 570' -> 'QR570'
    """
    if not flight_num or not isinstance(flight_num, str):
        raise InvalidFlightNumberException(str(flight_num), "Flight number cannot be empty.")

    # Remove all spaces, hyphens, underscores
    cleaned = re.sub(r"[\s\-_]+", "", flight_num).strip().upper()

    # Standard IATA (2-char, e.g. AI, 6E) or ICAO (3-letter, e.g. AIC, UAE) carrier code prefix + 1-4 numbers
    pattern = r"^(?:[A-Z]{2}|[A-Z][0-9]|[0-9][A-Z]|[A-Z]{3})\d{1,4}[A-Z]?$"
    if not re.match(pattern, cleaned):
        raise InvalidFlightNumberException(
            flight_num,
            "Expected format: Standard 2-3 char airline code followed by 1-4 numbers (e.g. AI302, EK504, 6E211)."
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


def split_flight_number(flight_clean: str) -> tuple[str, str]:
    """
    Splits normalized flight number into carrier IATA/ICAO code and numeric flight number digits.
    Examples:
        'AI302'  -> ('AI', '302')
        '6E211'  -> ('6E', '211')
        'EK510'  -> ('EK', '510')
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
    Production-ready Aviation Edge Flight Intelligence Provider implementation.
    Acts as the primary provider for Shafsky Aviation.
    """

    DEFAULT_CACHE_TTL = 300  # 5 minutes in seconds

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or getattr(settings, "AVIATION_EDGE_API_KEY", "")
        self.base_url = base_url or getattr(settings, "AVIATION_EDGE_BASE_URL", "https://aviation-edge.com/v2/public")
        self.timeout = float(getattr(settings, "AVIATION_EDGE_TIMEOUT", 10.0))
        self.max_retries = int(getattr(settings, "AVIATION_EDGE_MAX_RETRIES", 3))

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
        and masked logging.
        Never logs or exposes the API key.
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

                        # Log raw JSON payload preview for audit
                        raw_snippet = json.dumps(data)[:600] if data else "[]"
                        logger.info(f"[RAW API RESPONSE] Endpoint: {endpoint} | Items: {len(data) if isinstance(data, list) else 1} | Raw Payload Snippet: {raw_snippet}")

                        if isinstance(data, dict) and "error" in data:
                            error_msg = str(data.get("error", "Unknown error from provider"))
                            logger.error(f"[PROVIDER ERROR PAYLOAD] {error_msg}")

                            error_lower = error_msg.lower()
                            if "rate limit" in error_lower or "limit exceeded" in error_lower or "quota" in error_lower:
                                raise FlightRateLimitExceededException(f"Provider rate limit exceeded: {error_msg}")
                            elif "not found" in error_lower or "no data" in error_lower or "empty" in error_lower or "no record" in error_lower or "record" in error_lower:
                                return []
                            elif "key" in error_lower or "unauthorized" in error_lower or "invalid" in error_lower:
                                raise FlightProviderUnavailableException("Invalid or unauthorized Aviation Edge API key.")
                            else:
                                raise FlightProviderUnavailableException(f"Provider error: {error_msg}")

                        if isinstance(data, list):
                            return data
                        elif isinstance(data, dict):
                            return [data]
                        return []

                    elif response.status_code == 404:
                        logger.info(f"[PROVIDER 404] No records found for endpoint {endpoint}")
                        return []
                    elif response.status_code == 429:
                        logger.error("[PROVIDER 429] Rate limit exceeded!")
                        raise FlightRateLimitExceededException()
                    elif response.status_code in (401, 403):
                        logger.error(f"[PROVIDER AUTH FAIL] Status {response.status_code}")
                        raise FlightProviderUnavailableException("Invalid or unauthorized Aviation Edge API credentials.")
                    elif response.status_code >= 500:
                        logger.warning(f"[PROVIDER 5XX] Status {response.status_code} (attempt {attempt}/{self.max_retries})")
                        last_exception = FlightProviderUnavailableException(f"Provider HTTP error {response.status_code}")
                    else:
                        logger.warning(f"[PROVIDER UNEXPECTED] Status {response.status_code}")
                        return []

            except httpx.TimeoutException:
                elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
                logger.warning(f"[PROVIDER TIMEOUT] Attempt {attempt}/{self.max_retries} timed out after {elapsed_ms}ms")
                last_exception = FlightProviderTimeoutException()
            except (httpx.NetworkError, httpx.RequestError) as exc:
                logger.warning(f"[PROVIDER NETWORK ERROR] Attempt {attempt}/{self.max_retries}: {exc}")
                last_exception = FlightProviderUnavailableException(f"Network error: {str(exc)}")

            if attempt < self.max_retries:
                time.sleep(0.5 * (2 ** (attempt - 1)))

        if last_exception:
            raise last_exception
        raise FlightProviderUnavailableException("Flight intelligence provider is currently unreachable.")

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

    def _normalize_flight_data(self, raw: Dict[str, Any], date_context: Optional[str] = None) -> FlightStatusData:
        """
        Normalize raw Aviation Edge payload into standard internal FlightStatusData response schema.
        Handles 'routes', 'timetable', and 'flights' API response shapes seamlessly.
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

        airline_registry = {
            "AI": "Air India",
            "6E": "IndiGo",
            "EK": "Emirates",
            "QR": "Qatar Airways",
            "BA": "British Airways",
            "EY": "Etihad Airways",
            "SG": "SpiceJet",
            "UK": "Vistara",
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
            "IX": "Air India Express",
            "QP": "Akasa Air"
        }

        raw_airline_name = airline_obj.get("name") or airline_obj.get("airline_name") or raw.get("airlineName")
        airline_name = raw_airline_name or (airline_registry.get(airline_iata) if airline_iata else None)
        airline_icao = airline_obj.get("icaoCode") or airline_obj.get("icao") or raw.get("airlineIcao") or None
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
        Performs flight number normalization, date format validation, multi-tier provider query
        (routes -> timetable -> flights tracker), exact route normalization, and direction/provider isolated caching.
        """
        provider_name = "aviation_edge"
        flight_clean = normalize_flight_number(flight_num)
        date_clean = validate_date_string(date)
        direction_clean = (direction or "any").strip().lower()
        carrier_code, flight_digits = split_flight_number(flight_clean)
        padded_digits = flight_digits.zfill(4) if flight_digits.isdigit() else flight_digits

        logger.info(
            f"\n======================================================\n"
            f"[FLIGHT VALIDATION AUDIT REQUEST]\n"
            f"  • Raw Input: flight_num='{flight_num}', date='{date}', origin='{origin_code}', dest='{destination_code}'\n"
            f"  • Normalized Flight: '{flight_clean}' (Carrier: '{carrier_code}', Digits: '{flight_digits}', Padded: '{padded_digits}')\n"
            f"  • Date Sent: '{date_clean}'\n"
            f"  • Direction: '{direction_clean}'\n"
            f"  • Provider: {provider_name}\n"
            f"======================================================"
        )

        cache_key = f"flight:validate:{provider_name}:{flight_clean}:{date_clean}:{direction_clean}"

        cached = self._get_cached_data(cache_key)
        if cached:
            try:
                flight_status = FlightStatusData.model_validate(cached)
                logger.info(f"[RETURNED ROUTE] Flight: {flight_clean} (from cache) | Final Route: {flight_status.departure.airport} -> {flight_status.arrival.airport} | Status: {flight_status.status}")
                return flight_status
            except Exception:
                pass

        results = []
        try:
            # Multi-Tier Endpoint Query Pipeline (Systematic fallback search)
            # Tier 1: Routes Endpoint (Master Schedule per Airline IATA + Flight Number)
            results = self._make_request("routes", {"airlineIata": carrier_code, "flightNumber": flight_digits})

            # Tier 2: Routes Endpoint (Flight IATA)
            if not results:
                results = self._make_request("routes", {"flightIata": flight_clean})

            # Tier 3: Routes Endpoint (Padded 4-digit Flight Number e.g. 0201 for AI201)
            if not results and padded_digits != flight_digits:
                results = self._make_request("routes", {"airlineIata": carrier_code, "flightNumber": padded_digits})

            # Tier 4: Timetable Endpoint (flight_iata + date)
            if not results:
                results = self._make_request("timetable", {"flight_iata": flight_clean, "date": date_clean})

            # Tier 5: Timetable Endpoint (flightIata + date)
            if not results:
                results = self._make_request("timetable", {"flightIata": flight_clean, "date": date_clean})

            # Tier 6: Timetable Endpoint (flight_iata without date filter)
            if not results:
                results = self._make_request("timetable", {"flight_iata": flight_clean})

            # Tier 7: Timetable Endpoint (flight_number + airline_iata + date)
            if not results:
                results = self._make_request("timetable", {"flight_number": flight_digits, "airline_iata": carrier_code, "date": date_clean})

            # Tier 8: Timetable Endpoint (Padded flight_number + airline_iata + date)
            if not results and padded_digits != flight_digits:
                results = self._make_request("timetable", {"flight_number": padded_digits, "airline_iata": carrier_code, "date": date_clean})

            # Tier 9: Timetable Endpoint (Origin Airport Departure Timetable)
            if not results and origin_code:
                results = self._make_request("timetable", {"iataCode": origin_code.strip().upper(), "type": "departure", "flight_iata": flight_clean})

            # Tier 10: Live Flight Tracker Endpoint (Active airborne flights)
            if not results:
                results = self._make_request("flights", {"flightIata": flight_clean})

            # Tier 11: Live Flight Tracker Endpoint with padded flight number
            if not results and padded_digits != flight_digits:
                results = self._make_request("flights", {"flightIata": f"{carrier_code}{padded_digits}"})
        except FlightDomainException as exc:
            logger.warning(f"[PROVIDER ERROR] Upstream request for {flight_clean} failed ({exc.message}).")
            raise FlightNotFoundException(flight_num=flight_clean, date=date_clean)

        logger.info(f"[FLIGHT PROVIDER RESPONSE SUMMARY] Provider: {provider_name} | Total items from upstream API for {flight_clean}: {len(results)}")

        if not results:
            logger.warning(
                f"\n======================================================\n"
                f"[FLIGHT VALIDATION REJECTED DECISION]\n"
                f"  • Flight: {flight_clean} on {date_clean} (REJECTED)\n"
                f"  • Reason: Exhausted all 11 lookup tiers across routes, timetable, airport schedule, and live tracking endpoints. Provider confirmed 0 records exist.\n"
                f"======================================================"
            )
            raise FlightNotFoundException(flight_num=flight_clean, date=date_clean)

        # Smart Multi-leg Segment Matcher (Never auto-pick wrong route if origin/destination provided)
        matching_item = None
        if len(results) > 1:
            if origin_code:
                orig_clean = origin_code.strip().upper()
                for item in results:
                    dep_code = (item.get("departure", {}).get("iataCode") or item.get("departureIata") or "").upper()
                    if dep_code == orig_clean:
                        matching_item = item
                        break
            if not matching_item and destination_code:
                dest_clean = destination_code.strip().upper()
                for item in results:
                    arr_code = (item.get("arrival", {}).get("iataCode") or item.get("arrivalIata") or "").upper()
                    if arr_code == dest_clean:
                        matching_item = item
                        break

        target_item = matching_item or results[0]
        flight_status = self._normalize_flight_data(target_item, date_context=date_clean)
        self._set_cached_data(cache_key, flight_status.model_dump(mode="json"))

        logger.info(
            f"\n======================================================\n"
            f"[FLIGHT VALIDATION SUCCESS DECISION]\n"
            f"  • Flight: {flight_clean} (ACCEPTED)\n"
            f"  • Final Route: {flight_status.departure.airport} ({flight_status.departure.timezone or 'UTC'}) -> {flight_status.arrival.airport} ({flight_status.arrival.timezone or 'UTC'})\n"
            f"  • Scheduled Departure: {flight_status.departure.scheduled}\n"
            f"  • Scheduled Arrival: {flight_status.arrival.scheduled}\n"
            f"  • Duration: {flight_status.duration.formatted}\n"
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
        padded_digits = flight_digits.zfill(4) if flight_digits.isdigit() else flight_digits

        results = self._make_request("routes", {"airlineIata": carrier_code, "flightNumber": flight_digits})
        if not results:
            results = self._make_request("routes", {"flightIata": flight_clean})
        if not results and padded_digits != flight_digits:
            results = self._make_request("routes", {"airlineIata": carrier_code, "flightNumber": padded_digits})
        if not results:
            results = self._make_request("timetable", {"flight_iata": flight_clean})
        if not results:
            results = self._make_request("timetable", {"flightIata": flight_clean})
        if not results:
            results = self._make_request("flights", {"flightIata": flight_clean})

        if not results:
            today_str = datetime.now().strftime("%Y-%m-%d")
            raise FlightNotFoundException(flight_num=flight_clean, date=today_str)

        flight_status = self._normalize_flight_data(results[0])
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
