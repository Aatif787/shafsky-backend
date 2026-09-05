import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


IXE_DOMESTIC_DEPARTURE_PLATINUM_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

IXE_DOMESTIC_DEPARTURE_ELITE_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

IXE_DOMESTIC_ARRIVAL_PLATINUM_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]

IXE_DOMESTIC_ARRIVAL_ELITE_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]

IXE_INTERNATIONAL_DEPARTURE_PLATINUM_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST IN MONEY EXCHANGE COUNTER",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "ASSIST FOR IMMIGRATION COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

IXE_INTERNATIONAL_DEPARTURE_ELITE_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST IN MONEY EXCHANGE COUNTER",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "ASSIST FOR IMMIGRATION COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

IXE_INTERNATIONAL_ARRIVAL_PLATINUM_FEATURES = [
    "WELCOME GUEST FROM POST IMMIGRATION.",
    "ASSIST IN DUTY FREE SHOP.",
    "ASSIST IN BAGGAGE BELT AREA.",
    "ASSIST FROM POST CUSTOMS.",
    "COORDINATION WITH RECEIVING PERSON.",
    "DROP OFF TILL CAR PARKING AREA.",
]


def seed_ixe_production_packages(db: Session, ixe_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    Mangaluru Airport (IXE) production service and pricing configuration.
    Domestic Departure Platinum = INR 2420.00
    Domestic Departure Elite = INR 4400.00
    Domestic Arrival Platinum = INR 2420.00
    Domestic Arrival Elite = INR 4400.00
    International Departure Platinum = INR 3300.00
    International Departure Elite = INR 4950.00
    International Arrival Platinum = INR 2750.00
    Verbatim service inclusions stored exactly without alteration.
    Transit and International Arrival Elite are unconfigured.
    """
    print("\n-- Configuring Production Packages for Mangaluru Airport (IXE) --")

    plat_svc = service_map.get("platinum")
    elite_svc = service_map.get("elite")

    if not plat_svc or not elite_svc:
        raise RuntimeError("IXE requires catalog services slug=platinum and slug=elite")

    # Deactivate all existing IXE mappings
    db.query(AirportService).filter(
        AirportService.airport_id == ixe_airport.id
    ).update({"is_available": False}, synchronize_session=False)
    db.flush()

    def upsert(svc: Service, journey: str, flight: str, price: float, features: list[str], priority: int) -> str:
        existing = (
            db.query(AirportService)
            .filter_by(
                airport_id=ixe_airport.id,
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
                airport_id=ixe_airport.id,
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

    upsert(plat_svc, "DEPARTURE", "DOMESTIC", 2420.00, IXE_DOMESTIC_DEPARTURE_PLATINUM_FEATURES, 1)
    upsert(elite_svc, "DEPARTURE", "DOMESTIC", 4400.00, IXE_DOMESTIC_DEPARTURE_ELITE_FEATURES, 2)

    upsert(plat_svc, "ARRIVAL", "DOMESTIC", 2420.00, IXE_DOMESTIC_ARRIVAL_PLATINUM_FEATURES, 1)
    upsert(elite_svc, "ARRIVAL", "DOMESTIC", 4400.00, IXE_DOMESTIC_ARRIVAL_ELITE_FEATURES, 2)

    upsert(plat_svc, "DEPARTURE", "INTERNATIONAL", 3300.00, IXE_INTERNATIONAL_DEPARTURE_PLATINUM_FEATURES, 1)
    upsert(elite_svc, "DEPARTURE", "INTERNATIONAL", 4950.00, IXE_INTERNATIONAL_DEPARTURE_ELITE_FEATURES, 2)

    upsert(plat_svc, "ARRIVAL", "INTERNATIONAL", 2750.00, IXE_INTERNATIONAL_ARRIVAL_PLATINUM_FEATURES, 1)

    db.flush()
    print("  + Configured IXE production packages: 7 authoritative mappings active")
