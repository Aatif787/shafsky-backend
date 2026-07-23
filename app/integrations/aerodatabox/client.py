import httpx
import logging
import time
from typing import Dict, Any, Optional
from app.config import settings
from app.integrations.aerodatabox.exceptions import (
    ProviderUnavailableException,
    RateLimitException,
    TimeoutException,
    InvalidFlightException
)

logger = logging.getLogger("shafsky.integrations.aerodatabox")

class AeroDataBoxClient:
    _client: Optional[httpx.AsyncClient] = None

    @classmethod
    def get_headers(cls) -> Dict[str, str]:
        key = getattr(settings, "RAPIDAPI_KEY", "") or getattr(settings, "AERODATABOX_API_KEY", "")
        host = getattr(settings, "RAPIDAPI_HOST", "aerodatabox.p.rapidapi.com")
        return {
            "X-RapidAPI-Key": key,
            "X-RapidAPI-Host": host,
            "Accept": "application/json"
        }

    @classmethod
    def get_client(cls) -> httpx.AsyncClient:
        if cls._client is None or cls._client.is_closed:
            cls._client = httpx.AsyncClient(
                base_url=getattr(settings, "AERODATABOX_BASE_URL", "https://aerodatabox.p.rapidapi.com"),
                headers=cls.get_headers(),
                timeout=httpx.Timeout(10.0, connect=5.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
            )
        return cls._client

    @classmethod
    async def close_client(cls):
        if cls._client is not None and not cls._client.is_closed:
            await cls._client.aclose()
            cls._client = None

    @classmethod
    async def fetch_flight_status(cls, flight_number: str, date: str) -> Dict[str, Any]:
        client = cls.get_client()
        clean_flight = flight_number.strip().upper().replace(" ", "")
        endpoint = f"/flights/number/{clean_flight}/{date}"

        start_time = time.time()
        logger.info(f"Dispatching AeroDataBox API call for flight {clean_flight} on {date}")

        try:
            headers = cls.get_headers()
            response = await client.get(endpoint, headers=headers)
            latency = round(time.time() - start_time, 4)

            if response.status_code == 404:
                logger.warning(f"AeroDataBox: Flight {clean_flight} not found on {date}")
                raise InvalidFlightException(f"Flight '{clean_flight}' not found for date {date}.")
            elif response.status_code == 429:
                logger.error(f"AeroDataBox: Rate limit exceeded")
                raise RateLimitException("RapidAPI rate limit exceeded.")
            elif response.status_code >= 500:
                logger.error(f"AeroDataBox HTTP {response.status_code}")
                raise ProviderUnavailableException(f"AeroDataBox HTTP {response.status_code} Error")

            response.raise_for_status()
            data = response.json()
            return data

        except httpx.TimeoutException:
            logger.error(f"AeroDataBox request timeout for {clean_flight}")
            raise TimeoutException(f"AeroDataBox timed out for {clean_flight}")
        except (InvalidFlightException, RateLimitException, ProviderUnavailableException, TimeoutException):
            raise
        except Exception as e:
            logger.error(f"AeroDataBox execution error: {str(e)}")
            raise ProviderUnavailableException(f"AeroDataBox Client Exception: {str(e)}")
