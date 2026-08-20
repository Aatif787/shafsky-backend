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
