import re
import httpx
from datetime import datetime
from typing import Dict, Any, Optional
from app.config import settings

class FlightDurationResolver:
    @staticmethod
    def parse_iso_duration(dur_str: str) -> Optional[str]:
        if not dur_str:
            return None
        dur_str = dur_str.strip()
        # ISO-8601 match e.g. PT2H15M
        match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?$", dur_str, re.IGNORECASE)
        if match:
            hours = int(match.group(1) or 0)
            mins = int(match.group(2) or 0)
            if hours > 0 or mins > 0:
                return f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
        
        # Standard match e.g. "2h 15m"
        match_std = re.match(r"^(\d+)\s*h\s*(\d+)\s*m$", dur_str, re.IGNORECASE)
        if match_std:
            return f"{match_std.group(1)}h {match_std.group(2)}m"
        
        return None

    @staticmethod
    def calculate_timestamp_delta(dep_iso: Optional[str], arr_iso: Optional[str]) -> Optional[str]:
        if not dep_iso or not arr_iso:
            return None
        try:
            dep_dt = datetime.fromisoformat(dep_iso.replace("Z", "+00:00"))
            arr_dt = datetime.fromisoformat(arr_iso.replace("Z", "+00:00"))
            diff_seconds = int((arr_dt - dep_dt).total_seconds())
            if diff_seconds > 0:
                hours = diff_seconds // 3600
                mins = (diff_seconds % 3600) // 60
                return f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
        except Exception:
            pass
        return None

    @classmethod
    async def resolve(cls, payload: Dict[str, Any]) -> Dict[str, str]:
        # Priority 1: Check payload provided duration fields
        for field in ["duration", "scheduledDuration", "estimatedDuration", "blockTime", "flightTime"]:
            parsed = cls.parse_iso_duration(payload.get(field, ""))
            if parsed:
                return {"duration": parsed, "source": "Live"}

        # Priority 2: Departure & Arrival Timestamp Delta
        delta = cls.calculate_timestamp_delta(payload.get("depTimeIso"), payload.get("arrTimeIso"))
        if delta:
            return {"duration": delta, "source": "Calculated"}

        # Priority 3: External Multi-Provider API Chain (AeroDataBox RapidAPI)
        flight_num = payload.get("flightNum")
        depart_date = payload.get("departDate")
        if flight_num and settings.AERODATABOX_API_KEY:
            try:
                url = f"https://aerodatabox.p.rapidapi.com/flights/number/{flight_num}"
                if depart_date:
                    url += f"/{depart_date}"
                headers = {
                    "X-RapidAPI-Key": settings.AERODATABOX_API_KEY,
                    "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com"
                }
                async with httpx.AsyncClient(timeout=4.0) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            flight = data[0]
                            dep_time = flight.get("departure", {}).get("scheduledTimeUtc")
                            arr_time = flight.get("arrival", {}).get("scheduledTimeUtc")
                            api_delta = cls.calculate_timestamp_delta(dep_time, arr_time)
                            if api_delta:
                                return {"duration": api_delta, "source": "Verified"}
            except Exception:
                pass

        return {"duration": "Flight Duration Unavailable", "source": "Unavailable"}
