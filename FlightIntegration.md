# Flight Integration & Booking Engine Workflow

## Pre-Booking Flight Validation & Auto-Fill

Before a booking is created in `BookingService.create_booking`:

1. **Flight Validation**: The requested flight number and travel date are checked via `FlightIntelligenceService.validate_flight(flight_number, date)`.
2. **Rejection Policy**: If the flight is invalid, the backend immediately responds with `HTTP 400 Bad Request` and `{"code": "INVALID_FLIGHT", "message": "Flight not found."}`.
3. **Auto-Fill Data Mapping**:
   - `flight_num` $\rightarrow$ Validated IATA flight number
   - `origin_code` $\rightarrow$ Origin airport IATA code (e.g. `DEL`)
   - `dest_code` $\rightarrow$ Destination airport IATA code (e.g. `BOM`)
   - `departure_time` $\rightarrow$ Scheduled departure timestamp
   - `arrival_time` $\rightarrow$ Scheduled arrival timestamp
   - `terminal` $\rightarrow$ Departure terminal
   - `gate` $\rightarrow$ Departure gate
   - `airline` $\rightarrow$ Airline name & IATA code
   - `aircraft` $\rightarrow$ Aircraft type & registration

## Background Status Refresh & Event Dispatching

- Active flight bookings are registered for background status polling.
- If a flight status changes (e.g., **Gate Change**, **Delay > 15m**, **Cancellation**, **Boarding**, **Terminal Change**), an internal event is dispatched to the **Notification Hub** without coupling business logic to external providers.
