import uuid
from sqlalchemy.orm import Session
from app.models.journey_models import SupportedAirport, Service, AirportService

# =========================================================================
# MUMBAI AIRPORT (BOM) — AUTHORITATIVE PRODUCTION TRANSIT PACKAGES
# =========================================================================

BOM_TRANSIT_DOMESTIC_DOMESTIC_FEATURES = [
    "WELCOME IN THE END OF THE AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)",
    "BUGGY SERVICE AVAILABLE END OF THE AEROBRIDGE",
    "ASSIST IN S.H.A.(TRANSIT AREA)",
    "LOUNGE ACCESS FOR 2 HOURS (AT DEPARTURE ONLY)",
    "BUGGY SERVICE AVAILABLE TILL THE BOARDING GATE (AS PER THE AVAILABILITY)",
    "ASSIST PAX UPTO BOARDING GATE",
]

BOM_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES = [
    "WELCOME IN THE END OF THE AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "BUGGY SERVICE AVAILABLE",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST SEPARATE BAGGAGE CHECK-IN AT AIRLINE COUNTER",
    "GUIDANCE TO THE IMMIGRATION COUNTER",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE ACCESS FOR 2 HOURS (AT DEPARTURE ONLY)",
    "BUGGY SERVICE TILL THE BOARDING GATE (AS PER THE AVAILABILITY)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

BOM_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES = [
    "WELCOME IN THE END OF THE AEROBRIDGE",
    "DEDICATED STAFF WITH PLACARD",
    "PORTER SERVICE WITH DEDICATED STAFF AT ARRIVALS",
    "BUGGY SERVICE AVAILABLE END OF THE AEROBRIDGE",
    "WHEELCHAIR SERVICE AVAILABLE (THROUGH AIRLINES)",
    "GUIDANCE TO THE IMMIGRATION COUNTER",
    "ASSIST IN BAGGAGE BELT AREA",
    "ASSIST AT SEPARATE CHECKIN PROCESS AT COUNTERS",
    "ASSIST IN S.H.A.(SECURITY HOLD AREA)",
    "LOUNGE ACCESS FOR 2 HOURS (AT DEPARTURE ONLY)",
    "BUGGY SERVICE AVAILABLE TILL THE BOARDING GATE (AS PER THE AVAILABILITY)",
    "ASSIST GUEST UPTO BOARDING GATE",
]

BOM_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES = [
    "WARM WELCOME AT AEROBRIDGE/BUS GATE BY PORTER.",
    "DEDICATED PORTER FOR ASSISTANCE FROM AEROBRIDGE ON ARRIVAL TILL THE BOARDING GATE OF THE NEXT CONNECTING FLIGHT.",
    "GUIDANCE WITH THE AIRPORT AND AIRLINE TRANSIT PROCESS.",
    "FACILITATION THROUGH SECURITY AS PER CLASS OF TRAVEL",
    "ENJOY ACCESS TO ADANI LOUNGE WITH SNACKS, FOOD, AND NON-ALCOHOLIC BEVERAGE.",
    "GOLF CART TRANSFER TILL LOUNGE/ BOARDING GATE (BATTERY OPERATED CAR SUBJECT TO THE LOCATION OF BOARDING GATE)",
]

BOM_TRANSIT_PRICING = {
    "DOMESTIC_DOMESTIC": 7150.00,
    "DOMESTIC_INTERNATIONAL": 9000.00,
    "INTERNATIONAL_DOMESTIC": 9000.00,
    "INTERNATIONAL_INTERNATIONAL": 10000.00,
}


def seed_bom_transit_production_packages(db: Session, bom_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    Mumbai Airport (BOM) Transit Services Only:
    Safely removes legacy BOM transit and configures the 4 authoritative production packages:
    1. Domestic-Domestic: INR 7150
    2. Domestic-International: INR 9000
    3. International-Domestic: INR 9000
    4. International-International: INR 10000
    Does NOT touch Domestic/International Departure or Arrival.
    """
    print("\n-- Configuring Authoritative Production Transit Packages for Mumbai (BOM) --")
    transit_svc = service_map.get("meet_greet") or service_map.get("platinum")
    if not transit_svc:
        raise RuntimeError("BOM Transit requires service slug 'meet_greet' or 'platinum'")

    # Remove ONLY existing transit rows for BOM
    db.query(AirportService).filter(
        AirportService.airport_id == bom_airport.id,
        AirportService.journey_type == "TRANSIT",
    ).delete(synchronize_session=False)
    db.flush()

    transit_packages = [
        ("DOMESTIC_DOMESTIC", 7150.00, BOM_TRANSIT_DOMESTIC_DOMESTIC_FEATURES, 1,
         "Authoritative Mumbai domestic to domestic transit assist."),
        ("DOMESTIC_INTERNATIONAL", 9000.00, BOM_TRANSIT_DOMESTIC_INTERNATIONAL_FEATURES, 2,
         "Authoritative Mumbai domestic to international transit assist."),
        ("INTERNATIONAL_DOMESTIC", 9000.00, BOM_TRANSIT_INTERNATIONAL_DOMESTIC_FEATURES, 3,
         "Authoritative Mumbai international to domestic transit assist."),
        ("INTERNATIONAL_INTERNATIONAL", 10000.00, BOM_TRANSIT_INTERNATIONAL_INTERNATIONAL_FEATURES, 4,
         "Authoritative Mumbai international to international transit assist."),
    ]

    for flight_type, price, features, priority, desc in transit_packages:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type=flight_type,
            terminal=None,
            short_description=desc,
            features=list(features),
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=priority,
            price=price,
            currency="INR",
            deleted_at=None,
        ))
    db.flush()
    print("  + Configured BOM Transit: 4 authoritative active routes (DD=7150, DI=9000, ID=9000, II=10000)")


def seed_bom_production_packages(db: Session, bom_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Mumbai Airport (BOM) Domestic Departure & Domestic Arrival:
    Seeds real production packages:
    - Domestic Departure: Platinum (₹3,850), Elite (₹4,950), Elite Plus (₹7,040)
    - Domestic Arrival: Platinum (₹3,600), Elite (₹4,950), Elite Plus (₹7,040)
    """
    print("\n-- Configuring Production Packages for Mumbai (BOM) Domestic Departure & Domestic Arrival --")

    # 1. Clear all existing services mapped to BOM to avoid duplicates
    db.query(AirportService).filter_by(
        airport_id=bom_airport.id,
    ).delete(synchronize_session=False)

    plat_svc = service_map.get("platinum")
    elite_svc = service_map.get("elite")
    elite_plus_svc = service_map.get("elite_plus")

    # ── DOMESTIC DEPARTURE PACKAGES ──
    # 1. Platinum Service (₹3,850)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=plat_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Premium domestic departure assist from the curbside area to the boarding gate.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST WITH SEPARATE BAGGAGE CHECK-IN AT THE AIRLINE COUNTER",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "BUGGY SERVICE TO THE BOARDING GATE (SHARING BASIS, SUBJECT TO AVAILABILITY)",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3850.00,
            currency="INR",
        ))

    # 2. Elite Service (₹4,950)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Enhanced domestic departure assist with lounge access and dedicated airport support.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST WITH SEPARATE CHECK-IN AT THE AIRLINE COUNTERS",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "LOUNGE SERVICE FACILITY",
                "BUGGY SERVICE TO THE BOARDING GATE (SUBJECT TO AVAILABILITY)",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4950.00,
            currency="INR",
        ))

    # 3. Elite Plus Service (₹7,040)
    if elite_plus_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_plus_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic departure assist with lounge access and flexible booking benefits.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST WITH SEPARATE CHECK-IN AT THE AIRLINE COUNTERS",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "LOUNGE SERVICE FACILITY",
                "BUGGY SERVICE TO THE BOARDING GATE (SUBJECT TO AVAILABILITY)",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "One-time rescheduling with a minimum of 6 hours' prior notice",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7040.00,
            currency="INR",
        ))

    # ── DOMESTIC ARRIVAL PACKAGES ──
    # 1. Platinum Service (₹3,600)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=plat_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Premium domestic arrival assist from the end of the aerobridge to the car parking area.",
            features=[
                "WELCOME AT THE END OF THE AEROBRIDGE",
                "DEDICATED STAFF WITH PLACARD",
                "DEDICATED PORTER SERVICE AT ARRIVALS",
                "BUGGY SERVICE FROM THE END OF THE AEROBRIDGE (SHARING BASIS, SUBJECT TO AVAILABILITY)",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE)",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3600.00,
            currency="INR",
        ))

    # 2. Elite Service (₹4,950)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Enhanced domestic arrival assist with dedicated airport support from the aerobridge to the car parking area.",
            features=[
                "WELCOME AT THE END OF THE AEROBRIDGE",
                "DEDICATED STAFF WITH PLACARD",
                "DEDICATED PORTER SERVICE AT ARRIVALS",
                "BUGGY SERVICE FROM THE END OF THE AEROBRIDGE (SUBJECT TO AVAILABILITY)",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE)",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4950.00,
            currency="INR",
        ))

    # 3. Elite Plus Service (₹7,040)
    if elite_plus_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_plus_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic arrival assist with flexible booking benefits.",
            features=[
                "WELCOME AT THE END OF THE AEROBRIDGE",
                "DEDICATED STAFF WITH PLACARD",
                "DEDICATED PORTER SERVICE AT ARRIVALS",
                "BUGGY SERVICE FROM THE END OF THE AEROBRIDGE (SUBJECT TO AVAILABILITY)",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE)",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "One-time rescheduling with a minimum of 6 hours' prior notice",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7040.00,
            currency="INR",
        ))

    # ── INTERNATIONAL DEPARTURE PACKAGES ──
    # 1. Platinum Service (₹7,700)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=plat_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Premium international departure assist from the curbside area to the boarding gate.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST AT THE MONEY EXCHANGE COUNTER",
                "ASSIST WITH BAGGAGE WRAPPING FACILITIES",
                "ASSIST WITH SEPARATE BAGGAGE CHECK-IN AT THE AIRLINE COUNTER",
                "ASSIST THROUGH IMMIGRATION",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "BUGGY SERVICE TO THE BOARDING GATE (SUBJECT TO AVAILABILITY)",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=7700.00,
            currency="INR",
        ))

    # 2. Elite Service (₹9,000)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Enhanced international departure assist with lounge access and dedicated airport support.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST AT THE MONEY EXCHANGE COUNTER",
                "ASSIST WITH BAGGAGE WRAPPING FACILITIES",
                "ASSIST WITH SEPARATE BAGGAGE CHECK-IN AT THE AIRLINE COUNTER",
                "ASSIST THROUGH IMMIGRATION",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "LOUNGE SERVICE FACILITY",
                "BUGGY SERVICE TO THE BOARDING GATE (SUBJECT TO AVAILABILITY)",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=9000.00,
            currency="INR",
        ))

    # 3. Elite Plus Service (₹13,500)
    if elite_plus_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_plus_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international departure assist with lounge access and flexible booking benefits.",
            features=[
                "WELCOME AT THE CURBSIDE AREA",
                "DEDICATED PORTER SERVICE",
                "WHEELCHAIR ASSIST (THROUGH THE AIRLINE)",
                "ASSIST THROUGH THE SEPARATE ENTRY GATE",
                "ASSIST AT THE MONEY EXCHANGE COUNTER",
                "ASSIST WITH BAGGAGE WRAPPING FACILITIES",
                "ASSIST WITH SEPARATE BAGGAGE CHECK-IN AT THE AIRLINE COUNTER",
                "ASSIST THROUGH IMMIGRATION",
                "ASSIST INSIDE THE SECURITY HOLD AREA (SHA)",
                "LOUNGE SERVICE FACILITY",
                "BUGGY SERVICE TO THE BOARDING GATE (SUBJECT TO AVAILABILITY)",
                "ESCORT TO THE BOARDING GATE",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "One-time rescheduling with a minimum of 6 hours' prior notice",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=13500.00,
            currency="INR",
        ))

    # ── INTERNATIONAL ARRIVAL PACKAGES ──
    # 1. Platinum Service (₹8,690)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=plat_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Premium international arrival assist from the aerobridge to the car parking area.",
            features=[
                "WELCOME AT THE AEROBRIDGE",
                "DEDICATED STAFF WITH PLACARD",
                "DEDICATED PORTER SERVICE AT ARRIVALS",
                "BUGGY SERVICE FROM THE END OF THE AEROBRIDGE (SHARING BASIS, SUBJECT TO AVAILABILITY)",
                "ASSIST THROUGH THE IMMIGRATION COUNTER",
                "ASSIST AT THE DUTY FREE SHOP",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "ASSIST THROUGH CUSTOMS",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=8690.00,
            currency="INR",
        ))

    # 2. Elite Service (₹9,900)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Enhanced international arrival assist with dedicated airport support from the aerobridge to the car parking area.",
            features=[
                "WELCOME AT THE AEROBRIDGE",
                "DEDICATED STAFF WITH PLACARD",
                "DEDICATED PORTER SERVICE AT ARRIVALS",
                "BUGGY SERVICE FROM THE END OF THE AEROBRIDGE (SUBJECT TO AVAILABILITY)",
                "ASSIST THROUGH THE IMMIGRATION COUNTER",
                "ASSIST AT THE DUTY FREE SHOP",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "ASSIST THROUGH CUSTOMS",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=9900.00,
            currency="INR",
        ))

    # 3. Elite Plus Service (₹13,500)
    if elite_plus_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=elite_plus_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international arrival assist with flexible booking benefits.",
            features=[
                "WELCOME AT THE AEROBRIDGE",
                "DEDICATED STAFF WITH PLACARD",
                "DEDICATED PORTER SERVICE AT ARRIVALS",
                "BUGGY SERVICE FROM THE AEROBRIDGE (SUBJECT TO AVAILABILITY)",
                "ASSIST AT THE BAGGAGE BELT AREA",
                "ASSIST THROUGH THE IMMIGRATION COUNTER",
                "ASSIST AT THE DUTY FREE SHOP",
                "ASSIST GUEST TILL THE CAR PARKING AREA",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "One-time rescheduling with a minimum of 6 hours' prior notice",
            ],
            min_booking_notice_hours=6,
            display_priority=3,
            price=13500.00,
            currency="INR",
        ))

    # ── TRANSIT PACKAGES ──
    seed_bom_transit_production_packages(db, bom_airport, service_map)

    db.flush()
    print("  + Created BOM Production Packages: Domestic & International Departure & Arrival + Transit")
