import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService


# =========================================================================
# THIRUVANANTHAPURAM / TRIVANDRUM AIRPORT (TRV) — AUTHORITATIVE PRODUCTION PACKAGES
# =========================================================================

# 1. Domestic Departure — Platinum (INR 2420)
TRV_DOMESTIC_DEPARTURE_PLATINUM_FEATURES = [
    "WELCOME GUEST FROM CURB SIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

# 2. Domestic Departure — Elite (INR 4400)
TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES = [
    "WELCOME GUEST FROM CURBSIDE AREA",
    "PORTER SERVICE WITH DEDICATED STAFF",
    "WHEELCHAIR SERVICE AVAILABLE (Through Airlines)",
    "ASSIST FROM SEPARATE ENTRY GATE",
    "ASSIST TO BAGGAGE WRAPPING FACILITIES",
    "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE SERVICE FACILITY AVAILABLE",
    "ASSIST GUEST TILL THE BOARDING GATE",
    "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME",
    "MINIMUM 6 HOURS PRIORS NOTICE REQUIRED FOR RESCHEDULING.",
]

# 3. Domestic Arrival — Platinum (INR 2420)
TRV_DOMESTIC_ARRIVAL_PLATINUM_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE WITH DEDICATED STAFF (Through Airlines)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
]

# 4. Domestic Arrival — Elite (INR 4400)
TRV_DOMESTIC_ARRIVAL_ELITE_FEATURES = [
    "WELCOME GUEST FROM AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE WITH DEDICATED STAFF (Through Airlines)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST GUEST TILL THE CAR PARKING AREA",
    "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME",
    "MINIMUM 6 HOURS PRIORS NOTICE REQUIRED FOR RESCHEDULING.",
]

# 5. International Departure — Platinum (INR 3300)
TRV_INTERNATIONAL_DEPARTURE_PLATINUM_FEATURES = [
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

# 6. International Departure — Elite (INR 4950)
TRV_INTERNATIONAL_DEPARTURE_ELITE_FEATURES = [
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
    "CANCELLATION BENEFITS UPTO 12 HOUR’S OF SERVICE TIME",
    "MINIMUM 6 HOURS PRIORS NOTICE REQUIRED FOR RESCHEDULING.",
]

# 7. International Arrival — Platinum (INR 2750)
TRV_INTERNATIONAL_ARRIVAL_PLATINUM_FEATURES = [
    "WELCOME GUEST FROM POST IMMIGRATION.",
    "ASSIST IN DUTY FREE SHOP.",
    "ASSIST IN BAGGAGE BELT AREA.",
    "ASSIST FROM POST CUSTOMS.",
    "COORDINATION WITH RECEIVING PERSON.",
    "DROP OFF TILL CAR PARKING AREA.",
]


# 7 Authoritative TRV Pricing Mappings
TRV_PRICING = {
    ("DEPARTURE", "DOMESTIC", "platinum"): 2420.00,
    ("DEPARTURE", "DOMESTIC", "elite"): 4400.00,
    ("ARRIVAL", "DOMESTIC", "platinum"): 2420.00,
    ("ARRIVAL", "DOMESTIC", "elite"): 4400.00,
    ("DEPARTURE", "INTERNATIONAL", "platinum"): 3300.00,
    ("DEPARTURE", "INTERNATIONAL", "elite"): 4950.00,
    ("ARRIVAL", "INTERNATIONAL", "platinum"): 2750.00,
}


def seed_trv_production_packages(db: Session, trv_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    Thiruvananthapuram (TRV) — 7 Authoritative Production Packages:
      - Domestic Departure: Platinum (2420) & Elite (4400)
      - Domestic Arrival: Platinum (2420) & Elite (4400)
      - International Departure: Platinum (3300) & Elite (4950)
      - International Arrival: Platinum (2750)
      - International Arrival Elite: NOT PROVIDED (MUST NOT BE CREATED)
      - Transit: NOT PROVIDED (Preserve existing if present as inactive, MUST NOT BE BOOKABLE)
    """
    print("\n-- Configuring Authoritative Production Packages for Thiruvananthapuram (TRV) --")

    platinum_svc = service_map.get("platinum")
    elite_svc = service_map.get("elite")
    if not platinum_svc or not elite_svc:
        raise RuntimeError("TRV requires catalog services slug=platinum and slug=elite")

    # Remove only departure and arrival mappings for TRV to be replaced with authoritative packages.
    # Preserve existing Transit mappings if any, but ensure they are disabled (not bookable).
    db.query(AirportService).filter(
        AirportService.airport_id == trv_airport.id,
        AirportService.journey_type.in_(["DEPARTURE", "ARRIVAL"]),
    ).delete(synchronize_session=False)

    # Disable any existing transit records so TRV transit is never bookable
    db.query(AirportService).filter(
        AirportService.airport_id == trv_airport.id,
        AirportService.journey_type == "TRANSIT",
    ).update({"is_available": False}, synchronize_session=False)

    db.flush()

    packages = [
        # Domestic Departure
        (platinum_svc, "DEPARTURE", "DOMESTIC", 2420.00, TRV_DOMESTIC_DEPARTURE_PLATINUM_FEATURES, 1,
         "Authoritative TRV domestic departure Platinum assist."),
        (elite_svc, "DEPARTURE", "DOMESTIC", 4400.00, TRV_DOMESTIC_DEPARTURE_ELITE_FEATURES, 2,
         "Authoritative TRV domestic departure Elite assist with lounge facility and flexible cancellation."),

        # Domestic Arrival
        (platinum_svc, "ARRIVAL", "DOMESTIC", 2420.00, TRV_DOMESTIC_ARRIVAL_PLATINUM_FEATURES, 1,
         "Authoritative TRV domestic arrival Platinum assist from aerobridge to car parking."),
        (elite_svc, "ARRIVAL", "DOMESTIC", 4400.00, TRV_DOMESTIC_ARRIVAL_ELITE_FEATURES, 2,
         "Authoritative TRV domestic arrival Elite assist with flexible cancellation."),

        # International Departure
        (platinum_svc, "DEPARTURE", "INTERNATIONAL", 3300.00, TRV_INTERNATIONAL_DEPARTURE_PLATINUM_FEATURES, 1,
         "Authoritative TRV international departure Platinum assist through immigration to gate."),
        (elite_svc, "DEPARTURE", "INTERNATIONAL", 4950.00, TRV_INTERNATIONAL_DEPARTURE_ELITE_FEATURES, 2,
         "Authoritative TRV international departure Elite assist with 02 hours lounge facility."),

        # International Arrival (Only Platinum provided)
        (platinum_svc, "ARRIVAL", "INTERNATIONAL", 2750.00, TRV_INTERNATIONAL_ARRIVAL_PLATINUM_FEATURES, 1,
         "Authoritative TRV international arrival Platinum assist from post immigration to car parking."),
    ]

    for svc, journey, flight, price, features, priority, desc in packages:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=trv_airport.id,
            service_id=svc.id,
            journey_type=journey,
            flight_type=flight,
            short_description=desc,
            features=list(features),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=priority,
            price=price,
            currency="INR",
        ))
    db.flush()
    print("  + Configured TRV Production Packages: 7 active authoritative packages.")
    print("    - Domestic Departure: Platinum (2420), Elite (4400)")
    print("    - Domestic Arrival: Platinum (2420), Elite (4400)")
    print("    - International Departure: Platinum (3300), Elite (4950)")
    print("    - International Arrival: Platinum (2750)")
    print("    - International Arrival Elite: NOT CONFIGURED")
    print("    - Transit: NOT CONFIGURED (Preserved & Disabled)")
