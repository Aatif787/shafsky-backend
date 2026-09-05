import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


IXR_DOMESTIC_DEPARTURE_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE (CHARGES APPLICABLE)",
    "ASSIST GUEST TILL THE BOARDING GATE",
]

IXR_DOMESTIC_ARRIVAL_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE WITH DEDICATED STAFF (Through Airlines)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]


def seed_ixr_production_packages(db: Session, ixr_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    Configure authoritative production packages for Ranchi Airport (IXR).
    Domestic Departure: INR 2500.00
    Domestic Arrival: INR 2500.00
    Verbatim service inclusions stored exactly with 'ASSIST' action wording.
    International Departure, International Arrival, and Transit are unconfigured (is_available = False).
    """
    print("\n-- Configuring Production Packages for Ranchi Airport (IXR) --")

    silver_svc = service_map.get("silver")
    if not silver_svc:
        raise RuntimeError("IXR requires catalog service slug=silver")

    # Deactivate all existing IXR mappings to start with clean authoritative state
    db.query(AirportService).filter(
        AirportService.airport_id == ixr_airport.id
    ).update({"is_available": False}, synchronize_session=False)
    db.flush()

    def upsert(svc: Service, journey: str, flight: str, price: float, features: list[str], priority: int) -> str:
        existing = (
            db.query(AirportService)
            .filter_by(
                airport_id=ixr_airport.id,
                service_id=svc.id,
                journey_type=journey,
                flight_type=flight,
                terminal=None,
            )
            .first()
        )
        if existing:
            target = existing
            action = "updated"
        else:
            target = AirportService(
                id=uuid.uuid4(),
                airport_id=ixr_airport.id,
                service_id=svc.id,
                journey_type=journey,
                flight_type=flight,
                terminal=None,
            )
            db.add(target)
            action = "created"

        target.short_description = None
        target.features = list(features)
        target.additional_benefits = []
        target.min_booking_notice_hours = 6
        target.is_available = True
        target.display_priority = priority
        target.price = price
        target.currency = "INR"
        target.terminal = None
        return action

    upsert(silver_svc, "DEPARTURE", "DOMESTIC", 2500.00, IXR_DOMESTIC_DEPARTURE_FEATURES, 1)
    upsert(silver_svc, "ARRIVAL", "DOMESTIC", 2500.00, IXR_DOMESTIC_ARRIVAL_FEATURES, 1)

    db.flush()
    print("  + Configured IXR production packages: 2 authoritative mappings active (Dom Dep INR 2500.00, Dom Arr INR 2500.00)")
