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
    "LKO": {"name": "Chaudhary Charan Singh International Airport", "city": "Lucknow", "country": "India", "timezone": "Asia/Kolkata"},
    "GOI": {"name": "Dabolim International Airport", "city": "Goa", "country": "India", "timezone": "Asia/Kolkata"},
    "JAI": {"name": "Jaipur International Airport", "city": "Jaipur", "country": "India", "timezone": "Asia/Kolkata"},
    "ATQ": {"name": "Sri Guru Ram Dass Jee International Airport", "city": "Amritsar", "country": "India", "timezone": "Asia/Kolkata"},
    "TRV": {"name": "Trivandrum International Airport", "city": "Thiruvananthapuram", "country": "India", "timezone": "Asia/Kolkata"},
    "VTZ": {"name": "Visakhapatnam International Airport", "city": "Visakhapatnam", "country": "India", "timezone": "Asia/Kolkata"},
    "BBI": {"name": "Biju Patnaik International Airport", "city": "Bhubaneswar", "country": "India", "timezone": "Asia/Kolkata"},
    "IXC": {"name": "Shaheed Bhagat Singh International Airport", "city": "Chandigarh", "country": "India", "timezone": "Asia/Kolkata"},
    "GOX": {"name": "Manohar International Airport (Mopa)", "city": "Goa (Mopa)", "country": "India", "timezone": "Asia/Kolkata"},
    "GAU": {"name": "Lokpriya Gopinath Bordoloi International Airport", "city": "Guwahati", "country": "India", "timezone": "Asia/Kolkata"},
    "IXE": {"name": "Mangaluru International Airport", "city": "Mangaluru", "country": "India", "timezone": "Asia/Kolkata"},
    "IXR": {"name": "Birsa Munda Airport", "city": "Ranchi", "country": "India", "timezone": "Asia/Kolkata"},
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


def search_global_airports(query: str, limit: int = 15) -> list:
    """Search the IATA metadata registry. Allows any 3-letter IATA even if not listed."""
    q = (query or "").strip()
    results = []
    if not q:
        for code, info in list(AIRPORT_REGISTRY.items())[:limit]:
            results.append({
                "code": code,
                "name": info.get("name") or f"{code} Airport",
                "city": info.get("city") or code,
                "country": info.get("country") or "",
                "timezone": info.get("timezone"),
                "is_supported": False,
            })
        return results

    q_up = q.upper()
    for code, info in AIRPORT_REGISTRY.items():
        name = info.get("name") or ""
        city = info.get("city") or ""
        country = info.get("country") or ""
        if (
            code == q_up
            or code.startswith(q_up)
            or q_up in name.upper()
            or q_up in city.upper()
            or q_up in country.upper()
        ):
            results.append({
                "code": code,
                "name": name or f"{code} Airport",
                "city": city or code,
                "country": country,
                "timezone": info.get("timezone"),
                "is_supported": False,
            })
        if len(results) >= limit:
            break

    if not results and len(q_up) == 3 and q_up.isalpha():
        info = AIRPORT_REGISTRY.get(q_up, {})
        results.append({
            "code": q_up,
            "name": info.get("name") or f"{q_up} International Airport",
            "city": info.get("city") or q_up,
            "country": info.get("country") or "",
            "timezone": info.get("timezone"),
            "is_supported": False,
        })
    return results


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
