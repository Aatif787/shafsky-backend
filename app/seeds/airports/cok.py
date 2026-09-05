import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


COK_DOMESTIC_DEPARTURE_SILVER_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "BAGGAGE ASSISTANCE AT BAGGAGE (3 PCS)",
    "ASSIST IN SEPARATE ENTRY GATE",
    "ASSIST IN AIRLINE CHECK IN BAGGAGE",
    "ASSIST IN S.H.A (SECURITY HOLD AREA)",
    "LOUNGE ACCESS FOR 2 HOURS",
    "DROP OFF TILL BOARDING GATE.",
]

COK_DOMESTIC_DEPARTURE_ELITE_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "BAGGAGE ASSISTANCE AT BAGGAGE (3 PCS)",
    "ASSIST IN SEPARATE ENTRY GATE",
    "ASSIST IN AIRLINE CHECK IN BAGGAGE",
    "ASSIST IN S.H.A (SECURITY HOLD AREA)",
    "LOUNGE ACCESS FOR 2 HOURS",
    "DROP OFF TILL BOARDING GATE.",
]

COK_DOMESTIC_ARRIVAL_SILVER_FEATURES = [
    "WELCOME GUEST FROM END OF THE AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "ASSIST IN BAGGAGE BELT AREA",
    "COORDINATION WITH RECEIVING PARTY.",
    "DROP OFF TILL CAR PARKING AREA.",
]

COK_DOMESTIC_ARRIVAL_ELITE_FEATURES = [
    "WELCOME GUEST FROM END OF THE AEROBRIDGE.",
    "DEDICATED STAFF WITH PLACARD.",
    "ASSIST IN BAGGAGE BELT AREA",
    "COORDINATION WITH RECEIVING PARTY.",
    "DROP OFF TILL CAR PARKING AREA.",
]


def seed_cok_production_packages(db: Session, cok_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    Kochi / Cochin Airport (COK) production service and pricing configuration.
    Domestic Departure Silver = INR 3500.00
    Domestic Departure Elite = INR 5500.00
    Domestic Arrival Silver = INR 3500.00
    Domestic Arrival Elite = INR 5500.00
    Verbatim service inclusions stored exactly with "ASSIST" action wording.
    International Departure, International Arrival, and Transit are unconfigured.
    """
    print("\n-- Configuring Production Packages for Kochi / Cochin Airport (COK) --")

    silver_svc = service_map.get("silver")
    elite_svc = service_map.get("elite")

    if not silver_svc or not elite_svc:
        raise RuntimeError("COK requires catalog services slug=silver and slug=elite")

    # Deactivate all existing COK mappings to eliminate demo/legacy unconfigured services
    db.query(AirportService).filter(
        AirportService.airport_id == cok_airport.id
    ).update({"is_available": False}, synchronize_session=False)
    db.flush()

    def upsert(svc: Service, journey: str, flight: str, price: float, features: list[str], priority: int) -> str:
        existing = (
            db.query(AirportService)
            .filter_by(
                airport_id=cok_airport.id,
                service_id=svc.id,
                journey_type=journey,
                flight_type=flight,
            )
            .first()
        )
        if existing:
            target = existing
            action = "updated"
        else:
            target = AirportService(
                id=uuid.uuid4(),
                airport_id=cok_airport.id,
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

    upsert(silver_svc, "DEPARTURE", "DOMESTIC", 3500.00, COK_DOMESTIC_DEPARTURE_SILVER_FEATURES, 1)
    upsert(elite_svc, "DEPARTURE", "DOMESTIC", 5500.00, COK_DOMESTIC_DEPARTURE_ELITE_FEATURES, 2)

    upsert(silver_svc, "ARRIVAL", "DOMESTIC", 3500.00, COK_DOMESTIC_ARRIVAL_SILVER_FEATURES, 1)
    upsert(elite_svc, "ARRIVAL", "DOMESTIC", 5500.00, COK_DOMESTIC_ARRIVAL_ELITE_FEATURES, 2)

    db.flush()
    print("  + Configured COK production packages: 4 authoritative mappings active (Dom Dep Silver INR 3500, Dom Dep Elite INR 5500, Dom Arr Silver INR 3500, Dom Arr Elite INR 5500)")
