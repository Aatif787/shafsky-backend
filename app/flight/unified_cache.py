"""
Unified Multi-Tier Flight Cache Engine for Shafsky Aviation.
Synchronizes flight lookups across Aviation Edge (Web/API) and AviationStack (WhatsApp).

Architecture:
1. Tier 1: Process In-Memory RAM Cache (< 1ms, zero network)
2. Tier 2: Distributed Redis Cache (Shared across containers & workers)
3. Tier 3: PostgreSQL Database Cache (Persistent across server restarts)
"""

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.redis import get_redis_client
from app.database import SessionLocal
from app.models.schema import FlightAPICache
from app.flight.schemas import (
    AircraftDetails,
    AirlineDetails,
    DurationDetails,
    FlightInfo,
    FlightStatusData,
    LocationEndpointDetails,
)

logger = logging.getLogger("shafsky.flight.unified_cache")

_RAM_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_RAM_LOCK = threading.Lock()

DEFAULT_RAM_TTL = 900       # 15 minutes in RAM
DEFAULT_REDIS_TTL = 7200    # 2 hours in Redis
DEFAULT_DB_TTL = 86400      # 24 hours in DB


def normalize_flight_code(code: Optional[str]) -> str:
    """Normalize flight number to uppercase alphanumeric without spaces (e.g. 'ai 101' -> 'AI101')."""
    if not code:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(code)).upper().strip()


def build_cache_keys(flight_iata: str, flight_date: Optional[str] = None) -> List[str]:
    """Generate canonical cache keys for flight lookup."""
    clean_flight = normalize_flight_code(flight_iata)
    clean_date = str(flight_date)[:10].strip() if flight_date else None
    
    keys = []
    if clean_date and clean_date not in ("ANY", "unknown", "None", ""):
        keys.append(f"shafsky:flight:{clean_flight}:{clean_date}")
    keys.append(f"shafsky:flight:{clean_flight}:LATEST")
    return keys


def get_unified_flight(
    flight_iata: str,
    flight_date: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Retrieve flight from Tier 1 (RAM) -> Tier 2 (Redis) -> Tier 3 (PostgreSQL DB).
    Returns raw JSON dict if found, or None if external API fetch is required.
    """
    clean_flight = normalize_flight_code(flight_iata)
    if not clean_flight:
        return None

    keys = build_cache_keys(clean_flight, flight_date)
    now_ts = time.time()

    # ── Tier 1: In-Memory RAM Cache (< 1ms) ──────────────────────────────────
    with _RAM_LOCK:
        for k in keys:
            if k in _RAM_CACHE:
                exp, data = _RAM_CACHE[k]
                if now_ts < exp:
                    logger.info("Unified Flight Cache [RAM HIT] | Flight: %s | Key: %s", clean_flight, k)
                    return data
                else:
                    _RAM_CACHE.pop(k, None)

    # ── Tier 2: Redis Distributed Cache ──────────────────────────────────────
    try:
        r_client = get_redis_client()
        if r_client:
            for k in keys:
                raw = r_client.get(k)
                if raw:
                    parsed = json.loads(raw)
                    # Promote to RAM cache
                    with _RAM_LOCK:
                        _RAM_CACHE[k] = (now_ts + DEFAULT_RAM_TTL, parsed)
                    logger.info("Unified Flight Cache [REDIS HIT] | Flight: %s | Key: %s", clean_flight, k)
                    return parsed
    except Exception as err:
        logger.debug("Redis lookup error: %s", err)

    # ── Tier 3: PostgreSQL Database Cache ─────────────────────────────────────
    try:
        now_dt = datetime.now(timezone.utc)
        clean_date = str(flight_date)[:10].strip() if flight_date else None
        
        with SessionLocal() as session:
            query = session.query(FlightAPICache).filter(
                FlightAPICache.flight_iata == clean_flight,
                FlightAPICache.expires_at > now_dt,
            )
            if clean_date and clean_date not in ("ANY", "unknown", "None", ""):
                record = query.filter(FlightAPICache.flight_date == clean_date).order_by(FlightAPICache.created_at.desc()).first()
            else:
                record = query.order_by(FlightAPICache.created_at.desc()).first()

            if record and isinstance(record.response_data, dict):
                data = record.response_data
                logger.info("Unified Flight Cache [DB HIT] | Flight: %s | Provider: %s", clean_flight, record.provider)
                
                # Promote to RAM and Redis
                store_unified_flight(
                    clean_flight,
                    data,
                    provider=record.provider,
                    flight_date=record.flight_date,
                    skip_db=True
                )
                return data
    except Exception as err:
        logger.debug("DB flight cache lookup error: %s", err)

    return None


def store_unified_flight(
    flight_iata: str,
    data: Dict[str, Any],
    provider: str = "shared",
    flight_date: Optional[str] = None,
    ttl: int = DEFAULT_DB_TTL,
    skip_db: bool = False,
) -> None:
    """
    Store flight record across Tier 1 (RAM), Tier 2 (Redis), and Tier 3 (PostgreSQL DB).
    """
    clean_flight = normalize_flight_code(flight_iata)
    if not clean_flight or not isinstance(data, dict):
        return

    clean_date = str(flight_date)[:10].strip() if flight_date else None
    if not clean_date or clean_date in ("ANY", "unknown", "None", ""):
        # Try extracting from payload
        dep = data.get("departure") or {}
        sched = dep.get("scheduled")
        if sched and len(str(sched)) >= 10:
            clean_date = str(sched)[:10]
        else:
            clean_date = data.get("flight_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    now_ts = time.time()
    keys = build_cache_keys(clean_flight, clean_date)
    serialized = json.dumps(data)

    # 1. Store in RAM
    with _RAM_LOCK:
        for k in keys:
            _RAM_CACHE[k] = (now_ts + DEFAULT_RAM_TTL, data)

    # 2. Store in Redis
    try:
        r_client = get_redis_client()
        if r_client:
            for k in keys:
                r_client.setex(k, min(ttl, DEFAULT_REDIS_TTL), serialized)
    except Exception as err:
        logger.debug("Redis store error: %s", err)

    # 3. Store in PostgreSQL DB
    if not skip_db:
        try:
            now_dt = datetime.now(timezone.utc)
            expires_at = now_dt + timedelta(seconds=ttl)
            with SessionLocal() as session:
                # Remove any expired or conflicting records for this flight & date
                session.query(FlightAPICache).filter(
                    FlightAPICache.flight_iata == clean_flight,
                    FlightAPICache.flight_date == clean_date,
                ).delete()

                cache_entry = FlightAPICache(
                    provider=provider,
                    flight_iata=clean_flight,
                    flight_date=clean_date,
                    response_data=data,
                    expires_at=expires_at,
                )
                session.add(cache_entry)
                session.commit()
                logger.info("Unified Flight Cache [STORED] | Flight: %s | Date: %s | Provider: %s", clean_flight, clean_date, provider)
        except Exception as err:
            logger.debug("DB flight cache store error: %s", err)


def to_flight_status_data(
    cached: Dict[str, Any],
    requested_flight: str,
    target_date: Optional[str] = None
) -> Optional[FlightStatusData]:
    """Convert any unified cached flight dictionary into a FlightStatusData schema, retargeting date if specified."""
    if not isinstance(cached, dict):
        return None

    clean_fl = normalize_flight_code(requested_flight)

    # Check if already in FlightStatusData format
    if "airline" in cached and "flight" in cached and "departure" in cached and "arrival" in cached:
        try:
            status_obj = FlightStatusData.model_validate(cached)
            if target_date:
                from app.flight.providers.aviation_edge_provider import _retarget_schedule_dates
                dep_sched, arr_sched = _retarget_schedule_dates(
                    status_obj.departure.scheduled,
                    status_obj.arrival.scheduled,
                    target_date
                )
                if dep_sched != status_obj.departure.scheduled:
                    status_obj.departure = status_obj.departure.model_copy(
                        update={"scheduled": dep_sched, "estimated": None, "actual": None, "delay": None}
                    )
                if arr_sched != status_obj.arrival.scheduled:
                    status_obj.arrival = status_obj.arrival.model_copy(
                        update={"scheduled": arr_sched, "estimated": None, "actual": None, "delay": None}
                    )
            return status_obj
        except Exception:
            pass

    # Convert from AviationStack or WhatsApp dictionary format
    carrier_iata = (cached.get("airline_iata") or clean_fl[:2]).upper()
    airline_name = cached.get("airline") or f"{carrier_iata} Airlines"
    airline_logo = f"https://images.aviation-edge.com/airline-logos/{carrier_iata}.png"

    dep_raw = cached.get("departure") or {}
    arr_raw = cached.get("arrival") or {}

    dep_sched = dep_raw.get("scheduled")
    arr_sched = arr_raw.get("scheduled")

    dep_est = dep_raw.get("estimated")
    dep_act = dep_raw.get("actual")
    arr_est = arr_raw.get("estimated")
    arr_act = arr_raw.get("actual")

    if target_date:
        from app.flight.providers.aviation_edge_provider import _retarget_schedule_dates
        retargeted_dep, retargeted_arr = _retarget_schedule_dates(
            dep_sched,
            arr_sched,
            target_date
        )
        if retargeted_dep != dep_sched:
            dep_sched = retargeted_dep
            dep_est = None
            dep_act = None
        if retargeted_arr != arr_sched:
            arr_sched = retargeted_arr
            arr_est = None
            arr_act = None

    dep_details = LocationEndpointDetails(
        airport=(dep_raw.get("iata") or dep_raw.get("airport") or "").upper(),
        airport_name=dep_raw.get("airport_name") or dep_raw.get("airport"),
        city=dep_raw.get("city"),
        country=dep_raw.get("country"),
        terminal=dep_raw.get("terminal"),
        gate=dep_raw.get("gate"),
        scheduled=dep_sched,
        estimated=dep_est,
        actual=dep_act,
    )

    arr_details = LocationEndpointDetails(
        airport=(arr_raw.get("iata") or arr_raw.get("airport") or "").upper(),
        airport_name=arr_raw.get("airport_name") or arr_raw.get("airport"),
        city=arr_raw.get("city"),
        country=arr_raw.get("country"),
        terminal=arr_raw.get("terminal"),
        gate=arr_raw.get("gate"),
        scheduled=arr_sched,
        estimated=arr_est,
        actual=arr_act,
    )

    fl_num_part = clean_fl[len(carrier_iata):] if clean_fl.startswith(carrier_iata) else clean_fl

    return FlightStatusData(
        airline=AirlineDetails(name=airline_name, iata=carrier_iata, logo=airline_logo),
        flight=FlightInfo(number=fl_num_part, iata=clean_fl),
        departure=dep_details,
        arrival=arr_details,
        duration=DurationDetails(),
        aircraft=AircraftDetails(),
        status=cached.get("status") or "Scheduled",
    )


def to_aviationstack_record(cached: Dict[str, Any], requested_flight: str) -> Dict[str, Any]:
    """Convert any unified cached flight dictionary into AviationStack record format."""
    clean_fl = normalize_flight_code(requested_flight)

    # If already in AviationStack normalized format
    if "flight_number" in cached and "departure" in cached and "arrival" in cached:
        return cached

    # Extract from FlightStatusData format
    airline_obj = cached.get("airline") or {}
    flight_obj = cached.get("flight") or {}
    dep_obj = cached.get("departure") or {}
    arr_obj = cached.get("arrival") or {}

    dep_iata = dep_obj.get("airport") or dep_obj.get("iata")
    arr_iata = arr_obj.get("airport") or arr_obj.get("iata")

    return {
        "flight_number": flight_obj.get("iata") or clean_fl,
        "airline": airline_obj.get("name"),
        "airline_iata": airline_obj.get("iata") or clean_fl[:2],
        "departure": {
            "airport": dep_obj.get("airport_name") or dep_iata,
            "timezone": dep_obj.get("timezone"),
            "iata": dep_iata,
            "scheduled": dep_obj.get("scheduled"),
            "estimated": dep_obj.get("estimated"),
            "actual": dep_obj.get("actual"),
            "terminal": dep_obj.get("terminal"),
            "gate": dep_obj.get("gate"),
        },
        "arrival": {
            "airport": arr_obj.get("airport_name") or arr_iata,
            "timezone": arr_obj.get("timezone"),
            "iata": arr_iata,
            "scheduled": arr_obj.get("scheduled"),
            "estimated": arr_obj.get("estimated"),
            "actual": arr_obj.get("actual"),
            "terminal": arr_obj.get("terminal"),
            "gate": arr_obj.get("gate"),
        },
        "status": cached.get("status") or "scheduled",
        "flight_date": ((dep_obj.get("scheduled") or "")[:10]) or cached.get("flight_date"),
        "provider": cached.get("provider") or "shared",
    }
