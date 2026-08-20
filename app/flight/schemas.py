from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, computed_field


class FlightAirport(BaseModel):
    code: str = Field(..., description="3-letter IATA code")
    name: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    timezone: Optional[str] = None
    terminal: Optional[str] = None
    gate: Optional[str] = None


class FlightCarrier(BaseModel):
    iata: Optional[str] = None
    name: Optional[str] = None
    icao: Optional[str] = None


class FlightEndpointDetails(BaseModel):
    airport: Optional[FlightAirport] = None
    terminal: Optional[str] = None
    gate: Optional[str] = None
    scheduled_time: Optional[datetime] = None
    estimated_time: Optional[datetime] = None
    actual_time: Optional[datetime] = None
    delay_minutes: Optional[int] = None


class AirlineDetails(BaseModel):
    name: Optional[str] = None
    iata: Optional[str] = None
    icao: Optional[str] = None
    logo: Optional[str] = None


class FlightInfo(BaseModel):
    number: Optional[str] = None
    iata: Optional[str] = None
    icao: Optional[str] = None
    codeshare: Optional[str] = None


class LocationEndpointDetails(BaseModel):
    airport: Optional[str] = Field(default=None, description="3-letter IATA code")
    airport_name: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    terminal: Optional[str] = None
    gate: Optional[str] = None
    scheduled: Optional[str] = None
    estimated: Optional[str] = None
    actual: Optional[str] = None
    delay: Optional[int] = None
    timezone: Optional[str] = None


class DurationDetails(BaseModel):
    minutes: Optional[int] = None
    formatted: Optional[str] = None


class AircraftDetails(BaseModel):
    model: Optional[str] = None
    registration: Optional[str] = None
    icao: Optional[str] = None
    type: Optional[str] = None
    distance: Optional[float] = None


class FlightStatusData(BaseModel):
    """
    Unified Internal Response Schema.
    Strictly provider-driven. All missing fields are null.
    No hardcoding, no fake values, no invented terminals or gates.
    """
    airline: AirlineDetails
    flight: FlightInfo
    departure: LocationEndpointDetails
    arrival: LocationEndpointDetails
    duration: DurationDetails
    aircraft: AircraftDetails
    status: Optional[str] = None

    # Backward compatibility computed fields
    @computed_field
    @property
    def flight_num(self) -> str:
        return self.flight.iata or self.flight.number or ""

    @computed_field
    @property
    def carrier(self) -> FlightCarrier:
        return FlightCarrier(iata=self.airline.iata or "", name=self.airline.name, icao=self.airline.icao)

    @computed_field
    @property
    def origin(self) -> FlightAirport:
        return FlightAirport(
            code=self.departure.airport or "",
            name=self.departure.airport_name,
            city=self.departure.city,
            country=self.departure.country,
            timezone=self.departure.timezone,
            terminal=self.departure.terminal,
            gate=self.departure.gate
        )

    @computed_field
    @property
    def destination(self) -> FlightAirport:
        return FlightAirport(
            code=self.arrival.airport or "",
            name=self.arrival.airport_name,
            city=self.arrival.city,
            country=self.arrival.country,
            timezone=self.arrival.timezone,
            terminal=self.arrival.terminal,
            gate=self.arrival.gate
        )

    @computed_field
    @property
    def scheduled_departure(self) -> Optional[str]:
        return self.departure.scheduled or self.departure.estimated

    @computed_field
    @property
    def scheduled_arrival(self) -> Optional[str]:
        return self.arrival.scheduled or self.arrival.estimated

    @computed_field
    @property
    def duration_minutes(self) -> int:
        return self.duration.minutes or 0

    @computed_field
    @property
    def duration_text(self) -> str:
        return self.duration.formatted or "0m"

    @computed_field
    @property
    def duration_formatted(self) -> str:
        return self.duration.formatted or "0m"

    @computed_field
    @property
    def durationMinutes(self) -> int:
        return self.duration.minutes or 0

    @computed_field
    @property
    def durationFormatted(self) -> str:
        return self.duration.formatted or "0m"

    class Config:
        populate_by_name = True


class FlightTelemetry(BaseModel):
    latitude: float
    longitude: float
    altitude: float
    heading: float
    speed: float


class FlightValidateRequest(BaseModel):
    flight_num: Optional[str] = Field(default=None, alias="flightNum")
    flightNumber: Optional[str] = None
    date: Optional[str] = Field(default=None, alias="departDate")
    departDate: Optional[str] = None
    depart_date: Optional[str] = None
    is_manual: Optional[bool] = Field(default=False, alias="isManual")
    depart_time: Optional[str] = Field(default=None, alias="departTime")
    origin_code: Optional[str] = Field(default=None, alias="originCode")
    destination_code: Optional[str] = Field(default=None, alias="destinationCode")
    airline_name: Optional[str] = Field(default=None, alias="airlineName")
    arrival_date: Optional[str] = Field(default=None, alias="arrivalDate")
    trip_type: Optional[str] = Field(default=None, alias="tripType")
    direction: Optional[str] = None
    service_type: Optional[str] = Field(default=None, alias="serviceType")
    airport_code: Optional[str] = Field(default=None, alias="airportCode")
    mode: Optional[str] = None

    @property
    def resolved_flight_num(self) -> str:
        return self.flight_num or self.flightNumber or ""

    @property
    def resolved_date(self) -> str:
        return self.date or self.departDate or self.depart_date or ""

    @property
    def resolved_direction(self) -> str:
        return self.direction or self.trip_type or self.service_type or self.mode or "any"

    class Config:
        populate_by_name = True


class FlightValidateResponseData(BaseModel):
    valid: bool
    flight_data: Optional[FlightStatusData] = Field(default=None, alias="flightData")

    class Config:
        populate_by_name = True


class FlightValidateResponse(BaseModel):
    success: bool
    data: Optional[FlightValidateResponseData] = None
    error: Optional[str] = None
    code: Optional[str] = None
