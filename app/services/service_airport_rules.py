"""
Authoritative service-airport resolution rules.

Arrival  → service airport = destination (must be Shafsky-supported)
Departure → service airport = origin (must be Shafsky-supported)
Transit  → service airport = transit airport (must be Shafsky-supported)

Origin/destination/other airports may be any real IATA code.
"""

from typing import Optional, Tuple


def normalize_iata(code: Optional[str]) -> str:
    if not code:
        return ""
    return str(code).strip().upper()


def normalize_journey_type(journey_type: Optional[str]) -> str:
    jt = (journey_type or "").strip().upper()
    if jt in ("ARRIVAL", "ARR", "INBOUND"):
        return "ARRIVAL"
    if jt in ("DEPARTURE", "DEP", "OUTBOUND"):
        return "DEPARTURE"
    if jt in ("TRANSIT", "CONNECTION", "CONNECTING", "LAYOVER"):
        return "TRANSIT"
    return "ARRIVAL"


def normalize_flight_type(flight_type: Optional[str]) -> Optional[str]:
    if not flight_type:
        return None
    ft = str(flight_type).strip().upper()
    if ft in ("DOMESTIC", "DOM", "D"):
        return "DOMESTIC"
    if ft in ("INTERNATIONAL", "INTL", "INT", "I"):
        return "INTERNATIONAL"
    if ft in (
        "DOMESTIC_DOMESTIC",
        "DOMESTIC_INTERNATIONAL",
        "INTERNATIONAL_DOMESTIC",
        "INTERNATIONAL_INTERNATIONAL",
    ):
        return ft
    return None


def resolve_service_airport_iata(
    journey_type: Optional[str],
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    transit: Optional[str] = None,
) -> str:
    jt = normalize_journey_type(journey_type)
    if jt == "ARRIVAL":
        return normalize_iata(destination)
    if jt == "DEPARTURE":
        return normalize_iata(origin)
    return normalize_iata(transit)


def flight_route_matches_service_airport(
    journey_type: Optional[str],
    service_airport: Optional[str],
    actual_origin: Optional[str] = None,
    actual_destination: Optional[str] = None,
    actual_transit: Optional[str] = None,
) -> Tuple[bool, str]:
    svc = normalize_iata(service_airport)
    if not svc:
        return False, "Service airport is required."

    jt = normalize_journey_type(journey_type)
    if jt == "ARRIVAL":
        actual = normalize_iata(actual_destination)
        if not actual:
            return False, "Could not determine the flight destination. Please verify the flight number."
        if actual != svc:
            return False, (
                f"This flight arrives at {actual}, but arrival services were selected for {svc}. "
                "Please verify the flight number and airport. The service airport was not changed."
            )
        return True, ""

    if jt == "DEPARTURE":
        actual = normalize_iata(actual_origin)
        if not actual:
            return False, "Could not determine the flight origin. Please verify the flight number."
        if actual != svc:
            return False, (
                f"This flight departs from {actual}, but departure services were selected for {svc}. "
                "Please verify the flight number and airport. The service airport was not changed."
            )
        return True, ""

    actual = normalize_iata(actual_transit)
    if not actual:
        return False, (
            f"Could not confirm a connection at {svc} from this flight. "
            "Please verify the flight number and transit airport."
        )
    if actual != svc:
        return False, (
            f"This itinerary connects via {actual}, but transit services were selected for {svc}. "
            "Please verify the flight number and airport. The service airport was not changed."
        )
    return True, ""


# ──────────────────────────────────────────────────────────
# Canonical Domestic / International Route Classification
# ──────────────────────────────────────────────────────────
# ALL India-vs-non-India logic lives EXCLUSIVELY here.
# No other file should duplicate this classification rule.
# ──────────────────────────────────────────────────────────

_INDIA_IDENTIFIERS = frozenset({"INDIA", "IN", "IND"})


def _is_india(country_value: str) -> bool:
    """Returns True if the country value identifies India."""
    return country_value.strip().upper() in _INDIA_IDENTIFIERS


def resolve_airport_country(db, iata_code: Optional[str]) -> str:
    """
    Resolve the country for an IATA airport code using authoritative database sources.

    Lookup priority:
      1. supported_airports table (Shafsky's 20 operating hubs, has 'country' column)
      2. airports table (global 85K+ airports, has 'iso_country' and 'country' columns,
         keyed by 'iata_code')

    Returns the country string (uppercased).
    Raises ValueError if the airport is not found or country is unavailable.
    """
    from sqlalchemy import select

    code = normalize_iata(iata_code)
    if not code:
        raise ValueError(
            "Airport code is missing. Please provide a valid IATA airport code."
        )

    # 1. Check supported_airports (Shafsky hubs) — keyed by iata_code
    from app.models.journey_models import SupportedAirport

    supported = db.scalar(
        select(SupportedAirport).where(SupportedAirport.iata_code == code)
    )
    if supported and supported.country:
        return supported.country.strip().upper()

    # 2. Check global airports table.
    #    The DB has an 'iata_code' column not mapped in the ORM, plus 'code' which
    #    sometimes stores IATA codes. Also has 'iso_country' not in the ORM.
    from app.models.schema import AirportManagement
    from sqlalchemy import or_, column

    global_ap = db.scalar(
        select(AirportManagement).where(
            or_(
                column("iata_code") == code,
                AirportManagement.code == code,
            )
        )
    )
    if global_ap:
        # Prefer iso_country (2-letter ISO code), fall back to country
        country_val = getattr(global_ap, "iso_country", None) or global_ap.country
        if country_val and str(country_val).strip():
            return str(country_val).strip().upper()

    raise ValueError(
        f"Unable to determine the country for airport '{code}'. "
        f"Please provide a valid origin and destination airport."
    )


def derive_flight_type_from_route(
    db,
    origin_code: Optional[str],
    dest_code: Optional[str],
    journey_type: Optional[str],
) -> Optional[str]:
    """
    Canonical route classification for ARRIVAL / DEPARTURE journeys.

    Rules:
      - India → India           = DOMESTIC
      - India → Outside India   = INTERNATIONAL
      - Outside India → India   = INTERNATIONAL
      - Outside India → Outside = INTERNATIONAL

    For TRANSIT: returns None (transit uses its own compound types).

    Raises ValueError if origin or destination is missing/unknown
    for ARRIVAL/DEPARTURE journeys.
    """
    jt = normalize_journey_type(journey_type)

    # Transit has its own compound classification — do not apply two-point logic
    if jt == "TRANSIT":
        return None

    # For ARRIVAL / DEPARTURE, both origin and destination must be resolvable
    origin_clean = normalize_iata(origin_code)
    dest_clean = normalize_iata(dest_code)

    if not origin_clean and not dest_clean:
        raise ValueError(
            "Unable to determine whether this route is domestic or international. "
            "Please provide valid origin and destination airports."
        )

    if not origin_clean:
        raise ValueError(
            "Origin airport is missing. Unable to classify this route as "
            "domestic or international."
        )

    if not dest_clean:
        raise ValueError(
            "Destination airport is missing. Unable to classify this route as "
            "domestic or international."
        )

    origin_country = resolve_airport_country(db, origin_clean)
    dest_country = resolve_airport_country(db, dest_clean)

    origin_is_india = _is_india(origin_country)
    dest_is_india = _is_india(dest_country)

    if origin_is_india and dest_is_india:
        return "DOMESTIC"

    return "INTERNATIONAL"


def resolve_catalog_flight_type(
    db,
    origin_code: Optional[str],
    dest_code: Optional[str],
    journey_type: Optional[str],
    client_flight_type: Optional[str] = None,
) -> Optional[str]:
    """
    Authoritative flight_type for loading airport service catalogs.

    Client DOMESTIC / INTERNATIONAL is never used when both route
    endpoints are known. Derive from origin/destination countries first.

    TRANSIT: returns the client compound type unchanged (never two-point).
    ARRIVAL/DEPARTURE with both origin and dest: route-derived value.
    ARRIVAL/DEPARTURE with neither origin nor dest: browsing — client hint.
    ARRIVAL/DEPARTURE with incomplete or unknown airports: raises ValueError.
      Do not guess DOMESTIC.
    """
    jt = normalize_journey_type(journey_type)

    if jt == "TRANSIT":
        return normalize_flight_type(client_flight_type)

    origin_clean = normalize_iata(origin_code)
    dest_clean = normalize_iata(dest_code)

    if origin_clean and dest_clean:
        return derive_flight_type_from_route(db, origin_clean, dest_clean, jt)

    if origin_clean or dest_clean:
        raise ValueError(
            "Unable to determine whether this route is domestic or international. "
            "Please provide valid origin and destination airports."
        )

    return normalize_flight_type(client_flight_type)
