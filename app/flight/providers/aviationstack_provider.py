"""
AviationStack Flight Intelligence Provider for Website Flight Fetching and Operations.

Serves as the primary provider for website commercial flight validation, schedule lookups,
and status queries (replacing expired Aviation Edge).
Inherits from the unified FlightProvider interface.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings
from app.core.redis import get_redis_client
from app.flight.airports import build_flight_airport
from app.flight.duration import compute_flight_duration
from app.flight.exceptions import (
    FlightDomainException,
    FlightNotFoundException,
    FlightProviderUnavailableException,
    FlightRateLimitExceededException,
    InvalidFlightDateException,
    InvalidFlightNumberException,
)
from app.flight.provider import FlightProvider
from app.flight.providers.aviation_edge_provider import (
    _retarget_schedule_dates,
    canonical_flight_iata,
    normalize_flight_number,
    split_flight_number,
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
from app.flight.unified_cache import (
    get_unified_flight,
    normalize_flight_code,
    store_unified_flight,
    to_flight_status_data,
)

logger = logging.getLogger("shafsky.flight.aviationstack_provider")

# In-memory RAM cache for fast sub-millisecond retrieval
_IN_MEMORY_CACHE: Dict[str, Tuple[float, Any]] = {}

# Canonical airline names for common carriers
AIRLINE_IATA_MASTER: Dict[str, str] = {
    "6E": "IndiGo",
    "AI": "Air India",
    "UK": "Vistara",
    "SG": "SpiceJet",
    "QP": "Akasa Air",
    "IX": "Air India Express",
    "I5": "Air India Express",
    "EK": "Emirates",
    "QR": "Qatar Airways",
    "ET": "Ethiopian Airlines",
    "EY": "Etihad Airways",
    "LH": "Lufthansa",
    "BA": "British Airways",
    "SQ": "Singapore Airlines",
    "FZ": "flydubai",
    "GF": "Gulf Air",
    "KU": "Kuwait Airways",
    "WY": "Oman Air",
    "SV": "Saudia",
    "J9": "Jazeera Airways",
}

ICAO_TO_IATA: Dict[str, str] = {
    "AIC": "AI",
    "IGO": "6E",
    "SEJ": "SG",
    "VTI": "UK",
    "AXB": "IX",
    "FLG": "9I",
    "GOW": "G8",
    "AKJ": "QP",
    "UAE": "EK",
    "QTR": "QR",
    "ETD": "EY",
    "BAW": "BA",
    "SIA": "SQ",
    "DLH": "LH",
    "AFR": "AF",
    "KLM": "KL",
    "THA": "TG",
    "MAS": "MH",
    "CXA": "CX",
    "FDB": "FZ",
    "GFA": "GF",
    "KAC": "KU",
    "OMA": "WY",
    "SVA": "SV",
    "JZR": "J9",
}


class AviationStackProvider(FlightProvider):
    """
    Production AviationStack Flight Intelligence Provider.
    Implements standard FlightProvider interface for web flight operations.
    """

    DEFAULT_CACHE_TTL = 300  # 5 minutes

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = (api_key or getattr(settings, "AVIATIONSTACK_API_KEY", "") or "").strip()
        self.base_url = (
            base_url or getattr(settings, "AVIATIONSTACK_BASE_URL", "https://api.aviationstack.com/v1")
        ).rstrip("/")
        self.timeout = float(getattr(settings, "AVIATIONSTACK_TIMEOUT", 10.0))
        self.max_retries = int(getattr(settings, "AVIATIONSTACK_MAX_RETRIES", 2))
        self.cache_ttl = int(getattr(settings, "AVIATIONSTACK_CACHE_TTL_SECONDS", 300))

    def _get_redis(self):
        return get_redis_client()

    def _get_cached_data(self, key: str) -> Optional[Any]:
        now = time.time()
        if key in _IN_MEMORY_CACHE:
            expire_at, val = _IN_MEMORY_CACHE[key]
            if now < expire_at:
                logger.debug("[IN-MEMORY CACHE HIT] Key: %s", key)
                return val
            _IN_MEMORY_CACHE.pop(key, None)

        client = self._get_redis()
        if not client:
            return None
        try:
            cached_val = client.get(key)
            if cached_val:
                parsed = json.loads(cached_val)
                _IN_MEMORY_CACHE[key] = (now + self.DEFAULT_CACHE_TTL, parsed)
                logger.debug("[REDIS CACHE HIT] Key: %s", key)
                return parsed
        except Exception as err:
            logger.debug("Redis get failed for key %s: %s", key, err)
        return None

    def _set_cached_data(self, key: str, data: Any, ttl: Optional[int] = None) -> None:
        cache_duration = ttl if ttl is not None else self.DEFAULT_CACHE_TTL
        now = time.time()
        _IN_MEMORY_CACHE[key] = (now + cache_duration, data)
        client = self._get_redis()
        if not client:
            return
        try:
            client.set(key, json.dumps(data), ex=cache_duration)
        except Exception as err:
            logger.debug("Redis set failed for key %s: %s", key, err)

    def _make_request(self, endpoint: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Execute an HTTP GET request to AviationStack with retries, timeout, and masked key logging.
        """
        if not self.api_key:
            logger.warning("AVIATIONSTACK_API_KEY is not configured.")
            return []

        query_params = dict(params)
        query_params["access_key"] = self.api_key

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        log_params = {k: ("***HIDDEN***" if k == "access_key" else v) for k, v in query_params.items()}
        logger.info("[AVIATIONSTACK REQUEST] GET %s | Params: %s", url, log_params)

        for attempt in range(1, self.max_retries + 1):
            try:
                start_time = time.perf_counter()
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.get(url, params=query_params)
                elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
                logger.info(
                    "[AVIATIONSTACK RESPONSE] Status: %s | Latency: %sms | Attempt: %s/%s",
                    response.status_code,
                    elapsed_ms,
                    attempt,
                    self.max_retries,
                )

                if response.status_code == 429:
                    logger.warning("[AVIATIONSTACK RATE LIMITED] Attempt: %s/%s", attempt, self.max_retries)
                    if attempt < self.max_retries:
                        time.sleep(0.4 * attempt)
                        continue
                    raise FlightRateLimitExceededException("AviationStack provider rate limit exceeded.")

                if response.status_code in (401, 403):
                    logger.error("[AVIATIONSTACK AUTH FAILED] Status: %s", response.status_code)
                    return []

                if 500 <= response.status_code < 600:
                    logger.warning("[AVIATIONSTACK SERVER ERROR] Status: %s", response.status_code)
                    if attempt < self.max_retries:
                        time.sleep(0.35 * attempt)
                        continue
                    return []

                if response.status_code != 200:
                    logger.warning("[AVIATIONSTACK UNEXPECTED STATUS] %s", response.status_code)
                    return []

                payload = response.json()
                if isinstance(payload, dict) and payload.get("error"):
                    err = payload.get("error")
                    logger.warning("[AVIATIONSTACK ERROR PAYLOAD] %s", err)
                    return []

                if isinstance(payload, dict):
                    data = payload.get("data")
                    if isinstance(data, list):
                        return data
                return []

            except httpx.TimeoutException:
                logger.warning("[AVIATIONSTACK TIMEOUT] Attempt: %s/%s", attempt, self.max_retries)
                if attempt < self.max_retries:
                    continue
                return []
            except httpx.TransportError as err:
                logger.warning("[AVIATIONSTACK TRANSPORT ERROR] %s", type(err).__name__)
                if attempt < self.max_retries:
                    time.sleep(0.3 * attempt)
                    continue
                return []
            except FlightDomainException:
                raise
            except Exception as err:
                logger.exception("[AVIATIONSTACK UNEXPECTED EXCEPTION] %s", err)
                return []

        return []

    def _normalize_flight_data(
        self,
        raw: Dict[str, Any],
        date_context: Optional[str] = None,
        requested_flight: Optional[str] = None,
    ) -> FlightStatusData:
        """
        Transform raw AviationStack flight item into canonical FlightStatusData schema.
        """
        flight_obj = raw.get("flight") if isinstance(raw.get("flight"), dict) else {}
        airline_obj = raw.get("airline") if isinstance(raw.get("airline"), dict) else {}
        dep_obj = raw.get("departure") if isinstance(raw.get("departure"), dict) else {}
        arr_obj = raw.get("arrival") if isinstance(raw.get("arrival"), dict) else {}
        ac_obj = raw.get("aircraft") if isinstance(raw.get("aircraft"), dict) else {}

        # 1. Resolve Airline Identity
        raw_airline_iata = airline_obj.get("iata") or raw.get("airline_iata") or ""
        airline_iata = str(raw_airline_iata).strip().upper() if raw_airline_iata else None

        flight_iata = (
            flight_obj.get("iata")
            or raw.get("flight_iata")
            or requested_flight
            or ""
        )
        flight_iata = normalize_flight_code(str(flight_iata))

        if not airline_iata and flight_iata:
            m = re.match(r"^([A-Z0-9]{2})", flight_iata)
            if m:
                airline_iata = m.group(1)

        airline_name = (
            airline_obj.get("name")
            or (AIRLINE_IATA_MASTER.get(airline_iata) if airline_iata else None)
            or (f"{airline_iata} Airlines" if airline_iata else None)
        )
        airline_icao = airline_obj.get("icao") or raw.get("airline_icao")
        airline_logo = (
            f"https://images.aviation-edge.com/airline-logos/{airline_iata}.png"
            if airline_iata
            else None
        )

        airline_details = AirlineDetails(
            name=airline_name,
            iata=airline_iata,
            icao=airline_icao,
            logo=airline_logo,
        )

        # 2. Flight Info
        flight_num_digits = str(flight_obj.get("number") or "")
        if not flight_num_digits and flight_iata and airline_iata and flight_iata.startswith(airline_iata):
            flight_num_digits = flight_iata[len(airline_iata):]

        flight_info = FlightInfo(
            number=flight_num_digits,
            iata=flight_iata,
            icao=flight_obj.get("icao") or raw.get("flight_icao"),
            codeshare=str(flight_obj.get("codeshared")) if flight_obj.get("codeshared") else None,
        )

        # 3. Departure Details
        dep_code = (dep_obj.get("iata") or dep_obj.get("iataCode") or "").strip().upper() or None
        dep_terminal = str(dep_obj.get("terminal")).strip() if dep_obj.get("terminal") else None
        dep_gate = str(dep_obj.get("gate")).strip() if dep_obj.get("gate") else None
        dep_ap = build_flight_airport(
            dep_code,
            raw_name=dep_obj.get("airport"),
            terminal=dep_terminal,
            gate=dep_gate,
        ) if dep_code else None

        dep_sched = dep_obj.get("scheduled") or dep_obj.get("scheduledTime")
        dep_est = dep_obj.get("estimated") or dep_obj.get("estimatedTime")
        dep_act = dep_obj.get("actual") or dep_obj.get("actualTime")
        dep_delay = int(dep_obj.get("delay")) if dep_obj.get("delay") is not None else None

        dep_details = LocationEndpointDetails(
            airport=dep_code,
            airport_name=dep_ap.name if dep_ap else dep_obj.get("airport"),
            city=dep_ap.city if dep_ap else None,
            country=dep_ap.country if dep_ap else None,
            terminal=dep_terminal,
            gate=dep_gate,
            scheduled=str(dep_sched).replace(" ", "T") if dep_sched else None,
            estimated=str(dep_est).replace(" ", "T") if dep_est else None,
            actual=str(dep_act).replace(" ", "T") if dep_act else None,
            delay=dep_delay,
            timezone=dep_ap.timezone if dep_ap else dep_obj.get("timezone"),
        )

        # 4. Arrival Details
        arr_code = (arr_obj.get("iata") or arr_obj.get("iataCode") or "").strip().upper() or None
        arr_terminal = str(arr_obj.get("terminal")).strip() if arr_obj.get("terminal") else None
        arr_gate = str(arr_obj.get("gate")).strip() if arr_obj.get("gate") else None
        arr_ap = build_flight_airport(
            arr_code,
            raw_name=arr_obj.get("airport"),
            terminal=arr_terminal,
            gate=arr_gate,
        ) if arr_code else None

        arr_sched = arr_obj.get("scheduled") or arr_obj.get("scheduledTime")
        arr_est = arr_obj.get("estimated") or arr_obj.get("estimatedTime")
        arr_act = arr_obj.get("actual") or arr_obj.get("actualTime")
        arr_delay = int(arr_obj.get("delay")) if arr_obj.get("delay") is not None else None

        arr_details = LocationEndpointDetails(
            airport=arr_code,
            airport_name=arr_ap.name if arr_ap else arr_obj.get("airport"),
            city=arr_ap.city if arr_ap else None,
            country=arr_ap.country if arr_ap else None,
            terminal=arr_terminal,
            gate=arr_gate,
            scheduled=str(arr_sched).replace(" ", "T") if arr_sched else None,
            estimated=str(arr_est).replace(" ", "T") if arr_est else None,
            actual=str(arr_act).replace(" ", "T") if arr_act else None,
            delay=arr_delay,
            timezone=arr_ap.timezone if arr_ap else arr_obj.get("timezone"),
        )

        # 5. Retarget schedule date if requested booking date differs from returned timetable
        if date_context and dep_details.scheduled:
            retargeted_dep, retargeted_arr = _retarget_schedule_dates(
                dep_details.scheduled, arr_details.scheduled, date_context
            )
            if retargeted_dep != dep_details.scheduled:
                dep_details.scheduled = retargeted_dep
                dep_details.estimated = None
                dep_details.actual = None
                dep_details.delay = None
            if retargeted_arr != arr_details.scheduled:
                arr_details.scheduled = retargeted_arr
                arr_details.estimated = None
                arr_details.actual = None
                arr_details.delay = None

        # 6. Duration
        dep_dt = None
        arr_dt = None
        try:
            if dep_details.scheduled:
                dep_dt = datetime.fromisoformat(dep_details.scheduled.replace("Z", "+00:00"))
            if arr_details.scheduled:
                arr_dt = datetime.fromisoformat(arr_details.scheduled.replace("Z", "+00:00"))
        except Exception:
            pass

        dur_mins, dur_text = compute_flight_duration(None, dep_dt, arr_dt, flight_iata)
        duration_details = DurationDetails(minutes=dur_mins, formatted=dur_text)

        # 7. Aircraft Details
        aircraft_details = AircraftDetails(
            model=ac_obj.get("iata") or ac_obj.get("icao"),
            registration=ac_obj.get("registration"),
            icao=ac_obj.get("icao"),
            type=ac_obj.get("iata"),
        )

        raw_status = raw.get("flight_status") or raw.get("status") or "scheduled"
        formatted_status = str(raw_status).capitalize()

        return FlightStatusData(
            airline=airline_details,
            flight=flight_info,
            departure=dep_details,
            arrival=arr_details,
            duration=duration_details,
            aircraft=aircraft_details,
            status=formatted_status,
        )

    def validate_flight(
        self,
        flight_num: str,
        date: str,
        direction: Optional[str] = None,
        origin_code: Optional[str] = None,
        destination_code: Optional[str] = None,
        airport_code: Optional[str] = None,
    ) -> FlightStatusData:
        """
        Validate flight and return structured FlightStatusData schema for website booking.
        """
        if not flight_num or not isinstance(flight_num, str):
            raise InvalidFlightNumberException(str(flight_num or ""))

        try:
            flight_clean = normalize_flight_number(flight_num)
        except Exception:
            flight_clean = normalize_flight_code(flight_num)

        if not flight_clean or len(flight_clean) < 3:
            raise InvalidFlightNumberException(flight_num)

        date_clean = (date.strip()[:10]) if date else datetime.now().strftime("%Y-%m-%d")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_clean):
            raise InvalidFlightDateException(date_clean)

        direction_clean = (direction or "").strip().lower() or None
        origin_iata = (origin_code or "").strip().upper() or None
        dest_iata = (destination_code or "").strip().upper() or None
        service_iata = (airport_code or "").strip().upper() or None

        cache_key = (
            f"flight:validate:aviationstack:{flight_clean}:{date_clean}:{direction_clean or '-'}"
            f":{origin_iata or '-'}:{dest_iata or '-'}:{service_iata or '-'}"
        )

        # ── 1. Check Unified Cross-Provider Cache (RAM -> Redis -> PostgreSQL) ─
        try:
            unified_hit = get_unified_flight(
                flight_clean,
                date_clean,
                direction=direction_clean,
                origin=origin_iata,
                dest=dest_iata,
            )
            if unified_hit:
                cached_status = to_flight_status_data(unified_hit, flight_clean, target_date=date_clean)
                if cached_status:
                    logger.info("[UNIFIED FLIGHT CACHE HIT] Flight: %s (bypassed AviationStack)", flight_clean)
                    return cached_status
        except Exception as err:
            logger.debug("Unified cache check error in AviationStackProvider: %s", err)

        # ── 2. Check Local Provider Cache ─────────────────────────────────────
        cached = self._get_cached_data(cache_key)
        if cached:
            try:
                flight_status = FlightStatusData.model_validate(cached)
                logger.info("[LOCAL CACHE HIT] Flight: %s", flight_clean)
                return flight_status
            except Exception:
                pass

        # ── 3. Fetch from AviationStack API ───────────────────────────────────
        raw_candidates = self._make_request("flights", {"flight_iata": flight_clean, "limit": 10})

        if not raw_candidates:
            # Fallback query by flight_number + airline_iata
            carrier_iata, numeric_flight = split_flight_number(flight_clean)
            if carrier_iata and numeric_flight:
                raw_candidates = self._make_request(
                    "flights",
                    {"flight_number": numeric_flight, "airline_iata": carrier_iata, "limit": 10},
                )

        if not raw_candidates:
            today_str = datetime.now().strftime("%Y-%m-%d")
            logger.warning("[AVIATIONSTACK NO RECORDS] Flight: %s on %s", flight_clean, date_clean)
            raise FlightNotFoundException(flight_num=flight_clean, date=date_clean)

        # ── 4. Candidate Matching & Sector Ranking ────────────────────────────
        valid_candidates = []
        target_canon = canonical_flight_iata(flight_clean)

        for cand in raw_candidates:
            if not isinstance(cand, dict):
                continue
            cand_flight = cand.get("flight") if isinstance(cand.get("flight"), dict) else {}
            cand_iata = cand_flight.get("iata") or cand.get("flight_iata") or ""
            cand_num = cand_flight.get("number") or cand.get("flight_number") or ""

            # Verify flight identity
            if cand_iata and canonical_flight_iata(cand_iata) != target_canon:
                continue
            if not cand_iata and cand_num and str(cand_num) not in flight_clean:
                continue

            valid_candidates.append(cand)

        if not valid_candidates:
            valid_candidates = raw_candidates

        # Sector / Airport filtering
        matched_candidates = []
        for cand in valid_candidates:
            dep_obj = cand.get("departure") if isinstance(cand.get("departure"), dict) else {}
            arr_obj = cand.get("arrival") if isinstance(cand.get("arrival"), dict) else {}
            c_dep = (dep_obj.get("iata") or "").strip().upper()
            c_arr = (arr_obj.get("iata") or "").strip().upper()

            # Origin + Destination sector filter
            if origin_iata and dest_iata:
                if c_dep == origin_iata and c_arr == dest_iata:
                    matched_candidates.append(cand)
            elif origin_iata and c_dep == origin_iata:
                matched_candidates.append(cand)
            elif dest_iata and c_arr == dest_iata:
                matched_candidates.append(cand)
            elif direction_clean in ("departure", "depart") and service_iata:
                if c_dep == service_iata:
                    matched_candidates.append(cand)
            elif direction_clean in ("arrival", "arrive") and service_iata:
                if c_arr == service_iata:
                    matched_candidates.append(cand)
            else:
                matched_candidates.append(cand)

        pool = matched_candidates if matched_candidates else valid_candidates

        # If date match is present, prefer it
        exact_date_candidates = []
        for cand in pool:
            fl_date = str(cand.get("flight_date") or "")[:10]
            dep_date = str(((cand.get("departure") or {}).get("scheduled") or ""))[:10]
            if fl_date == date_clean or dep_date == date_clean:
                exact_date_candidates.append(cand)

        target_candidate = (
            exact_date_candidates[0] if exact_date_candidates else pool[0]
        )

        flight_status = self._normalize_flight_data(
            target_candidate,
            date_context=date_clean,
            requested_flight=flight_clean,
        )

        # ── 5. Cache Results ──────────────────────────────────────────────────
        self._set_cached_data(cache_key, flight_status.model_dump(mode="json"))
        try:
            store_unified_flight(
                flight_clean,
                flight_status.model_dump(mode="json"),
                provider="aviationstack",
                flight_date=date_clean,
                direction=direction_clean,
                origin=origin_iata,
                dest=dest_iata,
            )
        except Exception as err:
            logger.debug("Failed to store unified flight cache: %s", err)

        logger.info(
            "[AVIATIONSTACK SUCCESS] Flight: %s | %s (%s) -> %s (%s)",
            flight_clean,
            flight_status.departure.airport,
            flight_status.departure.scheduled,
            flight_status.arrival.airport,
            flight_status.arrival.scheduled,
        )
        return flight_status

    def get_flight_status(self, flight_num: str) -> FlightStatusData:
        """Retrieve real-time or master flight status for a flight number."""
        flight_clean = normalize_flight_code(flight_num)
        if not flight_clean:
            raise InvalidFlightNumberException(flight_num)

        cache_key = f"flight:status:aviationstack:{flight_clean}"

        # Check unified cache first
        try:
            unified_hit = get_unified_flight(flight_clean)
            if unified_hit:
                cached_status = to_flight_status_data(unified_hit, flight_clean)
                if cached_status:
                    return cached_status
        except Exception:
            pass

        cached = self._get_cached_data(cache_key)
        if cached:
            try:
                return FlightStatusData.model_validate(cached)
            except Exception:
                pass

        results = self._make_request("flights", {"flight_iata": flight_clean, "limit": 5})
        if not results:
            today_str = datetime.now().strftime("%Y-%m-%d")
            raise FlightNotFoundException(flight_num=flight_clean, date=today_str)

        flight_status = self._normalize_flight_data(results[0], requested_flight=flight_clean)
        self._set_cached_data(cache_key, flight_status.model_dump(mode="json"))
        try:
            store_unified_flight(flight_clean, flight_status.model_dump(mode="json"), provider="aviationstack")
        except Exception:
            pass
        return flight_status

    def search_flights(self, query: str) -> List[FlightStatusData]:
        """Search flights by flight number or 3-letter IATA airport code."""
        if not query or not query.strip():
            return []

        q_clean = query.strip().upper()
        cache_key = f"flight:search:aviationstack:{q_clean}"

        cached = self._get_cached_data(cache_key)
        if cached and isinstance(cached, list):
            try:
                return [FlightStatusData.model_validate(item) for item in cached]
            except Exception:
                pass

        if len(q_clean) == 3 and q_clean.isalpha():
            results = self._make_request("flights", {"dep_iata": q_clean, "limit": 10})
        else:
            results = self._make_request("flights", {"flight_iata": q_clean, "limit": 10})

        normalized_list = []
        for item in results:
            try:
                normalized_list.append(self._normalize_flight_data(item, requested_flight=q_clean))
            except Exception as err:
                logger.warning("Skipping unparseable search candidate: %s", err)

        if normalized_list:
            self._set_cached_data(
                cache_key,
                [item.model_dump(mode="json") for item in normalized_list],
            )

        return normalized_list

    def get_live_telemetry(self, flight_num: str) -> FlightTelemetry:
        """Retrieve real-time GPS telemetry from AviationStack."""
        flight_clean = normalize_flight_code(flight_num)
        cache_key = f"flight:telemetry:aviationstack:{flight_clean}"

        cached = self._get_cached_data(cache_key)
        if cached:
            try:
                return FlightTelemetry.model_validate(cached)
            except Exception:
                pass

        results = self._make_request("flights", {"flight_iata": flight_clean, "limit": 1})
        if not results:
            raise FlightNotFoundException(
                flight_num=flight_clean,
                date=datetime.now().strftime("%Y-%m-%d"),
            )

        target = results[0]
        live = target.get("live") if isinstance(target.get("live"), dict) else {}
        if not live or live.get("latitude") is None or live.get("longitude") is None:
            raise FlightNotFoundException(
                flight_num=flight_clean,
                date=datetime.now().strftime("%Y-%m-%d"),
            )

        def _to_float(v: Any) -> float:
            if v is None:
                return 0.0
            try:
                return float(v)
            except (ValueError, TypeError):
                return 0.0

        telemetry = FlightTelemetry(
            latitude=_to_float(live.get("latitude")),
            longitude=_to_float(live.get("longitude")),
            altitude=_to_float(live.get("altitude")),
            heading=_to_float(live.get("direction")),
            speed=_to_float(live.get("speed_horizontal")),
        )
        self._set_cached_data(cache_key, telemetry.model_dump(mode="json"), ttl=60)
        return telemetry
