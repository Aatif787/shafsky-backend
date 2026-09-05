import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


GAU_DOMESTIC_DEPARTURE_PLATINUM_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

GAU_DOMESTIC_DEPARTURE_ELITE_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE",
    "ASSIST GUEST TILL THE BOARDING GATE",
    "CANCELLATION BENEFITS UPTO 12 HOUR'S OF SERVICE TIME",
    "MINIMUM 6 HOURS PRIORS NOTICE REQUIRED FOR RESCHEDULING.",
]

GAU_DOMESTIC_ARRIVAL_PLATINUM_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]

GAU_DOMESTIC_ARRIVAL_ELITE_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]

GAU_INTERNATIONAL_DEPARTURE_ELITE_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST IN MONEY EXCHANGE COUNTER",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "ASSIST FOR IMMIGRATION COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE (02 HOURS)",
    "ASSIST GUEST UPTO BOARDING GATE",
    "CANCELLATION BENEFITS UPTO 12 HOUR'S OF SERVICE TIME",
    "MINIMUM 6 HOURS PRIORS NOTICE REQUIRED FOR RESCHEDULING.",
]

GAU_INTERNATIONAL_ARRIVAL_PLATINUM_FEATURES = [
    "WELCOME GUEST FROM POST IMMIGRATION.",
    "ASSIST IN DUTY FREE SHOP.",
    "ASSIST IN BAGGAGE BELT AREA.",
    "ASSIST FROM POST CUSTOMS.",
    "COORDINATION WITH RECEIVING PERSON.",
    "DROP OFF TILL CAR PARKING AREA.",
]


def seed_gau_production_packages(db: Session, gau_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    Guwahati (GAU) production Platinum / Elite packages.
    Service inclusion text is stored verbatim from the GAU source configuration.
    International Departure Platinum is price-only (inclusions not supplied).
    International Arrival Elite is not created (price and inclusions not supplied).
    Existing GAU TRANSIT records are preserved.
    """
    print("\n-- Configuring Production Packages for Guwahati (GAU) --")

    plat_svc = service_map.get("platinum")
    elite_svc = service_map.get("elite")
    if not plat_svc or not elite_svc:
        raise RuntimeError("GAU requires catalog services slug=platinum and slug=elite")

    # Deactivate demo ARRIVAL/DEPARTURE rows only. Do not touch TRANSIT.
    demo_rows = (
        db.query(AirportService)
        .filter(
            AirportService.airport_id == gau_airport.id,
            AirportService.journey_type.in_(["ARRIVAL", "DEPARTURE"]),
            AirportService.service_id.notin_([plat_svc.id, elite_svc.id]),
        )
        .all()
    )
    for row in demo_rows:
        row.is_available = False

    def upsert(svc: Service, journey: str, flight: str, price: float, features: list[str], priority: int) -> str:
        existing = (
            db.query(AirportService)
            .filter_by(
                airport_id=gau_airport.id,
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
                airport_id=gau_airport.id,
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

    upsert(plat_svc, "DEPARTURE", "DOMESTIC", 2420.00, GAU_DOMESTIC_DEPARTURE_PLATINUM_FEATURES, 1)
    upsert(elite_svc, "DEPARTURE", "DOMESTIC", 4400.00, GAU_DOMESTIC_DEPARTURE_ELITE_FEATURES, 2)
    upsert(plat_svc, "ARRIVAL", "DOMESTIC", 2420.00, GAU_DOMESTIC_ARRIVAL_PLATINUM_FEATURES, 1)
    upsert(elite_svc, "ARRIVAL", "DOMESTIC", 4400.00, GAU_DOMESTIC_ARRIVAL_ELITE_FEATURES, 2)
    upsert(plat_svc, "DEPARTURE", "INTERNATIONAL", 3300.00, [], 1)
    upsert(elite_svc, "DEPARTURE", "INTERNATIONAL", 4950.00, GAU_INTERNATIONAL_DEPARTURE_ELITE_FEATURES, 2)
    upsert(plat_svc, "ARRIVAL", "INTERNATIONAL", 2750.00, GAU_INTERNATIONAL_ARRIVAL_PLATINUM_FEATURES, 1)

    # Do not create International Arrival Elite — source did not provide price or inclusions.
    intl_arr_elite = (
        db.query(AirportService)
        .filter_by(
            airport_id=gau_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
        )
        .all()
    )
    for row in intl_arr_elite:
        row.is_available = False

    db.flush()
    print("  + Configured GAU Platinum/Elite production packages (Intl Arrival Elite not created)")
