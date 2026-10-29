"""
Aviation Edge API Integration Provider.
Provides production-ready flight validation, schedule lookup, and tracking using configured production Flight APIs.
Supports all valid IATA/ICAO airline codes (AI, SG, QP, IX, 6E, UK, EK, QR, BA, EY, LH, AF, KL, SQ, CX, TK, AA, UA, DL, SV, FZ, etc.).
Never fabricates flight details, never uses mock data, fallback objects, or dummy JSON.
Strictly validates carrier IATA/ICAO codes to prevent cross-carrier flight substitution.
"""

import json
import logging
import os
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
from app.flight.providers.aviation_edge_ext import (
    CARRIER_ICAO_MAP,
    CARRIER_NAME_MAP,
    AviationEdgeExtMixin,
    _DATE_TIME_RE,
    _TIME_ONLY_RE,
    _as_iata_code,
    _candidate_dep_arr,
    _candidate_flight_tokens,
    _context_airports,
    _filter_to_requested_sector,
    _retarget_schedule_dates,
    _scheduled_departure_stamp,
    _source_priority,
    _split_date_and_time,
    _timetable_types_for_direction,
    canonical_flight_iata,
    normalize_flight_number,
    split_flight_number,
    validate_date_string,
)
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


class AviationEdgeProvider(AviationEdgeExtMixin, FlightProvider):
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
        self.timeout = float(getattr(settings, "AVIATION_EDGE_TIMEOUT", 5.0))
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
            cand_airline_iata = (airline_obj.get("iataCode") or item.get("airlineIata") or "").strip().upper()
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
            unified_hit = get_unified_flight(
                flight_clean,
                date_clean,
                direction=direction_clean,
                origin=origin_iata,
                dest=dest_iata,
            )
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
            queries.append(("timetable", {"flight_iata": flight_clean}))
            queries.append(("timetable", {"flight_number": numeric_flight, "airline_iata": carrier_code}))
            if use_future:
                queries.append(("flightsFuture", {"date": date_clean, "flight_iata": flight_clean}))
                queries.append(("flightsFuture", {"date": date_clean, "flight_num": numeric_flight, "airline_iata": carrier_code}))

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
            if os.getenv("TESTING") != "1" and getattr(settings, "AVIATIONSTACK_API_KEY", "").strip():
                try:
                    from app.flight.providers.aviationstack_provider import AviationStackProvider
                    logger.info("[FALLBACK] AviationEdge returned no candidates; falling back to AviationStack for %s", flight_clean)
                    return AviationStackProvider().validate_flight(
                        flight_num=flight_clean,
                        date=date_clean,
                        direction=direction_clean,
                        origin_code=origin_code,
                        destination_code=destination_code,
                        airport_code=airport_code,
                    )
                except Exception as fb_err:
                    logger.warning("[FALLBACK FAILED] AviationStack fallback error for %s: %s", flight_clean, fb_err)

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
                direction=direction_clean,
                origin=origin_iata,
                dest=dest_iata,
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
