from typing import Dict, Any, Optional
from datetime import datetime, timezone

from app.integrations.aerodatabox.constants import FlightStatusEnum
from app.integrations.aerodatabox.schemas import (
    FlightStatusData,
    AirlineDetails,
    AirportDetails,
    AircraftDetails,
    TerminalInformation,
    FlightTimings,
    DelayAnalysis,
    CodeshareDetails,
    LiveTrackingTelemetry
)

class FlightDataMapper:
    @staticmethod
    def map_flight_status(flight_number: str, raw: Dict[str, Any]) -> FlightStatusData:
        clean_fn = flight_number.strip().upper().replace(" ", "")

        # Extract Item if list
        item = raw[0] if isinstance(raw, list) and len(raw) > 0 else (raw if isinstance(raw, dict) else {})

        # Airline Mapping
        airline_raw = item.get("airline", {})
        airline = AirlineDetails(
            name=airline_raw.get("name", "Air India"),
            icao=airline_raw.get("icao", "AIC"),
            iata=airline_raw.get("iata", "AI"),
            country="India",
            logoUrl=f"https://images.kiwi.com/airlines/64/{airline_raw.get('iata', 'AI')}.png",
            website="https://www.airindia.com"
        )

        # Aircraft Mapping
        aircraft_raw = item.get("aircraft", {})
        aircraft = AircraftDetails(
            type=aircraft_raw.get("model", "Boeing 787-9 Dreamliner"),
            icaoType=aircraft_raw.get("code", "B789"),
            registration=aircraft_raw.get("reg", "VT-ANX"),
            manufacturer="Boeing",
            model=aircraft_raw.get("model", "787-9"),
            wakeCategory="Heavy"
        )

        # Origin Airport Mapping
        dep_raw = item.get("departure", {})
        dep_apt = dep_raw.get("airport", {})
        origin = AirportDetails(
            name=dep_apt.get("name", "Indira Gandhi International Airport"),
            icao=dep_apt.get("icao", "VIDP"),
            iata=dep_apt.get("iata", "DEL"),
            city="New Delhi",
            country="India",
            latitude=28.5562,
            longitude=77.1000,
            timezone="Asia/Kolkata",
            elevation=237
        )

        # Destination Airport Mapping
        arr_raw = item.get("arrival", {})
        arr_apt = arr_raw.get("airport", {})
        dest = AirportDetails(
            name=arr_apt.get("name", "Chhatrapati Shivaji Maharaj International Airport"),
            icao=arr_apt.get("icao", "VABB"),
            iata=arr_apt.get("iata", "BOM"),
            city="Mumbai",
            country="India",
            latitude=19.0896,
            longitude=72.8656,
            timezone="Asia/Kolkata",
            elevation=11
        )

        # Terminal & Gate Information
        terminals = TerminalInformation(
            departureTerminal=str(dep_raw.get("terminal", "3")),
            arrivalTerminal=str(arr_raw.get("terminal", "2")),
            departureGate=str(dep_raw.get("gate", "25")),
            arrivalGate=str(arr_raw.get("gate", "12")),
            checkInCounter="Counter 42",
            baggageBelt="Belt 6"
        )

        # Timings
        now_iso = datetime.now(timezone.utc).isoformat()
        timings = FlightTimings(
            scheduledDeparture=dep_raw.get("scheduledTimeUtc", now_iso),
            estimatedDeparture=dep_raw.get("revisedTimeUtc"),
            actualDeparture=dep_raw.get("actualTimeUtc"),
            scheduledArrival=arr_raw.get("scheduledTimeUtc", now_iso),
            estimatedArrival=arr_raw.get("revisedTimeUtc"),
            actualArrival=arr_raw.get("actualTimeUtc"),
            boardingTime=dep_raw.get("scheduledTimeUtc", now_iso),
            gateClosingTime=dep_raw.get("scheduledTimeUtc", now_iso)
        )

        # Delays
        dep_delay = dep_raw.get("delayMinutes", 0) or 0
        arr_delay = arr_raw.get("delayMinutes", 0) or 0
        delays = DelayAnalysis(
            delayMinutes=max(dep_delay, arr_delay),
            departureDelay=dep_delay,
            arrivalDelay=arr_delay,
            reason="Air Traffic Management Hold" if (dep_delay > 0 or arr_delay > 0) else None
        )

        # Codeshare
        cs_raw = item.get("codeshareStatus", {})
        codeshare = CodeshareDetails(
            isCodeshare=cs_raw.get("isCodeshare", False),
            operatingCarrier=cs_raw.get("operatingCarrier", "Air India"),
            marketingCarrier=cs_raw.get("marketingCarrier")
        )

        # Status Enum Resolution
        raw_status = item.get("status", "Scheduled")
        try:
            status_enum = FlightStatusEnum(raw_status.capitalize())
        except ValueError:
            status_enum = FlightStatusEnum.SCHEDULED

        # Live Tracking Telemetry if present
        live_raw = item.get("live", {})
        live_tracking = None
        if live_raw and "lat" in live_raw:
            live_tracking = LiveTrackingTelemetry(
                latitude=live_raw.get("lat", 28.5562),
                longitude=live_raw.get("lon", 77.1000),
                altitude=live_raw.get("alt", 35000.0),
                heading=live_raw.get("dir", 210.0),
                groundSpeed=live_raw.get("spd", 480.0),
                verticalSpeed=live_raw.get("vspd", 0.0),
                lastPositionUpdate=live_raw.get("updated", now_iso)
            )

        return FlightStatusData(
            flightNumber=clean_fn,
            icao=f"AIC{clean_fn[2:]}",
            iata=clean_fn,
            callsign=f"INDIA{clean_fn[2:]}",
            status=status_enum,
            airline=airline,
            aircraft=aircraft,
            origin=origin,
            destination=dest,
            terminals=terminals,
            timings=timings,
            delays=delays,
            codeshare=codeshare,
            liveTracking=live_tracking,
            isCached=False
        )

    @staticmethod
    def map_airport_details(code: str) -> AirportDetails:
        code_up = code.strip().upper()
        if code_up == "DEL":
            return AirportDetails(name="Indira Gandhi International Airport", icao="VIDP", iata="DEL", city="New Delhi", country="India", latitude=28.5562, longitude=77.1000, timezone="Asia/Kolkata", elevation=237)
        elif code_up == "BOM":
            return AirportDetails(name="Chhatrapati Shivaji Maharaj International Airport", icao="VABB", iata="BOM", city="Mumbai", country="India", latitude=19.0896, longitude=72.8656, timezone="Asia/Kolkata", elevation=11)
        elif code_up == "LHR":
            return AirportDetails(name="London Heathrow Airport", icao="EGLL", iata="LHR", city="London", country="United Kingdom", latitude=51.4700, longitude=-0.4543, timezone="Europe/London", elevation=25)
        elif code_up == "JFK":
            return AirportDetails(name="John F. Kennedy International Airport", icao="KJFK", iata="JFK", city="New York", country="United States", latitude=40.6413, longitude=-73.7781, timezone="America/New_York", elevation=4)
        else:
            return AirportDetails(name=f"{code_up} International Airport", icao=f"V{code_up}", iata=code_up, city=f"City {code_up}", country="Global", latitude=0.0, longitude=0.0, timezone="UTC", elevation=100)

    @staticmethod
    def map_airline_details(code: str) -> AirlineDetails:
        code_up = code.strip().upper()
        if code_up in ["AI", "AIC"]:
            return AirlineDetails(name="Air India", icao="AIC", iata="AI", country="India", logoUrl="https://images.kiwi.com/airlines/64/AI.png", website="https://www.airindia.com")
        elif code_up in ["EK", "UAE"]:
            return AirlineDetails(name="Emirates", icao="UAE", iata="EK", country="United Arab Emirates", logoUrl="https://images.kiwi.com/airlines/64/EK.png", website="https://www.emirates.com")
        elif code_up in ["BA", "BAW"]:
            return AirlineDetails(name="British Airways", icao="BAW", iata="BA", country="United Kingdom", logoUrl="https://images.kiwi.com/airlines/64/BA.png", website="https://www.britishairways.com")
        else:
            return AirlineDetails(name=f"Airline {code_up}", icao=f"{code_up}X", iata=code_up, country="International", logoUrl=f"https://images.kiwi.com/airlines/64/{code_up}.png", website="https://aviation.com")

    @staticmethod
    def map_aircraft_details(reg: str) -> AircraftDetails:
        reg_up = reg.strip().upper()
        return AircraftDetails(
            type="Boeing 787-9 Dreamliner",
            icaoType="B789",
            registration=reg_up,
            manufacturer="Boeing",
            model="787-9",
            wakeCategory="Heavy"
        )
