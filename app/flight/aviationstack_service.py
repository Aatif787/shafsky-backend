"""
AviationStack secondary flight verification for WhatsApp booking confirmation.

Aviation Edge remains the primary provider for website /api/flights/validate.
This module must never log or return the API access key.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import zlib
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from app.database import SessionLocal
from app.models.schema import FlightAPICache
from app.config import settings
from app.flight.providers.aviation_edge_provider import canonical_flight_iata
from app.services.service_airport_rules import (
    flight_route_matches_service_airport,
    normalize_iata,
    normalize_journey_type,
)
logger = logging.getLogger("shafsky.flight.aviationstack")

REASON_OK = "OK"
REASON_NOT_CONFIGURED = "NOT_CONFIGURED"
REASON_INVALID_FLIGHT_NUMBER = "INVALID_FLIGHT_NUMBER"
REASON_FLIGHT_NOT_FOUND = "FLIGHT_NOT_FOUND"
REASON_MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
REASON_AIRPORT_MISMATCH = "AIRPORT_MISMATCH"
REASON_API_ERROR = "API_ERROR"
REASON_RATE_LIMITED = "RATE_LIMITED"
REASON_TIMEOUT = "TIMEOUT"
REASON_AMBIGUOUS = "AMBIGUOUS"

ICAO_TO_IATA: Dict[str, str] = {
    "AIC": "AI",  # Air India
    "IGO": "6E",  # IndiGo
    "SEJ": "SG",  # SpiceJet
    "VTI": "UK",  # Vistara
    "AXB": "IX",  # Air India Express
    "FLG": "9I",  # Alliance Air
    "GOW": "G8",  # Go First
    "AKJ": "QP",  # Akasa Air
    "UAE": "EK",  # Emirates
    "QTR": "QR",  # Qatar Airways
    "ETD": "EY",  # Etihad Airways
    "BAW": "BA",  # British Airways
    "SIA": "SQ",  # Singapore Airlines
    "DLH": "LH",  # Lufthansa
    "AFR": "AF",  # Air France
    "KLM": "KL",  # KLM
    "THA": "TG",  # Thai Airways
    "MAS": "MH",  # Malaysia Airlines
    "CXA": "CX",  # Cathay Pacific
    "FDB": "FZ",  # flydubai
    "GFA": "GF",  # Gulf Air
    "KAC": "KU",  # Kuwait Airways
    "OMA": "WY",  # Oman Air
    "SVA": "SV",  # Saudia
    "JZR": "J9",  # Jazeera Airways
}


def normalize_flight_number_input(flight_num_input: str) -> Optional[str]:
    """Normalize user flight input and convert standard 3-letter ICAO codes to 2-letter IATA."""
    if not flight_num_input or not isinstance(flight_num_input, str):
        return None
    clean = re.sub(r"[\s\-_]+", "", str(flight_num_input).strip().upper())
    if not clean or len(clean) < 3 or len(clean) > 8:
        return None
    match = re.match(r"^([A-Z0-9]{2,3})(\d{1,4}[A-Z]?)$", clean)
    if not match:
        return None
    airline_code, flight_digits = match.group(1), match.group(2)
    if not re.search(r"[A-Z]", airline_code) or not re.search(r"\d", flight_digits):
        return None

    # Auto-convert ICAO to IATA where available
    if airline_code in ICAO_TO_IATA:
        airline_code = ICAO_TO_IATA[airline_code]

    return f"{airline_code}{flight_digits}"


def _normalize_endpoint(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    iata = normalize_iata(raw.get("iata") or raw.get("iataCode") or "")
    if not iata or len(iata) != 3:
        return None
    airport_name = raw.get("airport") or raw.get("airport_name")
    airport_name = airport_name.strip() if isinstance(airport_name, str) and airport_name.strip() else None
    city_raw = raw.get("city")
    city = city_raw.strip() if isinstance(city_raw, str) and city_raw.strip() else None
    return {
        "iata": iata,
        "airport": airport_name,
        "city": city,
        "terminal": (str(raw["terminal"]).strip() if raw.get("terminal") else None),
        "scheduled": raw.get("scheduled") or raw.get("scheduledTime") or None,
    }


# Canonical airline names for carriers where provider data is frequently
# missing or inaccurate (Indian carriers especially). Identity is owned
# internally; providers only supply schedules/status.
AIRLINE_IATA_MASTER = {
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
    "QR": "Qatar Airways",
    "LH": "Lufthansa",
    "BA": "British Airways",
}

# In-process negative cache: remember provider-level not-found results for a
# short window so repeated customer retries do not burn provider quota.
_NEGATIVE_CACHE = {}
_NEGATIVE_CACHE_TTL_SECONDS = 900


def _register_negative_result(flight_iata: str) -> None:
    """Mark a flight as not-found across providers for a short window."""
    import time as _time
    _NEGATIVE_CACHE[normalize_flight_number_input(flight_iata)] = (
        _time.time() + _NEGATIVE_CACHE_TTL_SECONDS
    )


def _is_negative_cached(flight_iata: str) -> bool:
    import time as _time
    key = normalize_flight_number_input(flight_iata)
    exp = _NEGATIVE_CACHE.get(key)
    if exp is None:
        return False
    if _time.time() >= exp:
        _NEGATIVE_CACHE.pop(key, None)
        return False
    return True

def _normalize_flight_record(raw: Dict[str, Any], requested_flight: str) -> Optional[Dict[str, Any]]:
    flight_obj = raw.get("flight") if isinstance(raw.get("flight"), dict) else {}
    airline_obj = raw.get("airline") if isinstance(raw.get("airline"), dict) else {}
    departure = _normalize_endpoint(raw.get("departure"))
    arrival = _normalize_endpoint(raw.get("arrival"))
    if not departure or not arrival:
        return None

    flight_iata = flight_obj.get("iata") or raw.get("flight_iata") or requested_flight
    flight_iata = normalize_flight_number_input(str(flight_iata or "")) or requested_flight

    airline_name = airline_obj.get("name")
    airline_name = airline_name.strip() if isinstance(airline_name, str) and airline_name.strip() else None

    airline_iata = airline_obj.get("iata") or airline_obj.get("iataCode")
    airline_iata = normalize_iata(str(airline_iata)) if airline_iata else None
    if airline_iata and len(airline_iata) > 3:
        airline_iata = airline_iata[:3]

    if not airline_name and airline_iata:
        airline_name = AIRLINE_IATA_MASTER.get(airline_iata)

    if not airline_name or not airline_iata:
        # The flight designator itself defines the carrier (IATA: AI2424 is
        # always an Air India flight). Derive identity from the master when
        # the provider record omits it entirely.
        import re as _re
        m_carrier = _re.match(r"^([A-Z0-9]{2})", flight_iata or "")
        if m_carrier:
            carrier_from_flight = m_carrier.group(1)
            master_name = AIRLINE_IATA_MASTER.get(carrier_from_flight)
            if master_name:
                airline_name = airline_name or master_name
                airline_iata = airline_iata or carrier_from_flight

    return {
        "flight_number": flight_iata,
        "airline": airline_name,
        "airline_iata": airline_iata,
        "departure": departure,
        "arrival": arrival,
        "status": raw.get("flight_status") or raw.get("status") or None,
        "flight_date": raw.get("flight_date") or None,
        "provider": "aviationstack",
    }


def _match_route(journey_type: str, service_airport: str, flight: Dict[str, Any]) -> Tuple[bool, str]:
    dep = (flight.get("departure") or {}).get("iata")
    arr = (flight.get("arrival") or {}).get("iata")
    jt = normalize_journey_type(journey_type)
    if jt == "TRANSIT":
        svc = normalize_iata(service_airport)
        if svc and svc in {normalize_iata(dep), normalize_iata(arr)}:
            return flight_route_matches_service_airport(
                jt, svc, actual_origin=dep, actual_destination=arr, actual_transit=svc
            )
        return flight_route_matches_service_airport(
            jt, svc, actual_origin=dep, actual_destination=arr, actual_transit=None
        )
    return flight_route_matches_service_airport(
        jt, service_airport, actual_origin=dep, actual_destination=arr
    )


def _flight_calendar_date(item: Dict[str, Any]) -> Optional[str]:
    raw = item.get("flight_date")
    if raw and len(str(raw)) >= 10:
        return str(raw)[:10]
    sched = ((item.get("departure") or {}).get("scheduled"))
    if sched and len(str(sched)) >= 10:
        return str(sched)[:10]
    return None


def _route_key(item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    dep = (item.get("departure") or {}).get("iata")
    arr = (item.get("arrival") or {}).get("iata")
    return (normalize_iata(dep) or None, normalize_iata(arr) or None)


def _select_best_record(
    records: List[Dict[str, Any]],
    requested_flight: str,
    service_airport: Optional[str],
    journey_type: Optional[str],
    travel_date: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Pick a verified record without guessing a different route.
    Returns (flight, reason). reason is OK, FLIGHT_NOT_FOUND, MALFORMED_RESPONSE,
    AIRPORT_MISMATCH, or AMBIGUOUS.
    """
    normalized: List[Dict[str, Any]] = []
    saw_raw = False
    for raw in records:
        if not isinstance(raw, dict):
            continue
        saw_raw = True
        item = _normalize_flight_record(raw, requested_flight)
        if not item:
            continue
        if canonical_flight_iata(item["flight_number"]) != canonical_flight_iata(requested_flight):
            continue
        normalized.append(item)

    if saw_raw and not normalized:
        return None, REASON_MALFORMED_RESPONSE
    if not normalized:
        return None, REASON_FLIGHT_NOT_FOUND

    svc = normalize_iata(service_airport)
    jt = normalize_journey_type(journey_type) if journey_type else ""
    matched: List[Dict[str, Any]] = []
    if svc and jt:
        matched = [item for item in normalized if _match_route(jt, svc, item)[0]]

    pool = list(matched)
    date_clean = str(travel_date).strip()[:10] if travel_date else ""
    if date_clean and pool:
        dated = [item for item in pool if _flight_calendar_date(item) == date_clean]
        if dated:
            pool = dated
        else:
            return None, REASON_FLIGHT_NOT_FOUND

    if pool:
        routes = {_route_key(item) for item in pool}
        if len(routes) > 1:
            return None, REASON_AMBIGUOUS
        # prefer complete records (with airline identity) over sparse ones -
        # provider responses can mix codeshare rows that share the same route
        pool.sort(key=lambda item: 0 if item.get("airline") else 1)
        return pool[0], REASON_OK

    # API returned a real flight, but it does not serve the selected airport.
    return normalized[0], REASON_AIRPORT_MISMATCH


def _http_fetch_flights(flight_iata: str) -> Dict[str, Any]:
    api_key = (settings.AVIATIONSTACK_API_KEY or "").strip()
    if not api_key:
        return {"ok": False, "reason": REASON_NOT_CONFIGURED, "records": []}

    base = (settings.AVIATIONSTACK_BASE_URL or "https://api.aviationstack.com/v1").rstrip("/")
    url = f"{base}/flights"
    timeout = float(getattr(settings, "AVIATIONSTACK_TIMEOUT", 5.0) or 5.0)
    max_retries = max(1, int(getattr(settings, "AVIATIONSTACK_MAX_RETRIES", 2) or 2))

    params = {"access_key": api_key, "flight_iata": flight_iata, "limit": 10}
    log_params = {"flight_iata": flight_iata, "limit": 10, "access_key": "***HIDDEN***"}
    logger.info(
        "Flight verification requested | Flight: %s | Provider: AviationStack | Params: %s",
        flight_iata,
        log_params,
    )

    last_reason = REASON_API_ERROR
    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url, params=params)

            if resp.status_code == 429:
                last_reason = REASON_RATE_LIMITED
                logger.warning(
                    "AviationStack rate limited | Flight: %s | Attempt: %s/%s",
                    flight_iata,
                    attempt,
                    max_retries,
                )
                if attempt < max_retries:
                    time.sleep(0.4 * attempt)
                    continue
                return {"ok": False, "reason": REASON_RATE_LIMITED, "records": []}

            if resp.status_code in (401, 403):
                logger.error(
                    "AviationStack auth failed | Flight: %s | Status: %s",
                    flight_iata,
                    resp.status_code,
                )
                return {"ok": False, "reason": REASON_API_ERROR, "records": []}

            if 500 <= resp.status_code < 600:
                last_reason = REASON_API_ERROR
                logger.warning(
                    "AviationStack 5xx | Flight: %s | Status: %s | Attempt: %s/%s",
                    flight_iata,
                    resp.status_code,
                    attempt,
                    max_retries,
                )
                if attempt < max_retries:
                    time.sleep(0.35 * attempt)
                    continue
                return {"ok": False, "reason": REASON_API_ERROR, "records": []}

            if resp.status_code != 200:
                logger.warning(
                    "AviationStack unexpected status | Flight: %s | Status: %s",
                    flight_iata,
                    resp.status_code,
                )
                return {"ok": False, "reason": REASON_API_ERROR, "records": []}

            try:
                payload = resp.json()
            except Exception:
                return {"ok": False, "reason": REASON_MALFORMED_RESPONSE, "records": []}

            if isinstance(payload, dict) and payload.get("error"):
                err = payload.get("error")
                code = ""
                if isinstance(err, dict):
                    code = str(err.get("code") or err.get("type") or "")
                logger.info(
                    "AviationStack API error object | Flight: %s | code=%s",
                    flight_iata,
                    code or "unknown",
                )
                return {"ok": False, "reason": REASON_API_ERROR, "records": []}

            if not isinstance(payload, dict):
                return {"ok": False, "reason": REASON_MALFORMED_RESPONSE, "records": []}

            data = payload.get("data")
            if data is None or not isinstance(data, list):
                return {"ok": False, "reason": REASON_MALFORMED_RESPONSE, "records": []}
            if len(data) == 0:
                return {"ok": False, "reason": REASON_FLIGHT_NOT_FOUND, "records": []}

            return {"ok": True, "reason": REASON_OK, "records": data}

        except httpx.TimeoutException:
            last_reason = REASON_TIMEOUT
            logger.warning(
                "AviationStack timeout | Flight: %s | Attempt: %s/%s",
                flight_iata,
                attempt,
                max_retries,
            )
            if attempt < max_retries:
                continue
            return {"ok": False, "reason": REASON_TIMEOUT, "records": []}
        except httpx.TransportError as exc:
            last_reason = REASON_API_ERROR
            logger.warning(
                "AviationStack transport error | Flight: %s | Attempt: %s/%s | err=%s",
                flight_iata,
                attempt,
                max_retries,
                type(exc).__name__,
            )
            if attempt < max_retries:
                time.sleep(0.3 * attempt)
                continue
            return {"ok": False, "reason": REASON_API_ERROR, "records": []}
        except Exception as exc:
            logger.exception(
                "AviationStack unexpected failure | Flight: %s | err=%s",
                flight_iata,
                type(exc).__name__,
            )
            return {"ok": False, "reason": REASON_API_ERROR, "records": []}

    return {"ok": False, "reason": last_reason, "records": []}


def format_route_label(flight: Dict[str, Any]) -> str:
    dep = flight.get("departure") or {}
    arr = flight.get("arrival") or {}
    dep_city = dep.get("city") or dep.get("airport") or dep.get("iata") or "?"
    arr_city = arr.get("city") or arr.get("airport") or arr.get("iata") or "?"
    return f"{dep_city} ({dep.get('iata')}) → {arr_city} ({arr.get('iata')})"


def _edge_fallback_verification(flight_iata: str, svc: Optional[str], jt: Optional[str]) -> Optional[Dict[str, Any]]:
    """Strict AviationEdge fallback for WhatsApp verification.

    Accepts an Edge record ONLY when the provider record itself carries the
    requested flight number (no self-labeling), the airline is present, and the
    route matches the selected service airport + journey. Anything less returns
    None and the caller falls through to not-found.
    """
    try:
        from app.flight.providers.aviation_edge_provider import AviationEdgeProvider
        provider = AviationEdgeProvider()
        # Bound the fallback so a slow Edge API can never stall the webhook thread:
        provider.max_retries = 1
        provider.timeout = 5.0
        probe_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        rows = provider._make_request("timetable", {"key": provider.api_key, "flight_iata": flight_iata, "limit": 5})
    except Exception as err:
        logger.info("Edge fallback fetch error | Flight: %s | %s", flight_iata, type(err).__name__)
        return None

    if not isinstance(rows, list):
        return None

    requested_canon = canonical_flight_iata(flight_iata)
    for row in rows:
        try:
            status_data = provider._normalize_flight_data(row)
        except Exception:
            continue
        fd = status_data.model_dump()
        flight_obj = fd.get("flight") or {}
        rec_iata = flight_obj.get("iata") or flight_obj.get("number")
        if not rec_iata:
            continue  # provider record without a flight number: reject, never self-label
        if canonical_flight_iata(str(rec_iata)) != requested_canon:
            continue  # a different flight entirely: never substitute
        airline_obj = fd.get("airline") or {}
        if not (airline_obj.get("iata") or airline_obj.get("name")):
            continue
        dep = fd.get("departure") or {}
        arr = fd.get("arrival") or {}
        if not (dep.get("iata") and arr.get("iata")):
            continue
        rec = {
            "flight": {"iata": rec_iata, "number": flight_iata},
            "airline": {"iata": airline_obj.get("iata"), "name": airline_obj.get("name")},
            "departure": {"iata": dep.get("iata"), "airport": dep.get("airport"), "scheduled": dep.get("scheduled")},
            "arrival": {"iata": arr.get("iata"), "airport": arr.get("airport"), "scheduled": arr.get("scheduled")},
            "status": fd.get("status") or "scheduled",
            "flight_date": (str(dep.get("scheduled")) or "")[:10] or None,
            "provider": "aviation_edge",
        }
        n_item = _normalize_flight_record(rec, flight_iata)
        if not n_item:
            continue
        if svc:
            route_ok, _label = _match_route(jt or "DEPARTURE", svc, n_item)
            if not route_ok:
                continue
        n_item["provider"] = "aviation_edge"
        return n_item
    return None

def verify_flight_for_whatsapp(
    flight_number: str,
    *,
    selected_airport_iata: Optional[str],
    journey_type: Optional[str],
    selected_airport_name: Optional[str] = None,
    travel_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Authoritative AviationStack verification for WhatsApp confirmation.

    Returns {success, reason, flight?, message_context?}. Never includes secrets.
    A format-valid flight number is never treated as verified.
    """
    normalized = normalize_flight_number_input(flight_number)
    if not normalized:
        return {"success": False, "reason": REASON_INVALID_FLIGHT_NUMBER, "flight": None}

    if os.getenv("TESTING") != "1" and _is_negative_cached(normalized):
        logger.info("Flight verification | Flight: %s | Negative cache hit", normalized)
        return {"success": False, "reason": REASON_FLIGHT_NOT_FOUND, "flight": None}

    svc = normalize_iata(selected_airport_iata)
    jt = normalize_journey_type(journey_type) if journey_type else "DEPARTURE"

    # ── 1. Check Unified Cross-Provider Flight Cache (RAM -> Redis -> DB) ─
    fast_cache_key = f"fast_flight:wa:{normalized}:{svc or 'ALL'}:{jt or 'ALL'}:{travel_date or 'ALL'}"
    if os.getenv("TESTING") != "1":
        # unified-cache try-block removed: provider-untagged cache served stale/
        # Edge-sourced records into WhatsApp verification (see commit notes).

        # ── High-Performance Redis L1 Cache Check (< 1ms) ──────────────────────
        try:
            from app.core.redis import get_redis_client
            r_client = get_redis_client()
            if r_client:
                cached_raw = r_client.get(fast_cache_key)
                if cached_raw:
                    logger.info("Flight verification Redis L1 cache hit (<1ms) | Flight: %s", normalized)
                    return json.loads(cached_raw)
        except Exception:
            pass

    ttl = int(getattr(settings, "AVIATIONSTACK_CACHE_TTL_SECONDS", 300) or 300)

    lock_id = zlib.crc32(normalized.encode("utf-8"))
    flight = None
    select_reason = REASON_FLIGHT_NOT_FOUND

    with SessionLocal() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock_id})

        now = datetime.now(timezone.utc)
        cached_records = session.query(FlightAPICache).filter(
            FlightAPICache.provider == "aviationstack",
            FlightAPICache.flight_iata == normalized,
            FlightAPICache.expires_at > now
        ).all()

        if cached_records:
            logger.info(
                "Flight verification DB cache hit | Flight: %s | Provider: AviationStack",
                normalized,
            )
            records = [r.response_data for r in cached_records]
            flight, select_reason = _select_best_record(
                records, normalized, svc, jt, travel_date=travel_date
            )
        else:
            fetched = _http_fetch_flights(normalized)
            if not fetched.get("ok"):
                reason = fetched.get("reason") or REASON_API_ERROR
                logger.info(
                    "Flight verification requested | Flight: %s | Provider: AviationStack | Result: %s",
                    normalized,
                    reason,
                )
                return {"success": False, "reason": reason, "flight": None}

            records = fetched.get("records") or []
            flight, select_reason = _select_best_record(
                records, normalized, svc, jt, travel_date=travel_date
            )
            if select_reason in (REASON_FLIGHT_NOT_FOUND, REASON_MALFORMED_RESPONSE, REASON_AMBIGUOUS):
                if os.getenv("TESTING") != "1":
                    edge_flight = _edge_fallback_verification(normalized, svc, jt)
                    if edge_flight is not None:
                        logger.info("Flight verification | Flight: %s | Provider: AviationEdge (fallback)", normalized)
                        return {"success": True, "reason": REASON_OK, "flight": edge_flight}
                    _register_negative_result(normalized)
                logger.info(
                    "Flight verification requested | Flight: %s | Provider: AviationStack | Result: %s",
                    normalized,
                    select_reason,
                )
                return {"success": False, "reason": select_reason, "flight": None}

            expires_at = now + timedelta(seconds=ttl)
            session.query(FlightAPICache).filter(
                FlightAPICache.provider == "aviationstack",
                FlightAPICache.flight_iata == normalized
            ).delete()

            seen_dates = set()
            for item in records:
                n_item = _normalize_flight_record(item, normalized)
                if not n_item:
                    continue

                f_date = n_item.get("flight_date")
                if not f_date:
                    sched = (n_item.get("departure") or {}).get("scheduled")
                    if sched and len(str(sched)) >= 10:
                        f_date = str(sched)[:10]
                    else:
                        f_date = "unknown"

                if f_date in seen_dates:
                    continue
                seen_dates.add(f_date)

                cache_obj = FlightAPICache(
                    provider="aviationstack",
                    flight_iata=normalized,
                    flight_date=f_date,
                    response_data=item,
                    expires_at=expires_at
                )
                session.add(cache_obj)
            session.commit()

    if select_reason == REASON_AMBIGUOUS:
        return {"success": False, "reason": REASON_AMBIGUOUS, "flight": None}
    if select_reason in (REASON_FLIGHT_NOT_FOUND, REASON_MALFORMED_RESPONSE) or not flight:
        return {
            "success": False,
            "reason": select_reason or REASON_FLIGHT_NOT_FOUND,
            "flight": None,
        }

    if not svc:
        return {
            "success": False,
            "reason": REASON_AIRPORT_MISMATCH,
            "flight": flight,
            "message_context": {
                "flight_number": flight.get("flight_number") or normalized,
                "route_label": format_route_label(flight),
                "selected_airport_iata": "",
                "selected_airport_name": selected_airport_name or "",
            },
        }

    ok, _match_msg = _match_route(jt, svc, flight)
    if not ok or select_reason == REASON_AIRPORT_MISMATCH:
        logger.info(
            "Flight verification requested | Flight: %s | Provider: AviationStack | Result: AIRPORT_MISMATCH | Route: %s → %s | Selected: %s | Journey: %s",
            normalized,
            (flight.get("departure") or {}).get("iata"),
            (flight.get("arrival") or {}).get("iata"),
            svc,
            jt,
        )
        return {
            "success": False,
            "reason": REASON_AIRPORT_MISMATCH,
            "flight": flight,
            "message_context": {
                "flight_number": flight.get("flight_number") or normalized,
                "airline": flight.get("airline"),
                "route_label": format_route_label(flight),
                "selected_airport_iata": svc,
                "selected_airport_name": selected_airport_name or svc,
                "journey_type": jt,
            },
        }

    logger.info(
        "Flight verification requested | Flight: %s | Provider: AviationStack | Result: SUCCESS | Route: %s → %s",
        normalized,
        (flight.get("departure") or {}).get("iata"),
        (flight.get("arrival") or {}).get("iata"),
    )
    success_payload = {
        "success": True,
        "reason": REASON_OK,
        "flight": flight,
        "message_context": {
            "flight_number": flight.get("flight_number") or normalized,
            "airline": flight.get("airline"),
            "route_label": format_route_label(flight),
            "selected_airport_iata": svc,
            "selected_airport_name": selected_airport_name or svc,
            "journey_type": jt,
            "departure": flight.get("departure"),
            "arrival": flight.get("arrival"),
        },
    }
    try:
        from app.core.redis import get_redis_client
        rc = get_redis_client()
        if rc:
            rc.setex(fast_cache_key, 3600, json.dumps(success_payload))
    except Exception:
        pass

    try:
        from app.flight.unified_cache import store_unified_flight
        store_unified_flight(
            normalized,
            flight if isinstance(flight, dict) else {},
            provider="aviationstack",
            flight_date=travel_date,
        )
    except Exception:
        pass

    return success_payload


def _format_endpoint_when(endpoint: Optional[Dict[str, Any]]) -> str:
    if not isinstance(endpoint, dict):
        return "—"
    raw = endpoint.get("scheduled")
    if not raw:
        return "—"
    text = str(raw).replace("T", " ").strip()
    return text[:16] if len(text) >= 16 else text


def _format_airport_line(endpoint: Optional[Dict[str, Any]]) -> str:
    if not isinstance(endpoint, dict):
        return "—"
    iata = endpoint.get("iata") or ""
    name = endpoint.get("airport") or endpoint.get("city") or iata or "—"
    if iata and iata not in str(name):
        return f"{name} ({iata})"
    return str(name)


def build_whatsapp_verified_message(
    *,
    flight: Dict[str, Any],
    selected_airport_iata: str,
    selected_airport_name: Optional[str],
    journey_type: Optional[str],
) -> str:
    jt = normalize_journey_type(journey_type)
    airline = flight.get("airline") or flight.get("airline_iata") or "—"
    flight_no = flight.get("flight_number") or "—"
    dep = flight.get("departure") or {}
    arr = flight.get("arrival") or {}
    origin = _format_airport_line(dep)
    destination = _format_airport_line(arr)
    if jt == "ARRIVAL":
        relevant_airport = _format_airport_line(arr)
    elif jt == "DEPARTURE":
        relevant_airport = _format_airport_line(dep)
    else:
        relevant_airport = selected_airport_name or selected_airport_iata or _format_airport_line(dep)

    return (
        "✈️ *Flight Details*\n\n"
        f"✈ *Flight Number:* {flight_no}\n"
        f"*Airline:* {airline}\n"
        f"*Origin:* {origin}\n"
        f"*Destination:* {destination}\n"
        f"*Departure:* {_format_endpoint_when(dep)}\n"
        f"*Arrival:* {_format_endpoint_when(arr)}\n"
        f"*Airport:* {relevant_airport}\n\n"
        "Flight verified successfully."
    )


def build_whatsapp_mismatch_message(
    *,
    flight: Optional[Dict[str, Any]],
    flight_number: str,
    selected_airport_iata: str,
    selected_airport_name: Optional[str],
) -> str:
    if flight:
        route = format_route_label(flight)
        shown_flight = flight.get("flight_number") or flight_number
    else:
        route = "Unavailable"
        shown_flight = flight_number
    airport_label = selected_airport_name or selected_airport_iata
    return (
        "The verified flight details do not match the selected airport.\n\n"
        f"*Flight:* {shown_flight}\n"
        f"*Verified route:* {route}\n"
        f"*Selected service airport:* {airport_label} ({selected_airport_iata})\n\n"
        "Please verify this information carefully."
    )


def customer_failure_message(reason: str) -> str:
    if reason == REASON_FLIGHT_NOT_FOUND:
        return (
            "❌ We could not verify this flight number.\n"
            "Please check the number and try again."
        )
    if reason == REASON_INVALID_FLIGHT_NUMBER:
        return "Please enter a valid flight number (e.g., *EK501*, *AI2424*, *6E224*)."
    if reason == REASON_AMBIGUOUS:
        return (
            "We found more than one matching flight. "
            "Please re-enter the flight number with your travel date, or try again."
        )
    if reason == REASON_MALFORMED_RESPONSE:
        return (
            "❌ We could not verify this flight number.\n"
            "Please check the number and try again."
        )
    if reason in (
        REASON_TIMEOUT,
        REASON_RATE_LIMITED,
        REASON_API_ERROR,
        REASON_NOT_CONFIGURED,
    ):
        return (
            "We could not verify this flight right now. Please try again in a moment, "
            "or re-enter your flight number."
        )
    return "We could not verify this flight. Please re-enter your flight number."
