import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


BBI_DOMESTIC_DEPARTURE_PLATINUM_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "BAGGAGE ASSIST FOR BAGGAGE (3 PCS)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST IN AIRLINE CHECK IN BAGGAGE.",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

BBI_DOMESTIC_DEPARTURE_ELITE_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "BAGGAGE ASSIST FOR BAGGAGE (3 PCS)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST IN AIRLINE CHECK IN BAGGAGE.",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE ACCESS FOR 2 HOURS",
    "ASSIST GUEST UPTO BOARDING GATE",
]

BBI_DOMESTIC_ARRIVAL_PLATINUM_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "ASSIST IN BAGGAGE BELT AREA",
    "COORDINATION WITH RECEIVING PARTY.",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]


def seed_bbi_production_packages(db: Session, bbi_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    Bhubaneswar (BBI) production Platinum / Elite packages.
    Service inclusion text is stored verbatim from the BBI source configuration.
    Domestic Departure Platinum = INR 1700
    Domestic Departure Elite = INR 2500
    Domestic Arrival Platinum = INR 1700
    Domestic Arrival Elite = NOT PROVIDED (is_available=False)
    International Departure/Arrival = NOT PROVIDED (is_available=False)
    Transit = NOT PROVIDED (is_available=False)
    """
    print("\n-- Configuring Production Packages for Bhubaneswar (BBI) --")

    plat_svc = service_map.get("platinum")
    elite_svc = service_map.get("elite")
    if not plat_svc or not elite_svc:
        raise RuntimeError("BBI requires catalog services slug=platinum and slug=elite")

    # Deactivate any demo BBI service mappings
    demo_rows = (
        db.query(AirportService)
        .filter(
            AirportService.airport_id == bbi_airport.id,
        )
        .all()
    )
    for row in demo_rows:
        row.is_available = False

    def upsert(svc: Service, journey: str, flight: str, price: float, features: list[str], priority: int):
        existing = (
            db.query(AirportService)
            .filter_by(
                airport_id=bbi_airport.id,
                service_id=svc.id,
                journey_type=journey,
                flight_type=flight,
            )
            .first()
        )
        if existing:
            target = existing
        else:
            target = AirportService(
                id=uuid.uuid4(),
                airport_id=bbi_airport.id,
                service_id=svc.id,
                journey_type=journey,
                flight_type=flight,
            )
            db.add(target)

        target.short_description = None
        target.features = list(features)
        target.additional_benefits = []
        target.min_booking_notice_hours = 6
        target.is_available = True
        target.display_priority = priority
        target.price = price
        target.currency = "INR"

    # 1. Domestic Departure - Platinum (INR 1700)
    upsert(plat_svc, "DEPARTURE", "DOMESTIC", 1700.00, BBI_DOMESTIC_DEPARTURE_PLATINUM_FEATURES, 1)

    # 2. Domestic Departure - Elite (INR 2500)
    upsert(elite_svc, "DEPARTURE", "DOMESTIC", 2500.00, BBI_DOMESTIC_DEPARTURE_ELITE_FEATURES, 2)

    # 3. Domestic Arrival - Platinum (INR 1700)
    upsert(plat_svc, "ARRIVAL", "DOMESTIC", 1700.00, BBI_DOMESTIC_ARRIVAL_PLATINUM_FEATURES, 1)

    db.flush()
    print("  + Configured BBI Platinum/Elite production packages (Dom Dep Plat INR 1700, Dom Dep Elite INR 2500, Dom Arr Plat INR 1700)")
