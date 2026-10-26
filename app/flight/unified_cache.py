"""
Unified Multi-Tier Flight Cache Engine for Shafsky Aviation.
Synchronizes flight lookups across AeroDataBox, Aviation Edge, and AviationStack.

Architecture:
1. Tier 1: Process In-Memory RAM Cache (< 1ms, zero network)
2. Tier 2: Distributed Redis Cache (Shared across containers & workers)
3. Tier 3: PostgreSQL Database FlightAPICache (Long-lived flight cache)
4. Tier 4: PostgreSQL Persistent FlightStatusRecord (Persistent operational record: route, actual times, terminals, gates)
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
from app.models.schema import FlightAPICache, FlightStatusRecord
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

DEFAULT_RAM_TTL = 3600         # 1 hour in RAM
DEFAULT_REDIS_TTL = 86400      # 24 hours in Redis
DEFAULT_DB_TTL = 31536000      # 1 year in DB (persistent)


def normalize_flight_code(code: Optional[str]) -> str:
    """Normalize flight number to uppercase alphanumeric without spaces (e.g. 'ai 101' -> 'AI101')."""
    if not code:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", code).upper().strip()


def _parse_iso_dt(val: Any) -> Optional[datetime]:
    """Parse string or datetime to timezone-aware datetime."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    s = str(val).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def build_cache_keys(
    flight_iata: str,
    flight_date: Optional[str] = None,
    *,
    direction: Optional[str] = None,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    include_latest: bool = True,
) -> List[str]:
    """Generate canonical cache keys for flight lookup."""
    clean_flight = normalize_flight_code(flight_iata)
    clean_date = flight_date[:10].strip() if flight_date else None
    sector = ""
    dir_part = (direction or "").strip().lower()
    origin_part = (origin or "").strip().upper()
    dest_part = (dest or "").strip().upper()
    if dir_part or origin_part or dest_part:
        sector = f":{dir_part or '-'}:{origin_part or '-'}:{dest_part or '-'}"
        include_latest = False

    keys = []
    if clean_date and clean_date not in ("ANY", "unknown", "None", ""):
        keys.append(f"shafsky:flight:{clean_flight}:{clean_date}{sector}")
    if include_latest:
        keys.append(f"shafsky:flight:{clean_flight}:LATEST")
    elif not keys:
        keys.append(f"shafsky:flight:{clean_flight}{sector}")
    return keys


def get_unified_flight(
    flight_iata: str,
    flight_date: Optional[str] = None,
    *,
    direction: Optional[str] = None,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Retrieve flight from Tier 1 (RAM) -> Tier 2 (Redis) -> Tier 3 (PostgreSQL DB).
    Returns raw JSON dict if found, or None if external API fetch is required.
    """
    clean_flight = normalize_flight_code(flight_iata)
    if not clean_flight:
        return None

    keys = build_cache_keys(
        clean_flight,
        flight_date,
        direction=direction,
        origin=origin,
        dest=dest,
        include_latest=not (direction or origin or dest),
    )
    clean_date = flight_date[:10].strip() if flight_date else None
    fallback_keys = []
    # Only fall back to unsectored keys when no specific direction or route was requested
    if not (direction or origin or dest):
        if clean_date and clean_date not in ("ANY", "unknown", "None", ""):
            fallback_keys.append(f"shafsky:flight:{clean_flight}:{clean_date}")
        fallback_keys.append(f"shafsky:flight:{clean_flight}:LATEST")
        fallback_keys.append(f"shafsky:flight:{clean_flight}:PERSISTENT")

    now_ts = time.time()

    def _matches_route(payload: Dict[str, Any]) -> bool:
        """Verify that cached flight sector matches user's route if specified."""
        if not payload or not isinstance(payload, dict):
            return False
        if payload.get("not_found"):
            return False
        cand_dep = ((payload.get("departure") or {}).get("airport") or (payload.get("departure") or {}).get("iata") or "").upper()
        cand_arr = ((payload.get("arrival") or {}).get("airport") or (payload.get("arrival") or {}).get("iata") or "").upper()
        if origin and cand_dep and cand_dep != origin.strip().upper():
            return False
        if dest and cand_arr and cand_arr != dest.strip().upper():
            return False
        return True

    # ── Tier 1: In-Memory RAM Cache (< 1ms) ──────────────────────────────────
    with _RAM_LOCK:
        for k in keys + fallback_keys:
            if k in _RAM_CACHE:
                exp, data = _RAM_CACHE[k]
                if now_ts < exp and _matches_route(data):
                    logger.info("Unified Flight Cache [RAM HIT] | Flight: %s | Key: %s", clean_flight, k)
                    return data
                elif now_ts >= exp:
                    _RAM_CACHE.pop(k, None)

    # ── Tier 2: Redis Distributed Cache ──────────────────────────────────────
    try:
        r_client = get_redis_client()
        if r_client:
            for k in keys + fallback_keys:
                raw = r_client.get(k)
                if raw:
                    parsed = json.loads(raw)
                    if _matches_route(parsed):
                        with _RAM_LOCK:
                            _RAM_CACHE[k] = (now_ts + DEFAULT_RAM_TTL, parsed)
                        logger.info("Unified Flight Cache [REDIS HIT] | Flight: %s | Key: %s", clean_flight, k)
                        return parsed
    except Exception as err:
        logger.debug("Redis lookup error: %s", err)

    # ── Tier 3 & 4: PostgreSQL Database Cache ────────────────────────────────
    try:
        now_dt = datetime.now(timezone.utc)
        with SessionLocal() as session:
            # Check FlightAPICache
            query = session.query(FlightAPICache).filter(
                FlightAPICache.flight_iata == clean_flight,
                FlightAPICache.expires_at > now_dt,
            )
            record = None
            if clean_date and clean_date not in ("ANY", "unknown", "None", ""):
                record = query.filter(FlightAPICache.flight_date == clean_date).order_by(FlightAPICache.created_at.desc()).first()
            if not record and not (direction or origin or dest):
                record = query.filter(FlightAPICache.flight_date.in_(["LATEST", "PERPETUAL"])).order_by(FlightAPICache.created_at.desc()).first()
            if not record and not (direction or origin or dest):
                record = query.order_by(FlightAPICache.created_at.desc()).first()

            if record and isinstance(record.response_data, dict) and _matches_route(record.response_data):
                # When direction is requested without origin/dest, don't return unsectored DB cache
                if not direction or (origin and dest):
                    data = record.response_data
                    logger.info("Unified Flight Cache [DB FlightAPICache HIT] | Flight: %s | Provider: %s", clean_flight, record.provider)
                    store_unified_flight(
                        clean_flight,
                        data,
                        provider=record.provider,
                        flight_date=record.flight_date,
                        skip_db=True,
                        direction=direction,
                        origin=origin,
                        dest=dest,
                    )
                    return data

            # Check persistent FlightStatusRecord (requires route or exact date, not unsectored direction query)
            if (origin and dest) or (clean_date and clean_date not in ("ANY", "unknown", "None", "") and not direction):
                fs_query = session.query(FlightStatusRecord).filter(
                    FlightStatusRecord.flight_number == clean_flight
                )
                if origin:
                    fs_query = fs_query.filter(FlightStatusRecord.origin_code == origin.strip().upper())
                if dest:
                    fs_query = fs_query.filter(FlightStatusRecord.dest_code == dest.strip().upper())

                fs_rec = fs_query.order_by(FlightStatusRecord.updated_at.desc()).first()
                if fs_rec and isinstance(fs_rec.raw_details, dict) and _matches_route(fs_rec.raw_details):
                    data = fs_rec.raw_details
                    logger.info(
                        "Unified Flight Cache [DB FlightStatusRecord HIT] | Flight: %s | Route: %s -> %s",
                        clean_flight,
                        fs_rec.origin_code,
                        fs_rec.dest_code,
                    )
                    store_unified_flight(
                        clean_flight,
                        data,
                        provider="flight_status_record",
                        flight_date=clean_date,
                        skip_db=True,
                        direction=direction,
                        origin=origin,
                        dest=dest,
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
    *,
    direction: Optional[str] = None,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
) -> None:
    """
    Store flight record across Tier 1 (RAM), Tier 2 (Redis), Tier 3 (FlightAPICache),
    and Tier 4 (FlightStatusRecord) with full route, actual times, terminals, and gate numbers.
    Always safely persisted for future reuse.
    """
    clean_flight = normalize_flight_code(flight_iata)
    if not clean_flight or not isinstance(data, dict):
        return

    clean_date = flight_date[:10].strip() if flight_date else None
    dep_dict = data.get("departure") or {}
    arr_dict = data.get("arrival") or {}

    if not clean_date or clean_date in ("ANY", "unknown", "None", ""):
        sched = dep_dict.get("scheduled")
        if sched and len(str(sched)) >= 10:
            clean_date = str(sched)[:10]
        else:
            clean_date = data.get("flight_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    now_ts = time.time()
    now_dt = datetime.now(timezone.utc)
    keys = build_cache_keys(
        clean_flight,
        clean_date,
        direction=direction,
        origin=origin,
        dest=dest,
        include_latest=not (direction or origin or dest),
    )
    if not (direction or origin or dest):
        keys.append(f"shafsky:flight:{clean_flight}:{clean_date}")
        keys.append(f"shafsky:flight:{clean_flight}:LATEST")
        keys.append(f"shafsky:flight:{clean_flight}:PERSISTENT")
    unique_keys = list(dict.fromkeys(keys))

    serialized = json.dumps(data)

    # 1. Store in RAM Cache (< 1ms)
    with _RAM_LOCK:
        for k in unique_keys:
            _RAM_CACHE[k] = (now_ts + min(ttl, DEFAULT_RAM_TTL), data)

    # 2. Store in Redis Distributed Cache
    try:
        r_client = get_redis_client()
        if r_client:
            for k in unique_keys:
                r_client.setex(k, min(ttl, DEFAULT_REDIS_TTL), serialized)
    except Exception as err:
        logger.debug("Redis store error: %s", err)

    # 3. Store in PostgreSQL DB (FlightAPICache & FlightStatusRecord)
    if not skip_db:
        try:
            expires_at = now_dt + timedelta(seconds=max(ttl, DEFAULT_DB_TTL))
            with SessionLocal() as session:
                # 3a. Save into FlightAPICache
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

                # Perpetual entry for future queries beyond schedule publishing horizon
                session.query(FlightAPICache).filter(
                    FlightAPICache.flight_iata == clean_flight,
                    FlightAPICache.flight_date == "PERPETUAL",
                ).delete()
                perpetual_entry = FlightAPICache(
                    provider=provider,
                    flight_iata=clean_flight,
                    flight_date="PERPETUAL",
                    response_data=data,
                    expires_at=expires_at,
                )
                session.add(perpetual_entry)

                # 3b. Upsert into FlightStatusRecord (dedicated operational relational model)
                dep_iata = (dep_dict.get("airport") or dep_dict.get("iata") or origin or "").strip().upper()
                arr_iata = (arr_dict.get("airport") or arr_dict.get("iata") or dest or "").strip().upper()
                carrier_iata = ((data.get("airline") or {}).get("iata") or data.get("airline_iata") or clean_flight[:2]).strip().upper()

                dep_sched_dt = _parse_iso_dt(dep_dict.get("scheduled")) or now_dt
                arr_sched_dt = _parse_iso_dt(arr_dict.get("scheduled"))
                dep_act_dt = _parse_iso_dt(dep_dict.get("actual"))
                arr_act_dt = _parse_iso_dt(arr_dict.get("actual"))
                dep_est_dt = _parse_iso_dt(dep_dict.get("estimated"))
                arr_est_dt = _parse_iso_dt(arr_dict.get("estimated"))

                if dep_iata and arr_iata:
                    existing_fs = session.query(FlightStatusRecord).filter(
                        FlightStatusRecord.flight_number == clean_flight,
                        FlightStatusRecord.origin_code == dep_iata,
                        FlightStatusRecord.dest_code == arr_iata,
                    ).order_by(FlightStatusRecord.created_at.desc()).first()

                    if existing_fs:
                        existing_fs.airline_code = carrier_iata
                        existing_fs.status = str(data.get("status") or "SCHEDULED").upper()
                        existing_fs.departure_terminal = dep_dict.get("terminal") or existing_fs.departure_terminal
                        existing_fs.arrival_terminal = arr_dict.get("terminal") or existing_fs.arrival_terminal
                        existing_fs.departure_gate = dep_dict.get("gate") or existing_fs.departure_gate
                        existing_fs.arrival_gate = arr_dict.get("gate") or existing_fs.arrival_gate
                        existing_fs.baggage_belt = dep_dict.get("baggage_belt") or dep_dict.get("baggageBelt") or existing_fs.baggage_belt
                        existing_fs.checkin_counter = dep_dict.get("checkin_counter") or dep_dict.get("checkInDesk") or existing_fs.checkin_counter
                        existing_fs.scheduled_departure = dep_sched_dt
                        if arr_sched_dt:
                            existing_fs.scheduled_arrival = arr_sched_dt
                        if dep_act_dt:
                            existing_fs.actual_departure = dep_act_dt
                        if arr_act_dt:
                            existing_fs.actual_arrival = arr_act_dt
                        if dep_est_dt:
                            existing_fs.estimated_departure = dep_est_dt
                        if arr_est_dt:
                            existing_fs.estimated_arrival = arr_est_dt
                        existing_fs.raw_details = data
                        existing_fs.updated_at = now_dt
                    else:
                        fs_entry = FlightStatusRecord(
                            flight_number=clean_flight,
                            airline_code=carrier_iata,
                            origin_code=dep_iata,
                            dest_code=arr_iata,
                            status=str(data.get("status") or "SCHEDULED").upper(),
                            departure_terminal=dep_dict.get("terminal"),
                            arrival_terminal=arr_dict.get("terminal"),
                            departure_gate=dep_dict.get("gate"),
                            arrival_gate=arr_dict.get("gate"),
                            baggage_belt=arr_dict.get("baggage_belt") or arr_dict.get("baggageBelt"),
                            checkin_counter=dep_dict.get("checkin_counter") or dep_dict.get("checkInDesk"),
                            scheduled_departure=dep_sched_dt,
                            scheduled_arrival=arr_sched_dt,
                            actual_departure=dep_act_dt,
                            actual_arrival=arr_act_dt,
                            estimated_departure=dep_est_dt,
                            estimated_arrival=arr_est_dt,
                            raw_details=data,
                            created_at=now_dt,
                            updated_at=now_dt,
                        )
                        session.add(fs_entry)

                session.commit()
                logger.info(
                    "Unified Flight Cache [SAFELY PERSISTED TO DB] | Flight: %s | Route: %s -> %s | Date: %s | Terminals: %s / %s | Gates: %s / %s",
                    clean_flight,
                    dep_iata,
                    arr_iata,
                    clean_date,
                    dep_dict.get("terminal"),
                    arr_dict.get("terminal"),
                    dep_dict.get("gate"),
                    arr_dict.get("gate"),
                )
        except Exception as err:
            logger.debug("DB flight cache store error: %s", err)


def to_flight_status_data(
    cached: Dict[str, Any],
    requested_flight: str,
    target_date: Optional[str] = None
) -> Optional[FlightStatusData]:
    """Convert any unified cached flight dictionary into a FlightStatusData schema, retargeting date if specified."""
    if not isinstance(cached, dict) or cached.get("not_found"):
        return None

    clean_fl = normalize_flight_code(requested_flight)

    # Check if already in FlightStatusData format
    if "airline" in cached and "flight" in cached and "departure" in cached and "arrival" in cached:
        try:
            status_obj = FlightStatusData.model_validate(cached)
            if target_date:
                cached_dep_date = (status_obj.departure.scheduled or "")[:10]
                if cached_dep_date and cached_dep_date != target_date:
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
        delay=dep_raw.get("delay"),
        timezone=dep_raw.get("timezone"),
        checkin_counter=dep_raw.get("checkin_counter") or dep_raw.get("checkInDesk"),
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
        delay=arr_raw.get("delay"),
        timezone=arr_raw.get("timezone"),
        baggage_belt=arr_raw.get("baggage_belt") or arr_raw.get("baggageBelt"),
    )

    fl_num_part = clean_fl[len(carrier_iata):] if clean_fl.startswith(carrier_iata) else clean_fl

    # Extract or compute duration details
    dur_raw = cached.get("duration")
    if isinstance(dur_raw, dict) and (dur_raw.get("minutes") or dur_raw.get("formatted")):
        duration_details = DurationDetails(
            minutes=dur_raw.get("minutes"),
            formatted=dur_raw.get("formatted"),
        )
    else:
        duration_details = DurationDetails()
        if dep_sched and arr_sched:
            try:
                from app.flight.duration import compute_flight_duration
                d_dt = datetime.fromisoformat(str(dep_sched).replace("Z", "+00:00"))
                a_dt = datetime.fromisoformat(str(arr_sched).replace("Z", "+00:00"))
                mins, text = compute_flight_duration(None, d_dt, a_dt, clean_fl)
                duration_details = DurationDetails(minutes=mins, formatted=text)
            except Exception:
                pass

    # Extract aircraft details if available
    ac_raw = cached.get("aircraft")
    if isinstance(ac_raw, dict):
        aircraft_details = AircraftDetails(
            model=ac_raw.get("model"),
            registration=ac_raw.get("registration"),
            icao=ac_raw.get("icao"),
            type=ac_raw.get("type"),
            distance=ac_raw.get("distance"),
        )
    else:
        aircraft_details = AircraftDetails()

    return FlightStatusData(
        airline=AirlineDetails(name=airline_name, iata=carrier_iata, logo=airline_logo),
        flight=FlightInfo(number=fl_num_part, iata=clean_fl),
        departure=dep_details,
        arrival=arr_details,
        duration=duration_details,
        aircraft=aircraft_details,
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
            "delay": dep_obj.get("delay"),
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
            "delay": arr_obj.get("delay"),
        },
        "status": cached.get("status") or "scheduled",
        "flight_date": ((dep_obj.get("scheduled") or "")[:10]) or cached.get("flight_date"),
        "provider": cached.get("provider") or "shared",
    }


def clear_unified_cache() -> None:
    """Drop RAM + PostgreSQL flight cache (tests). Redis keys are flushed by conftest."""
    with _RAM_LOCK:
        _RAM_CACHE.clear()
    try:
        from app.flight.providers import aviationstack_provider as as_mod
        as_mod._NOT_FOUND_CACHE.clear()
        as_mod._IN_MEMORY_CACHE.clear()
    except Exception:
        pass
    try:
        from app.flight.providers import aviation_edge_provider as ae_mod
        ae_mod._IN_MEMORY_CACHE.clear()
    except Exception:
        pass
    try:
        with SessionLocal() as session:
            session.query(FlightAPICache).delete()
            session.query(FlightStatusRecord).delete()
            session.commit()
    except Exception as err:
        logger.debug("DB flight cache clear error: %s", err)
