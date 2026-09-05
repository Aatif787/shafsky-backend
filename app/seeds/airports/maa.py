import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


MAA_DOMESTIC_DEPARTURE_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA.",
    "PORTER SERVICE WITH DEDICATED STAFF.",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines).",
    "ASSIST FROM ENTRY GATE.",
    "ASSIST IN CHECKIN PROCESS AT COUNTERS.",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA).",
    "ASSIST GUEST TILL THE BOARDING GATE.",
]

MAA_DOMESTIC_ARRIVAL_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE .",
    "BAGGAGE ASSIST FOR HAND BAGGAGE.",
    "ASSIST AT BAGGAGE BELT AREA.",
    "COORDINATION WITH RECEIVING PERSON.",
    "DROP OFF TILL CAR PARKING.",
]

MAA_INTERNATIONAL_ARRIVAL_FEATURES = [
    "WELCOME GUEST FROM POST CUSTOM.",
    "ASSIST AT BAGGAGE BELT AREA.",
    "COORDINATION WITH RECEIVING PERSON.",
    "DROP OFF TILL CAR PARKING.",
]

MAA_INTERNATIONAL_DEPARTURE_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE.",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM ENTRY GATE",
    "ASSIST IN BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "GUIDANCE FOR IMMIGRATION COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "ASSIST GUEST UPTO BOARDING GATE",
]


def seed_maa_production_packages(db: Session, maa_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    Chennai Airport (MAA) production service and pricing configuration.
    Domestic Departure Silver = INR 2500.00
    Domestic Arrival Silver = INR 2500.00
    International Arrival Silver = INR 3500.00
    International Departure Silver = INR 4500.00
    Verbatim service inclusions stored exactly with 'ASSIST' action wording.
    Transit and Gold/Elite/Platinum tiers are deactivated (not supplied by source).
    """
    print("\n-- Configuring Production Packages for Chennai Airport (MAA) --")

    silver_svc = service_map.get("silver")
    if not silver_svc:
        raise RuntimeError("MAA requires catalog service slug=silver")

    # Deactivate ALL existing MAA services to start with clean state
    db.query(AirportService).filter(
        AirportService.airport_id == maa_airport.id
    ).update({"is_available": False}, synchronize_session=False)
    db.flush()

    def upsert(svc: Service, journey: str, flight: str, price: float, features: list[str], priority: int) -> str:
        existing = (
            db.query(AirportService)
            .filter_by(
                airport_id=maa_airport.id,
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
                airport_id=maa_airport.id,
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

    upsert(silver_svc, "DEPARTURE", "DOMESTIC", 2500.00, MAA_DOMESTIC_DEPARTURE_FEATURES, 1)
    upsert(silver_svc, "ARRIVAL", "DOMESTIC", 2500.00, MAA_DOMESTIC_ARRIVAL_FEATURES, 1)
    upsert(silver_svc, "ARRIVAL", "INTERNATIONAL", 3500.00, MAA_INTERNATIONAL_ARRIVAL_FEATURES, 1)
    upsert(silver_svc, "DEPARTURE", "INTERNATIONAL", 4500.00, MAA_INTERNATIONAL_DEPARTURE_FEATURES, 1)

    db.flush()
    print("  + Configured MAA production packages: Dom Dep Silver INR 2500, Dom Arr Silver INR 2500, Intl Arr Silver INR 3500, Intl Dep Silver INR 4500")
