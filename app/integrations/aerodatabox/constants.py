from enum import Enum

class FlightStatusEnum(str, Enum):
    SCHEDULED = "Scheduled"
    BOARDING = "Boarding"
    DELAYED = "Delayed"
    DEPARTED = "Departed"
    ARRIVED = "Arrived"
    CANCELLED = "Cancelled"
    DIVERTED = "Diverted"
    UNKNOWN = "Unknown"

# Cache TTLs in Seconds (10 Minutes)
FLIGHT_CACHE_TTL = 600
AIRPORT_CACHE_TTL = 3600  # 1 Hour for static airport metadata
AIRLINE_CACHE_TTL = 3600  # 1 Hour for static airline metadata
AIRCRAFT_CACHE_TTL = 1800 # 30 Minutes for aircraft metadata

# Error Code Constants
ERR_INVALID_FLIGHT = "INVALID_FLIGHT"
ERR_PROVIDER_UNAVAILABLE = "FLIGHT_PROVIDER_UNAVAILABLE"
ERR_AIRPORT_NOT_FOUND = "AIRPORT_NOT_FOUND"
ERR_AIRLINE_NOT_FOUND = "AIRLINE_NOT_FOUND"
ERR_AIRCRAFT_NOT_FOUND = "AIRCRAFT_NOT_FOUND"
ERR_RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
ERR_TIMEOUT = "PROVIDER_TIMEOUT"
