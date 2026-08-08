"""
Journey Detection Engine — Database Seeder (Phase 7 Production Packages).

Seeds:
1. Supported Airports (DEL, BOM, AMD, HYD, etc.)
2. Services Catalog (Platinum, Elite, Meet & Greet, Fast Track, Lounge, etc.)
3. Airport-Service Mappings (with production Platinum ₹2,420 & Elite ₹4,400 packages for AMD Domestic Arrival).
"""

import uuid
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.journey_models import SupportedAirport, Service, AirportService

AIRPORTS = [
    {
        "airport_name": "Indira Gandhi International Airport",
        "iata_code": "DEL",
        "icao_code": "VIDP",
        "city": "Delhi",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Chhatrapati Shivaji Maharaj International Airport",
        "iata_code": "BOM",
        "icao_code": "VABB",
        "city": "Mumbai",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Sardar Vallabhbhai Patel International Airport",
        "iata_code": "AMD",
        "icao_code": "VAAH",
        "city": "Ahmedabad",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Rajiv Gandhi International Airport",
        "iata_code": "HYD",
        "icao_code": "VOHS",
        "city": "Hyderabad",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Chaudhary Charan Singh International Airport",
        "iata_code": "LKO",
        "icao_code": "VILK",
        "city": "Lucknow",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Netaji Subhas Chandra Bose International Airport",
        "iata_code": "CCU",
        "icao_code": "VECC",
        "city": "Kolkata",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Dabolim International Airport",
        "iata_code": "GOI",
        "icao_code": "VOGO",
        "city": "Goa",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Jaipur International Airport",
        "iata_code": "JAI",
        "icao_code": "VIJP",
        "city": "Jaipur",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
    {
        "airport_name": "Sri Guru Ram Dass Jee International Airport",
        "iata_code": "ATQ",
        "icao_code": "VIAR",
        "city": "Amritsar",
        "country": "India",
        "timezone": "Asia/Kolkata",
        "is_supported": True,
        "is_active": True,
    },
]

SERVICES = [
    {
        "name": "Platinum Service",
        "slug": "platinum",
        "description": "Premium airport arrival assistance with baggage support and personalized meet & greet.",
        "icon": "Crown",
        "display_order": 1,
        "is_active": True,
    },
    {
        "name": "Elite Service",
        "slug": "elite",
        "description": "Premium personalized airport arrival assistance with dedicated baggage support and complete arrival coordination.",
        "icon": "Sparkles",
        "display_order": 2,
        "is_active": True,
    },
    {
        "name": "Elite Plus Service",
        "slug": "elite_plus",
        "description": "Complete premium airport assistance with flexible booking benefits.",
        "icon": "Award",
        "display_order": 3,
        "is_active": True,
    },
    {
        "name": "Silver Service",
        "slug": "silver",
        "description": "Premium domestic departure assistance from curbside to the boarding gate with dedicated airport support.",
        "icon": "ShieldCheck",
        "display_order": 4,
        "is_active": True,
    },
    {
        "name": "Gold Service",
        "slug": "gold",
        "description": "Premium domestic departure assistance with lounge access and personalized airport support from curbside to the boarding gate.",
        "icon": "Award",
        "display_order": 4,
        "is_active": True,
    },
    {
        "name": "Meet & Greet Escort",
        "slug": "meet_greet",
        "description": "Personal escort through terminal arrival, security & baggage retrieval.",
        "icon": "Sparkles",
        "display_order": 4,
        "is_active": True,
    },
    {
        "name": "VIP Fast Track",
        "slug": "fast_track",
        "description": "Priority queue access through security & immigration channels.",
        "icon": "Zap",
        "display_order": 5,
        "is_active": True,
    },
    {
        "name": "VIP Lounge Access",
        "slug": "lounge",
        "description": "Access to executive lounge sanctuary with dining & refreshment.",
        "icon": "Hotel",
        "display_order": 6,
        "is_active": True,
    },
]

JOURNEY_TYPES = ["ARRIVAL", "DEPARTURE", "TRANSIT"]


def seed_airports(db: Session) -> dict[str, SupportedAirport]:
    airport_map = {}
    for data in AIRPORTS:
        existing = db.query(SupportedAirport).filter_by(iata_code=data["iata_code"]).first()
        if existing:
            print(f"  [OK] Airport {data['iata_code']} exists.")
            airport_map[data["iata_code"]] = existing
        else:
            airport = SupportedAirport(id=uuid.uuid4(), **data)
            db.add(airport)
            airport_map[data["iata_code"]] = airport
            print(f"  + Created airport: {data['iata_code']}")
    db.flush()
    return airport_map


def seed_services(db: Session) -> dict[str, Service]:
    service_map = {}
    for data in SERVICES:
        existing = db.query(Service).filter_by(slug=data["slug"]).first()
        if existing:
            existing.name = data["name"]
            existing.description = data["description"]
            service_map[data["slug"]] = existing
            print(f"  [OK] Service '{data['slug']}' exists.")
        else:
            service = Service(id=uuid.uuid4(), **data)
            db.add(service)
            service_map[data["slug"]] = service
            print(f"  + Created service: {data['slug']}")
    db.flush()
    return service_map


def seed_amd_production_packages(db: Session, amd_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Ahmedabad Airport (AMD) Domestic Departure & Arrival:
    Seeds production Platinum Service (₹2,420) & Elite Service (₹4,400) packages.
    """
    print("\n-- Configuring Production Packages for Ahmedabad (AMD) Domestic Arrival & Departure --")
    
    # 1. Remove old demo services mapped to AMD Domestic
    db.query(AirportService).filter_by(
        airport_id=amd_airport.id,
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    for j_type in ["ARRIVAL", "DEPARTURE"]:
        # Package 1: Platinum Service (₹2,420)
        plat_svc = service_map.get("platinum")
        if plat_svc:
            plat_mapping = AirportService(
                id=uuid.uuid4(),
                airport_id=amd_airport.id,
                service_id=plat_svc.id,
                journey_type=j_type,
                flight_type="DOMESTIC",
                short_description="Premium airport arrival assistance with baggage support and personalized meet & greet.",
                features=[
                    "Welcome at the Aerobridge",
                    "Dedicated Staff with Personalized Placard",
                    "Baggage Assistance (Up to 3 Pieces)",
                    "Assistance at the Baggage Belt Area",
                    "Coordination with the Receiving Party",
                    "Escort to the Car Parking Area",
                ],
                min_booking_notice_hours=4,
                is_available=True,
                display_priority=1,
                price=2420.00,
                currency="INR",
            )
            db.add(plat_mapping)

        # Package 2: Elite Service (₹4,400)
        elite_svc = service_map.get("elite")
        if elite_svc:
            elite_mapping = AirportService(
                id=uuid.uuid4(),
                airport_id=amd_airport.id,
                service_id=elite_svc.id,
                journey_type=j_type,
                flight_type="DOMESTIC",
                short_description="Premium personalized airport arrival assistance with dedicated baggage support and complete arrival coordination.",
                features=[
                    "Welcome at the Aerobridge",
                    "Dedicated Staff with Personalized Placard",
                    "Baggage Assistance",
                    "Assistance at the Baggage Belt Area",
                    "Coordination with the Receiving Party",
                    "Escort to the Car Parking Area",
                ],
                min_booking_notice_hours=4,
                is_available=True,
                display_priority=2,
                price=4400.00,
                currency="INR",
            )
            db.add(elite_mapping)

    db.flush()
    print("  + Created AMD Domestic Packages: Platinum Service (INR 2,420) and Elite Service (INR 4,400)")

    # 2. Remove old demo services mapped to AMD International Departure
    print("\n-- Configuring Production Packages for Ahmedabad (AMD) International Departure --")
    db.query(AirportService).filter_by(
        airport_id=amd_airport.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    plat_svc = service_map.get("platinum")
    if plat_svc:
        plat_mapping = AirportService(
            id=uuid.uuid4(),
            airport_id=amd_airport.id,
            service_id=plat_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Premium international departure assistance from airport arrival to the boarding gate.",
            features=[
                "Welcome at the curbside area",
                "Dedicated porter service",
                "Wheelchair assistance (through the airline, if required)",
                "Assistance from the entry gate",
                "Assistance at the money exchange counter",
                "Assistance with baggage wrapping facilities",
                "Assistance during baggage check-in at the airline counter",
                "Assistance through immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the boarding gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3300.00,
            currency="INR",
        )
        db.add(plat_mapping)

    elite_svc = service_map.get("elite")
    if elite_svc:
        elite_mapping = AirportService(
            id=uuid.uuid4(),
            airport_id=amd_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international departure assistance with lounge access and enhanced passenger support.",
            features=[
                "Welcome at the curbside area",
                "Dedicated porter service",
                "Wheelchair assistance (through the airline, if required)",
                "Assistance from the entry gate",
                "Assistance at the money exchange counter",
                "Assistance with baggage wrapping facilities",
                "Assistance during baggage check-in at the airline counter",
                "Assistance through immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Complimentary lounge access (up to 2 hours)",
                "Escort to the boarding gate",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time.",
                "A minimum of 6 hours' notice is required for rescheduling.",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4950.00,
            currency="INR",
        )
        db.add(elite_mapping)

    db.flush()
    print("  + Created AMD International Departure Packages: Platinum Service (INR 3,300) and Elite Service (INR 4,950)")

    # 3. Remove old demo services mapped to AMD International Arrival
    print("\n-- Configuring Production Packages for Ahmedabad (AMD) International Arrival --")
    db.query(AirportService).filter_by(
        airport_id=amd_airport.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    plat_svc = service_map.get("platinum")
    if plat_svc:
        plat_mapping = AirportService(
            id=uuid.uuid4(),
            airport_id=amd_airport.id,
            service_id=plat_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Premium international arrival assistance from post-immigration to the airport exit.",
            features=[
                "Welcome after Immigration",
                "Assistance at the Duty Free Shop",
                "Assistance at the Baggage Belt Area",
                "Assistance after Customs Clearance",
                "Coordination with the Receiving Party",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2750.00,
            currency="INR",
        )
        db.add(plat_mapping)

    db.flush()
    print("  + Created AMD International Arrival Package: Platinum Service (INR 2,750)")


def seed_hyd_production_packages(db: Session, hyd_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Hyderabad Airport (HYD) Domestic Departure & Domestic Arrival:
    Seeds production Silver Service (₹3,000), Gold Service (₹3,500), and Elite Service (₹5,000) packages.
    """
    print("\n-- Configuring Production Packages for Hyderabad (HYD) Domestic Departure & Domestic Arrival --")
    
    # 1. Remove old demo services mapped to HYD Domestic Departure
    db.query(AirportService).filter_by(
        airport_id=hyd_airport.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    # 1a. Remove old demo services mapped to HYD Domestic Arrival
    db.query(AirportService).filter_by(
        airport_id=hyd_airport.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    silver_svc = service_map.get("silver")
    gold_svc = service_map.get("gold")
    elite_svc = service_map.get("elite")

    # ── DEPARTURE PACKAGES ──
    # 1. Silver Service (₹3,000)
    if silver_svc:
        silver_dep = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Premium domestic departure assistance from curbside to the boarding gate with dedicated airport support.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Buggy Service to the Boarding Gate (Sharing Basis, subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3000.00,
            currency="INR",
        )
        db.add(silver_dep)

    # 2. Gold Service (₹3,500)
    if gold_svc:
        gold_dep = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=gold_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Premium domestic departure assistance with lounge access and personalized airport support from curbside to the boarding gate.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Check-in at the Airline Counters",
                "Assistance inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access",
                "Buggy Service to the Boarding Gate",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=3500.00,
            currency="INR",
        )
        db.add(gold_dep)

    # 3. Elite Service (₹5,000)
    if elite_svc:
        elite_dep = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic departure experience with lounge access, dedicated airport assistance, and flexible booking benefits.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Check-in at the Airline Counters",
                "Assistance inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access",
                "Buggy Service to the Boarding Gate",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "Unlimited rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=5000.00,
            currency="INR",
        )
        db.add(elite_dep)

    # ── ARRIVAL PACKAGES ──
    # 1. Silver Service (₹3,000)
    if silver_svc:
        silver_arr = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Premium domestic arrival assistance from the aerobridge to the car parking area.",
            features=[
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (Sharing Basis, subject to availability)",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3000.00,
            currency="INR",
        )
        db.add(silver_arr)

    # 2. Gold Service (₹3,500)
    if gold_svc:
        gold_arr = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=gold_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Enhanced domestic arrival assistance with dedicated airport support from the aerobridge to the car parking area.",
            features=[
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Dedicated Buggy Service from the End of the Aerobridge",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=3500.00,
            currency="INR",
        )
        db.add(gold_arr)

    # 3. Elite Service (₹5,000)
    if elite_svc:
        elite_arr = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic arrival experience with flexible booking benefits and personalized airport assistance.",
            features=[
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Dedicated Buggy Service from the End of the Aerobridge",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "Unlimited rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=5000.00,
            currency="INR",
        )
        db.add(elite_arr)
    # 1b. Remove old demo services mapped to HYD International Departure
    db.query(AirportService).filter_by(
        airport_id=hyd_airport.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    # ── INTERNATIONAL DEPARTURE PACKAGES ──
    # 1. Silver Service (₹5,000)
    if silver_svc:
        silver_intl_dep = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Premium international departure assistance from the curbside to the boarding gate.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=5000.00,
            currency="INR",
        )
        db.add(silver_intl_dep)

    # 2. Gold Service (₹5,500)
    if gold_svc:
        gold_intl_dep = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=gold_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Enhanced international departure assistance with lounge access and personalized airport support.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access (up to 2 hours)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=5500.00,
            currency="INR",
        )
        db.add(gold_intl_dep)

    # 3. Elite Service (₹7,000)
    if elite_svc:
        elite_intl_dep = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international departure experience with lounge access, personalized airport assistance, and flexible booking benefits.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access (up to 2 hours)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "Unlimited rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7000.00,
            currency="INR",
        )
        db.add(elite_intl_dep)
    # 1c. Remove old demo services mapped to HYD International Arrival
    db.query(AirportService).filter_by(
        airport_id=hyd_airport.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    # ── INTERNATIONAL ARRIVAL PACKAGES ──
    # 1. Silver Service (₹2,500)
    if silver_svc:
        silver_intl_arr = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Premium international arrival assistance from post-customs to the car parking area.",
            features=[
                "Assistance after Customs Clearance",
                "Assistance at the Baggage Belt Area",
                "Coordination with the Receiving Party",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2500.00,
            currency="INR",
        )
        db.add(silver_intl_arr)

    # 2. Gold Service (₹3,500)
    if gold_svc:
        gold_intl_arr = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=gold_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Enhanced international arrival assistance with VIP parking facilitation and personalized airport support.",
            features=[
                "Assistance after Customs Clearance",
                "Assistance at the Baggage Belt Area",
                "Coordination with the Receiving Party",
                "Escort to the Car Parking Area",
                "VIP Car Parking Facilitation",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=3500.00,
            currency="INR",
        )
        db.add(gold_intl_arr)

    # 3. Elite Service (₹4,500)
    if elite_svc:
        elite_intl_arr = AirportService(
            id=uuid.uuid4(),
            airport_id=hyd_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international arrival experience with VIP parking facilitation and flexible booking benefits.",
            features=[
                "Assistance after Customs Clearance",
                "Assistance at the Baggage Belt Area",
                "Coordination with the Receiving Party",
                "Escort to the Car Parking Area",
                "VIP Car Parking Facilitation",
            ],
            additional_benefits=[
                "Free cancellation up to 14 hours before the scheduled service time",
                "Unlimited rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=4500.00,
            currency="INR",
        )
        db.add(elite_intl_arr)

    db.flush()
    print("  + Created HYD Production Packages (All Categories - Dom Dep/Arr & Intl Dep/Arr): Silver, Gold, and Elite")


def seed_del_production_packages(db: Session, del_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Delhi Airport (DEL) Domestic Departure:
    Seeds terminal-specific production packages for Terminal 1 & 2 and Terminal 3.
    - Terminal 1 & 2: Silver (₹3,000) and Elite (₹5,000)
    - Terminal 3: Silver (₹3,000), Gold (₹3,500), and Elite (₹5,000)
    """
    print("\n-- Configuring Production Packages for Delhi (DEL) Domestic Departure (T1 & T2, T3) --")

    try:
        db.execute(text("ALTER TABLE airport_services ALTER COLUMN flight_type TYPE VARCHAR(50);"))
        db.commit()
    except Exception:
        db.rollback()

    # Remove old services mapped to DEL Domestic Departure, Domestic Arrival T1 & T2, Intl Departure T3, Intl Arrival T3, and Transit
    db.query(AirportService).filter_by(
        airport_id=del_airport.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=del_airport.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
        terminal="Terminal 1 & 2",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=del_airport.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
        terminal="Terminal 3",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=del_airport.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
        terminal="Terminal 3",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=del_airport.id,
        journey_type="TRANSIT",
    ).delete(synchronize_session=False)

    silver_svc = service_map.get("silver")
    gold_svc = service_map.get("gold")
    elite_svc = service_map.get("elite")

    # ── Domestic Departure: Terminal 1 & 2 Packages ──
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            terminal="Terminal 1 & 2",
            short_description="Premium domestic departure assistance from curbside to gate (Terminal 1 & 2).",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3000.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            terminal="Terminal 1 & 2",
            short_description="Complete premium domestic departure experience with flexible booking benefits (Terminal 1 & 2).",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Unlimited Rescheduling (with at least 12 hours' prior notice)",
                "Free Cancellation up to 12 hours before the scheduled service time",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=5000.00,
            currency="INR",
        ))

    # ── Domestic Arrival: Terminal 1 & 2 Packages ──
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            terminal="Terminal 1 & 2",
            short_description="Premium domestic arrival assistance from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3000.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            terminal="Terminal 1 & 2",
            short_description="Complete premium domestic arrival assistance with personalized airport support and flexible booking benefits.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[
                "Unlimited Rescheduling (with at least 12 hours' prior notice)",
                "Free Cancellation up to 12 hours before the scheduled service time",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=5000.00,
            currency="INR",
        ))

    # ── Domestic Departure: Terminal 3 Packages ──
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            terminal="Terminal 3",
            short_description="Premium domestic departure assistance from curbside area to boarding gate (Terminal 3).",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Buggy Service (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3000.00,
            currency="INR",
        ))

    if gold_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=gold_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            terminal="Terminal 3",
            short_description="Enhanced domestic departure assistance with lounge access and airport support (Terminal 3).",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access (up to 2 hours)",
                "Buggy Service (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=3500.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            terminal="Terminal 3",
            short_description="Complete premium domestic departure experience with lounge access and flexible booking benefits (Terminal 3).",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access (up to 2 hours)",
                "Buggy Service (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Unlimited Rescheduling (with at least 12 hours' prior notice)",
                "Free Cancellation up to 12 hours before the scheduled service time",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=5000.00,
            currency="INR",
        ))

    # ── International Departure: Terminal 3 Packages ──
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            short_description="Premium international departure assistance from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=5500.00,
            currency="INR",
        ))

    if gold_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=gold_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            short_description="Enhanced international departure assistance with lounge access and premium airport support.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access (up to 2 hours)",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=6500.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            short_description="Complete premium international departure experience with lounge access, flexible booking benefits, and personalized airport assistance.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access (up to 2 hours)",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Unlimited Rescheduling (with at least 12 hours' prior notice)",
                "Free Cancellation up to 12 hours before the scheduled service time",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7000.00,
            currency="INR",
        ))

    # ── International Arrival: Terminal 3 Packages ──
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            short_description="Premium international arrival assistance from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (Sharing Basis, subject to availability)",
                "Assistance through Immigration",
                "Assistance at the Duty Free Shop",
                "Assistance at the Baggage Belt Area",
                "Assistance through Customs Clearance",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=5500.00,
            currency="INR",
        ))

    if gold_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=gold_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            short_description="Enhanced international arrival assistance with lounge access and premium airport support.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (Sharing Basis, subject to availability)",
                "Assistance through Immigration",
                "Assistance at the Duty Free Shop",
                "Assistance at the Baggage Belt Area",
                "Complimentary Lounge Access (up to 2 hours)",
                "Assistance through Customs Clearance",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=6000.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            terminal="Terminal 3",
            short_description="Complete premium international arrival experience with lounge access, flexible booking benefits, and personalized airport assistance.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (Sharing Basis, subject to availability)",
                "Assistance through Immigration",
                "Assistance at the Duty Free Shop",
                "Assistance at the Baggage Belt Area",
                "Complimentary Lounge Access (up to 2 hours)",
                "Assistance through Customs Clearance",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[
                "Unlimited Rescheduling (with at least 12 hours' prior notice)",
                "Free Cancellation up to 12 hours before the scheduled service time",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7000.00,
            currency="INR",
        ))

    # ── Transit Packages ──
    transit_service = service_map.get("meet_greet") or silver_svc

    if transit_service:
        # 1. Domestic → Domestic
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=transit_service.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_DOMESTIC",
            short_description="Premium domestic transit assistance between connecting domestic flights.",
            features=[
                "Welcome at the Aerobridge or Bus Gate",
                "Dedicated Baggage Assistance",
                "Buggy Service (Terminal 3 only, subject to availability)",
                "Assistance at the Baggage Belt Area (if required)",
                "Assistance with Terminal Change (T2 ↔ T3, if required)",
                "Assistance at Airline Counters",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=5500.00,
            currency="INR",
        ))

        # 2. Domestic → International
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=transit_service.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_INTERNATIONAL",
            short_description="Premium transit assistance for passengers connecting from a domestic flight to an international flight.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Baggage Assistance",
                "Buggy Service (Terminal 3 only, subject to availability)",
                "Assistance at the Baggage Belt Area (if required)",
                "Assistance with Terminal Change (T2 ↔ T3, if required)",
                "Assistance at Airline Counters",
                "Guidance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=7500.00,
            currency="INR",
        ))

        # 3. International → Domestic
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=transit_service.id,
            journey_type="TRANSIT",
            flight_type="INTERNATIONAL_DOMESTIC",
            short_description="Premium transit assistance for passengers connecting from an international flight to a domestic flight.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Baggage Assistance",
                "Buggy Service (Terminal 3 only, subject to availability)",
                "Assistance at the Baggage Belt Area",
                "Assistance with Terminal Change (T3 → T2, if required)",
                "Assistance at Airline Counters",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=7500.00,
            currency="INR",
        ))

        # 4. International → International
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=del_airport.id,
            service_id=transit_service.id,
            journey_type="TRANSIT",
            flight_type="INTERNATIONAL_INTERNATIONAL",
            short_description="Premium international-to-international transit assistance.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Baggage Assistance",
                "Buggy Service (subject to availability)",
                "Assistance at Airline Counters (Transit Area)",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=4,
            price=9500.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created DEL Production Packages for T1 & T2 (Departure & Arrival), T3 (Domestic Dep, Intl Dep & Intl Arr), and Transit")


def seed_lko_production_packages(db: Session, lko_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Lucknow Airport (LKO):
    Seeds production packages:
    - Domestic Departure: Platinum (INR 2,420) and Elite (INR 4,400)
    - Domestic Arrival: Platinum (INR 2,420) and Elite (INR 4,400)
    - International Departure: Platinum (INR 3,300) and Elite (INR 4,950)
    - International Arrival: Platinum (INR 2,750)
    """
    print("\n-- Configuring Production Packages for Lucknow (LKO) --")

    # Remove old Domestic Departure and Domestic Arrival services for LKO
    db.query(AirportService).filter_by(
        airport_id=lko_airport.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=lko_airport.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    # Remove old International Departure and International Arrival services for LKO
    db.query(AirportService).filter_by(
        airport_id=lko_airport.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=lko_airport.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    platinum_svc = service_map.get("platinum")
    elite_svc = service_map.get("elite")

    # ── Domestic Departure Packages ──
    if platinum_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=platinum_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Premium domestic departure assistance from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2420.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic departure assistance with lounge access and personalized airport support.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Check-in at the Airline Counters",
                "Assistance inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "Minimum 6 hours' prior notice required for rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4400.00,
            currency="INR",
        ))

    # ── Domestic Arrival Packages ──
    if platinum_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=platinum_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Premium domestic arrival assistance from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2420.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic arrival assistance with personalized airport support.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "Minimum 6 hours' prior notice required for rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4400.00,
            currency="INR",
        ))

    # ── International Departure Packages ──
    if platinum_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=platinum_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Premium international departure assistance from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3300.00,
            currency="INR",
        ))

    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international departure assistance with lounge access and personalized airport support.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access (up to 2 hours)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Free cancellation up to 12 hours before the scheduled service time",
                "Minimum 6 hours' prior notice required for rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4950.00,
            currency="INR",
        ))

    # ── International Arrival Packages ──
    if platinum_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=lko_airport.id,
            service_id=platinum_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Premium international arrival assistance from post-immigration to the car parking area.",
            features=[
                "Welcome after Immigration",
                "Assistance at the Duty Free Shop",
                "Assistance at the Baggage Belt Area",
                "Assistance after Customs Clearance",
                "Coordination with the Receiving Party",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2750.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created LKO Production Packages: Domestic Dep/Arr + International Dep/Arr")


def seed_ccu_production_packages(db: Session, ccu_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Kolkata Airport (CCU):
    Seeds production packages:
    - Domestic Departure: Silver (INR 2,200) and Gold (INR 3,500)
    - Domestic Arrival: Silver (INR 1,500)
    - International Departure: Silver (INR 3,000)
    - International Arrival: Silver (INR 2,000)
    """
    print("\n-- Configuring Production Packages for Kolkata (CCU) --")

    # Remove all existing services for CCU
    db.query(AirportService).filter_by(
        airport_id=ccu_airport.id,
        journey_type="DEPARTURE",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=ccu_airport.id,
        journey_type="ARRIVAL",
        flight_type="DOMESTIC",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=ccu_airport.id,
        journey_type="DEPARTURE",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    db.query(AirportService).filter_by(
        airport_id=ccu_airport.id,
        journey_type="ARRIVAL",
        flight_type="INTERNATIONAL",
    ).delete(synchronize_session=False)

    silver_svc = service_map.get("silver")
    gold_svc = service_map.get("gold")

    # -- Domestic Departure Packages --
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=ccu_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Premium domestic departure assistance from the departure curbside to the boarding gate.",
            features=[
                "Welcome at the Departure Curbside / Car Drop Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance at the Domestic Departure Area",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2200.00,
            currency="INR",
        ))

    if gold_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=ccu_airport.id,
            service_id=gold_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Enhanced domestic departure assistance with lounge access and personalized airport support.",
            features=[
                "Welcome at the Departure Curbside / Car Drop Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance at the Domestic Departure Area",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Complimentary Lounge Access (up to 2 hours)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=3500.00,
            currency="INR",
        ))

    # -- Domestic Arrival Packages --
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=ccu_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Premium domestic arrival assistance from the baggage belt area to the car parking area.",
            features=[
                "Welcome near the Baggage Belt Area",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=1500.00,
            currency="INR",
        ))

    # -- International Departure Packages --
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=ccu_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Premium international departure assistance from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside / Car Drop Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline, if required)",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3000.00,
            currency="INR",
        ))

    # -- International Arrival Packages --
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=ccu_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Premium international arrival assistance from post-customs to the car parking area.",
            features=[
                "Welcome after Customs Clearance",
                "Dedicated Porter Service (up to 3 bags per passenger)",
                "Assistance at the Baggage Belt Area",
                "Coordination with the Receiving Party",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2000.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created CCU Production Packages: Domestic Dep/Arr + International Dep/Arr")


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
            short_description="Premium domestic departure assistance from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Buggy Service to the Boarding Gate (Sharing Basis, subject to availability)",
                "Escort to the Boarding Gate",
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
            short_description="Enhanced domestic departure assistance with lounge access and dedicated airport support.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Separate Check-in at the Airline Counters",
                "Assistance inside the Security Hold Area (SHA)",
                "Lounge Service Facility",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
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
            short_description="Complete premium domestic departure assistance with lounge access and flexible booking benefits.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Separate Check-in at the Airline Counters",
                "Assistance inside the Security Hold Area (SHA)",
                "Lounge Service Facility",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
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
            short_description="Premium domestic arrival assistance from the end of the aerobridge to the car parking area.",
            features=[
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (Sharing Basis, subject to availability)",
                "Wheelchair Assistance (through the airline)",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
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
            short_description="Enhanced domestic arrival assistance with dedicated airport support from the aerobridge to the car parking area.",
            features=[
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (subject to availability)",
                "Wheelchair Assistance (through the airline)",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
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
            short_description="Complete premium domestic arrival assistance with flexible booking benefits.",
            features=[
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (subject to availability)",
                "Wheelchair Assistance (through the airline)",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
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
            short_description="Premium international departure assistance from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
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
            short_description="Enhanced international departure assistance with lounge access and dedicated airport support.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Lounge Service Facility",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
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
            short_description="Complete premium international departure assistance with lounge access and flexible booking benefits.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Lounge Service Facility",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
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
            short_description="Premium international arrival assistance from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (Sharing Basis, subject to availability)",
                "Assistance through the Immigration Counter",
                "Assistance at the Duty Free Shop",
                "Assistance at the Baggage Belt Area",
                "Assistance through Customs",
                "Escort to the Car Parking Area",
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
            short_description="Enhanced international arrival assistance with dedicated airport support from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge (subject to availability)",
                "Assistance through the Immigration Counter",
                "Assistance at the Duty Free Shop",
                "Assistance at the Baggage Belt Area",
                "Assistance through Customs",
                "Escort to the Car Parking Area",
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
            short_description="Complete premium international arrival assistance with flexible booking benefits.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the Aerobridge (subject to availability)",
                "Assistance at the Baggage Belt Area",
                "Assistance through the Immigration Counter",
                "Assistance at the Duty Free Shop",
                "Escort to the Parking Area",
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
    transit_svc = service_map.get("meet_greet") or plat_svc

    if transit_svc:
        # 1. Domestic → Domestic (₹7,150)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_DOMESTIC",
            short_description="Domestic transit assistance from arrival through the connecting flight boarding gate.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Wheelchair Assistance (through the airline)",
                "Buggy Service from the End of the Aerobridge",
                "Assistance inside the Security Hold Area (Transit Area)",
                "Lounge Access for up to 2 hours (Departure only)",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=7150.00,
            currency="INR",
        ))

        # 2. Domestic → International (₹9,000 - DRAFT / INACTIVE)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="DOMESTIC_INTERNATIONAL",
            short_description="Domestic to international transit assistance.",
            features=[],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=False,  # DRAFT / INACTIVE
            display_priority=2,
            price=9000.00,
            currency="INR",
        ))

        # 3. International → Domestic (₹9,000)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="INTERNATIONAL_DOMESTIC",
            short_description="International-to-domestic transit assistance from arrival through the connecting domestic boarding gate.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Buggy Service from the End of the Aerobridge",
                "Wheelchair Assistance (through the airline)",
                "Guidance to the Immigration Counter",
                "Assistance at the Baggage Belt Area",
                "Assistance with Separate Check-in at the Airline Counters",
                "Assistance inside the Security Hold Area (SHA)",
                "Lounge Access for up to 2 hours (Departure only)",
                "Buggy Service to the Boarding Gate (subject to availability)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=3,
            price=9000.00,
            currency="INR",
        ))

        # 4. International → International (₹10,000)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=bom_airport.id,
            service_id=transit_svc.id,
            journey_type="TRANSIT",
            flight_type="INTERNATIONAL_INTERNATIONAL",
            short_description="International transit assistance from the arriving flight through the airport transit process to the next connecting flight.",
            features=[
                "Warm welcome at the Aerobridge or Bus Gate by a porter",
                "Dedicated porter assistance from the Aerobridge on arrival to the boarding gate of the next connecting flight",
                "Guidance through the airport and airline transit process",
                "Facilitation through security according to the passenger's class of travel",
                "Adani Lounge access with snacks, food, and non-alcoholic beverages",
                "Golf cart transfer to the lounge or boarding gate, subject to the boarding gate location",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=4,
            price=10000.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created BOM Production Packages: Domestic & International Departure & Arrival + Transit")


def seed_goi_production_packages(db: Session, goi_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Goa Dabolim Airport (GOI):
    Seeds production packages:
    - Domestic Departure: Silver Service (₹2,500), Gold Service (₹4,000)
    - Domestic Arrival: Silver Service (₹2,500)
    - International Departure: Silver Service (₹2,000)
    - International Arrival: Silver Service (₹2,000)
    """
    print("\n-- Configuring Production Packages for Goa Dabolim Airport (GOI) --")

    # 1. Clear all existing services mapped to GOI to avoid duplicates
    db.query(AirportService).filter_by(
        airport_id=goi_airport.id,
    ).delete(synchronize_session=False)

    silver_svc = service_map.get("silver")
    gold_svc = service_map.get("gold")

    # ── 1. DOMESTIC DEPARTURE PACKAGES ──
    # Silver Service (₹2,500)
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=goi_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Domestic departure assistance from the departure curbside to the boarding gate.",
            features=[
                "Welcome at the Departure Curbside / Car Drop Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance at the Domestic Departure Area",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2500.00,
            currency="INR",
        ))

    # Gold Service (₹4,000)
    if gold_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=goi_airport.id,
            service_id=gold_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Enhanced domestic departure assistance with lounge service and dedicated airport support.",
            features=[
                "Welcome at the Departure Curbside / Car Drop Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance at the Domestic Departure Area",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Lounge Service Facility",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4000.00,
            currency="INR",
        ))

    # ── 2. DOMESTIC ARRIVAL PACKAGES ──
    # Silver Service (₹2,500)
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=goi_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Domestic arrival assistance from the end of the aerobridge to the car parking area.",
            features=[
                "Welcome at the End of the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Wheelchair Assistance (through the airline)",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2500.00,
            currency="INR",
        ))

    # ── 3. INTERNATIONAL DEPARTURE PACKAGES ──
    # Silver Service (₹2,000)
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=goi_airport.id,
            service_id=silver_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="International departure assistance from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside Area / Car Drop Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2000.00,
            currency="INR",
        ))

    # ── 4. INTERNATIONAL ARRIVAL PACKAGES ──
    # Silver Service (₹2,000)
    if silver_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=goi_airport.id,
            service_id=silver_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="International arrival assistance from post-customs to the car parking area.",
            features=[
                "Welcome after Customs Clearance",
                "Dedicated Porter Service (up to 3 bags per passenger)",
                "Assistance at the Baggage Belt Area",
                "Coordination with the Receiving Party",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2000.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created GOI Production Packages: Domestic Dep (Silver INR 2,500, Gold INR 4,000), Domestic Arr (Silver INR 2,500), Intl Dep (Silver INR 2,000), Intl Arr (Silver INR 2,000)")


def seed_jai_production_packages(db: Session, jai_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Jaipur International Airport (JAI):
    Seeds production packages:
    - Domestic Departure: Platinum Service (₹2,420), Elite Service (₹4,400)
    - Domestic Arrival: Platinum Service (₹2,420), Elite Service (₹4,400)
    """
    print("\n-- Configuring Production Packages for Jaipur International Airport (JAI) --")

    # 1. Clear all existing services mapped to JAI to avoid duplicates
    db.query(AirportService).filter_by(
        airport_id=jai_airport.id,
    ).delete(synchronize_session=False)

    plat_svc = service_map.get("platinum")
    elite_svc = service_map.get("elite")

    # ── 1. DOMESTIC DEPARTURE PACKAGES ──
    # Platinum Service (₹2,420)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=plat_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Premium domestic departure assistance from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2420.00,
            currency="INR",
        ))

    # Elite Service (₹4,400)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Complete premium domestic departure assistance with lounge service and flexible booking benefits.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance through the Separate Entry Gate",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance at the Separate Check-in Process at the Counters",
                "Assistance inside the Security Hold Area (SHA)",
                "Lounge Service Facility",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Cancellation benefits up to 12 hours before the scheduled service time",
                "Minimum 6 hours' prior notice required for rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4400.00,
            currency="INR",
        ))

    # ── 2. DOMESTIC ARRIVAL PACKAGES ──
    # Platinum Service (₹2,420)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=plat_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Premium domestic arrival assistance from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Wheelchair Assistance (through the airline)",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2420.00,
            currency="INR",
        ))

    # Elite Service (₹4,400)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=elite_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Premium domestic arrival assistance with dedicated airport support from the aerobridge to the car parking area.",
            features=[
                "Welcome at the Aerobridge",
                "Dedicated Staff with Personalized Placard",
                "Dedicated Porter Service at Arrivals",
                "Wheelchair Assistance (through the airline)",
                "Assistance at the Baggage Belt Area",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4400.00,
            currency="INR",
        ))

    # ── 3. INTERNATIONAL DEPARTURE PACKAGES ──
    # 1. Platinum Service (₹3,300)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=plat_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Premium international departure assistance from the curbside area to the boarding gate.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=3300.00,
            currency="INR",
        ))

    # 2. Elite Service (₹4,950)
    if elite_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=elite_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="Complete premium international departure assistance with lounge access and flexible booking benefits.",
            features=[
                "Welcome at the Curbside Area",
                "Dedicated Porter Service",
                "Wheelchair Assistance (through the airline)",
                "Assistance through the Separate Entry Gate",
                "Assistance at the Money Exchange Counter",
                "Assistance with Baggage Wrapping Facilities",
                "Assistance with Separate Baggage Check-in at the Airline Counter",
                "Assistance through Immigration",
                "Assistance inside the Security Hold Area (SHA)",
                "Lounge Service Facility (up to 2 hours)",
                "Escort to the Boarding Gate",
            ],
            additional_benefits=[
                "Cancellation benefits up to 12 hours before the scheduled service time",
                "Minimum 6 hours' prior notice required for rescheduling",
            ],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=2,
            price=4950.00,
            currency="INR",
        ))

    # ── 4. INTERNATIONAL ARRIVAL PACKAGES ──
    # 1. Platinum Service (₹2,750)
    if plat_svc:
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=jai_airport.id,
            service_id=plat_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="Premium international arrival assistance from post-immigration through customs to the car parking area.",
            features=[
                "Welcome after Immigration",
                "Assistance at the Duty Free Shop",
                "Assistance at the Baggage Belt Area",
                "Assistance after Customs",
                "Coordination with the Receiving Person",
                "Escort to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2750.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created JAI Production Packages: Domestic & International Departure & Arrival")


def seed_atq_production_packages(db: Session, atq_airport: SupportedAirport, service_map: dict[str, Service]):
    """
    For Amritsar Airport (ATQ):
    Seeds production packages (Price ₹2,500 across all categories):
    - Domestic Departure: ₹2,500
    - Domestic Arrival: ₹2,500
    - International Departure: ₹2,500
    - International Arrival: ₹2,500
    """
    print("\n-- Configuring Production Packages for Amritsar Airport (ATQ) --")

    # 1. Clear all existing services mapped to ATQ to avoid duplicates
    db.query(AirportService).filter_by(
        airport_id=atq_airport.id,
    ).delete(synchronize_session=False)

    meet_svc = service_map.get("meet_greet") or service_map.get("platinum")

    if meet_svc:
        # 1. Domestic Departure (₹2,500)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=atq_airport.id,
            service_id=meet_svc.id,
            journey_type="DEPARTURE",
            flight_type="DOMESTIC",
            short_description="Domestic departure assistance from the curbside area to the boarding gate.",
            features=[
                "Welcome Guest from Curbside Area",
                "Porter Service with Dedicated Staff",
                "Wheelchair Service Available (Through Airlines)",
                "Assistance from Separate Entry Gate",
                "Assistance to Baggage Wrapping Facilities",
                "Assistance at Separate Check-in Process at Counters",
                "Assistance in Security Hold Area (SHA)",
                "Lounge Service Facility Available (Charges Applicable)",
                "Assistance to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2500.00,
            currency="INR",
        ))

        # 2. Domestic Arrival (₹2,500)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=atq_airport.id,
            service_id=meet_svc.id,
            journey_type="ARRIVAL",
            flight_type="DOMESTIC",
            short_description="Domestic arrival assistance from the aerobridge to the car parking area.",
            features=[
                "Welcome Guest from Aerobridge",
                "Dedicated Staff with Placard",
                "Porter Service with Dedicated Staff at Arrivals",
                "Wheelchair Service Available (Through Airlines)",
                "Assistance in Baggage Belt Area",
                "Assistance to the Car Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2500.00,
            currency="INR",
        ))

        # 3. International Departure (₹2,000)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=atq_airport.id,
            service_id=meet_svc.id,
            journey_type="DEPARTURE",
            flight_type="INTERNATIONAL",
            short_description="International departure assistance from the curbside area to the boarding gate.",
            features=[
                "Welcome Guest from Curbside Area",
                "Porter Service with Dedicated Staff",
                "Wheelchair Service Available (Through Airlines)",
                "Assistance from Separate Entry Gate",
                "Assistance to Baggage Wrapping Facilities",
                "Assistance at Separate Check-in Process at Counters",
                "Assistance in Security Hold Area (SHA)",
                "Lounge Service Facility Available (Charges Applicable)",
                "Assistance to the Boarding Gate",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2000.00,
            currency="INR",
        ))

        # 4. International Arrival (₹2,500)
        db.add(AirportService(
            id=uuid.uuid4(),
            airport_id=atq_airport.id,
            service_id=meet_svc.id,
            journey_type="ARRIVAL",
            flight_type="INTERNATIONAL",
            short_description="International arrival assistance from post-immigration to the parking area.",
            features=[
                "Welcome Guest from Post Immigration",
                "Porter Service with Dedicated Staff at Post Immigration Area",
                "Assistance in Baggage Belt Area",
                "Assistance in Customs",
                "Coordination with Receiving Party",
                "Assistance to the Parking Area",
            ],
            additional_benefits=[],
            min_booking_notice_hours=6,
            is_available=True,
            display_priority=1,
            price=2500.00,
            currency="INR",
        ))

    db.flush()
    print("  + Created ATQ Production Packages: Domestic & International Departure & Arrival (INR 2,500)")


def seed_other_airport_services(db: Session, airport_map: dict[str, SupportedAirport], service_map: dict[str, Service]):
    """Seed default services for other airports."""
    for code, airport in airport_map.items():
        if code in ("AMD", "BOM", "GOI", "JAI", "ATQ"):
            continue  # Handled separately

        for slug, svc in service_map.items():
            if slug in ("platinum", "elite", "silver", "elite_plus"):
                continue
            for j_type in JOURNEY_TYPES:
                if code == "HYD":
                    continue  # Handled separately by seed_hyd_production_packages
                if code == "LKO":
                    continue  # Handled separately by seed_lko_production_packages
                if code == "CCU":
                    continue  # Handled separately by seed_ccu_production_packages
                if code == "DEL" and j_type == "DEPARTURE":
                    continue  # Handled separately by seed_del_production_packages

                existing = db.query(AirportService).filter_by(
                    airport_id=airport.id,
                    service_id=svc.id,
                    journey_type=j_type,
                    flight_type="DOMESTIC",
                ).first()

                if not existing:
                    mapping = AirportService(
                        id=uuid.uuid4(),
                        airport_id=airport.id,
                        service_id=svc.id,
                        journey_type=j_type,
                        flight_type="DOMESTIC",
                        short_description=svc.description,
                        features=[
                            "Aerobridge exit welcome with placard",
                            "Priority baggage assistance",
                            "Executive handoff to chauffeur",
                        ],
                        min_booking_notice_hours=6,
                        is_available=True,
                        display_priority=svc.display_order,
                        price=2499.00 if slug == "meet_greet" else 1999.00,
                        currency="INR",
                    )
                    db.add(mapping)
    db.flush()


def run_seed():
    print("\n==================================================")
    print("  Shafsky Aviation -- Production Package Seeder   ")
    print("==================================================\n")

    db = SessionLocal()
    try:
        from sqlalchemy import text
        db.execute(text("ALTER TABLE airport_services ADD COLUMN IF NOT EXISTS terminal VARCHAR(50);"))
        db.execute(text("ALTER TABLE airport_services DROP CONSTRAINT IF EXISTS uq_airport_service_journey_flight;"))
        db.commit()

        airport_map = seed_airports(db)
        service_map = seed_services(db)

        if "AMD" in airport_map:
            seed_amd_production_packages(db, airport_map["AMD"], service_map)

        if "BOM" in airport_map:
            seed_bom_production_packages(db, airport_map["BOM"], service_map)

        if "GOI" in airport_map:
            seed_goi_production_packages(db, airport_map["GOI"], service_map)

        if "JAI" in airport_map:
            seed_jai_production_packages(db, airport_map["JAI"], service_map)

        if "ATQ" in airport_map:
            seed_atq_production_packages(db, airport_map["ATQ"], service_map)

        if "HYD" in airport_map:
            seed_hyd_production_packages(db, airport_map["HYD"], service_map)

        if "DEL" in airport_map:
            seed_del_production_packages(db, airport_map["DEL"], service_map)

        if "LKO" in airport_map:
            seed_lko_production_packages(db, airport_map["LKO"], service_map)

        if "CCU" in airport_map:
            seed_ccu_production_packages(db, airport_map["CCU"], service_map)

        seed_other_airport_services(db, airport_map, service_map)

        db.commit()
        print("\n[OK] Production packages seeded successfully!\n")
    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] Seeding failed: {e}\n")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_seed()

