import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


VTZ_DOMESTIC_DEPARTURE_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA.",
    "PORTER SERVICE WITH DEDICATED STAFF.",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE.",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES.",
    "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS.",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA).",
]

VTZ_DOMESTIC_ARRIVAL_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE WITH DEDICATED STAFF (Through Airlines)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]


def seed_vtz_production_packages(db: Session, vtz_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    Visakhapatnam (VTZ) production service and pricing configuration.
    Departure Silver = INR 2500.00
    Arrival Silver = INR 2500.00
    Verbatim service inclusions stored exactly without alteration.
    Transit and International journey types are deactivated (not supplied by source).
    """
    print("\n-- Configuring Production Packages for Visakhapatnam (VTZ) --")

    silver_svc = service_map.get("silver")
    if not silver_svc:
        raise RuntimeError("VTZ requires catalog service slug=silver")

    # Deactivate any existing VTZ services for TRANSIT or INTERNATIONAL or non-silver services
    unsupported_rows = (
        db.query(AirportService)
        .filter(
            AirportService.airport_id == vtz_airport.id,
        )
        .all()
    )
    for row in unsupported_rows:
        if row.journey_type not in ("DEPARTURE", "ARRIVAL") or row.flight_type != "DOMESTIC" or row.service_id != silver_svc.id:
            row.is_available = False

    def upsert(svc: Service, journey: str, flight: str, price: float, features: list[str], priority: int) -> str:
        existing = (
            db.query(AirportService)
            .filter_by(
                airport_id=vtz_airport.id,
                service_id=svc.id,
                journey_type=journey,
                flight_type=flight,
            )
            .all()
        )
        if len(existing) > 1:
            keep = existing[0]
            for dup in existing[1:]:
                dup.is_available = False
            target = keep
            action = "updated"
        elif existing:
            target = existing[0]
            action = "updated"
        else:
            target = AirportService(
                id=uuid.uuid4(),
                airport_id=vtz_airport.id,
                service_id=svc.id,
                journey_type=journey,
                flight_type=flight,
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
        return action

    upsert(silver_svc, "DEPARTURE", "DOMESTIC", 2500.00, VTZ_DOMESTIC_DEPARTURE_FEATURES, 1)
    upsert(silver_svc, "ARRIVAL", "DOMESTIC", 2500.00, VTZ_DOMESTIC_ARRIVAL_FEATURES, 1)

    db.flush()
    print("  + Configured VTZ production packages (Departure Silver INR 2500.00, Arrival Silver INR 2500.00)")
