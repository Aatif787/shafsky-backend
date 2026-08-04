"""
IATA Airport Registry and Metadata Enrichment Utility.

Provides standard IATA airport metadata (Name, City, Country, Timezone) for enrichment.
Never alters or overrides provider values when supplied.
"""

from typing import Dict, Any, Optional
from app.flight.schemas import FlightAirport

AIRPORT_REGISTRY: Dict[str, Dict[str, str]] = {
    "DEL": {"name": "Delhi Indira Gandhi International Airport", "city": "New Delhi", "country": "India", "timezone": "Asia/Kolkata"},
    "BOM": {"name": "Mumbai Chhatrapati Shivaji Maharaj International Airport", "city": "Mumbai", "country": "India", "timezone": "Asia/Kolkata"},
    "MAA": {"name": "Chennai International Airport", "city": "Chennai", "country": "India", "timezone": "Asia/Kolkata"},
    "BLR": {"name": "Bengaluru Kempegowda International Airport", "city": "Bengaluru", "country": "India", "timezone": "Asia/Kolkata"},
    "CCU": {"name": "Kolkata Netaji Subhash Chandra Bose International Airport", "city": "Kolkata", "country": "India", "timezone": "Asia/Kolkata"},
    "HYD": {"name": "Hyderabad Rajiv Gandhi International Airport", "city": "Hyderabad", "country": "India", "timezone": "Asia/Kolkata"},
    "COK": {"name": "Cochin International Airport", "city": "Kochi", "country": "India", "timezone": "Asia/Kolkata"},
    "AMD": {"name": "Sardar Vallabhbhai Patel International Airport", "city": "Ahmedabad", "country": "India", "timezone": "Asia/Kolkata"},
    "PNQ": {"name": "Pune Airport", "city": "Pune", "country": "India", "timezone": "Asia/Kolkata"},
    "DXB": {"name": "Dubai International Airport", "city": "Dubai", "country": "United Arab Emirates", "timezone": "Asia/Dubai"},
    "DOH": {"name": "Doha Hamad International Airport", "city": "Doha", "country": "Qatar", "timezone": "Asia/Qatar"},
    "AUH": {"name": "Abu Dhabi Zayed International Airport", "city": "Abu Dhabi", "country": "United Arab Emirates", "timezone": "Asia/Dubai"},
    "LHR": {"name": "London Heathrow Airport", "city": "London", "country": "United Kingdom", "timezone": "Europe/London"},
    "LGW": {"name": "London Gatwick Airport", "city": "London", "country": "United Kingdom", "timezone": "Europe/London"},
    "JFK": {"name": "New York John F. Kennedy International Airport", "city": "New York", "country": "United States", "timezone": "America/New_York"},
    "EWR": {"name": "Newark Liberty International Airport", "city": "Newark", "country": "United States", "timezone": "America/New_York"},
    "SFO": {"name": "San Francisco International Airport", "city": "San Francisco", "country": "United States", "timezone": "America/Los_Angeles"},
    "LAX": {"name": "Los Angeles International Airport", "city": "Los Angeles", "country": "United States", "timezone": "America/Los_Angeles"},
    "ORD": {"name": "Chicago O'Hare International Airport", "city": "Chicago", "country": "United States", "timezone": "America/Chicago"},
    "SIN": {"name": "Singapore Changi Airport", "city": "Singapore", "country": "Singapore", "timezone": "Asia/Singapore"},
    "CDG": {"name": "Paris Charles de Gaulle Airport", "city": "Paris", "country": "France", "timezone": "Europe/Paris"},
    "FRA": {"name": "Frankfurt Airport", "city": "Frankfurt", "country": "Germany", "timezone": "Europe/Berlin"},
    "AMS": {"name": "Amsterdam Airport Schiphol", "city": "Amsterdam", "country": "Netherlands", "timezone": "Europe/Amsterdam"},
    "HKG": {"name": "Hong Kong International Airport", "city": "Hong Kong", "country": "Hong Kong", "timezone": "Asia/Hong_Kong"},
    "BKK": {"name": "Bangkok Suvarnabhumi Airport", "city": "Bangkok", "country": "Thailand", "timezone": "Asia/Bangkok"},
    "HND": {"name": "Tokyo Haneda Airport", "city": "Tokyo", "country": "Japan", "timezone": "Asia/Tokyo"},
    "NRT": {"name": "Tokyo Narita International Airport", "city": "Tokyo", "country": "Japan", "timezone": "Asia/Tokyo"},
    "SYD": {"name": "Sydney Kingsford Smith Airport", "city": "Sydney", "country": "Australia", "timezone": "Australia/Sydney"},
    "MEL": {"name": "Melbourne Airport", "city": "Melbourne", "country": "Australia", "timezone": "Australia/Melbourne"},
}


def build_flight_airport(
    iata_code: Optional[str],
    raw_name: Optional[str] = None,
    raw_city: Optional[str] = None,
    raw_country: Optional[str] = None,
    terminal: Optional[str] = None,
    gate: Optional[str] = None
) -> Optional[FlightAirport]:
    """
    Builds a FlightAirport object trusting the exact IATA code from provider.
    Returns None if iata_code is missing/empty (never invents code).
    """
    if not iata_code:
        return None

    clean_code = iata_code.strip().upper()
    info = AIRPORT_REGISTRY.get(clean_code, {})

    name = raw_name or info.get("name")
    city = raw_city or info.get("city")
    country = raw_country or info.get("country")
    tz = info.get("timezone")

    return FlightAirport(
        code=clean_code,
        name=name,
        city=city,
        country=country,
        timezone=tz,
        terminal=terminal or None,
        gate=gate or None
    )
