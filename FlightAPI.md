# Enterprise Flight Intelligence API Documentation

The **Shafsky Aviation Flight Intelligence API** provides production-grade endpoints for validating flights, retrieving live status, obtaining airport, airline, and aircraft metadata, performing multi-parameter searches, and fetching real-time flight tracking telemetry.

---

## Base Path: `/api/flights`

### 1. Validate Flight
- **Endpoint**: `POST /api/flights/validate`
- **Description**: Validates flight existence and auto-fills metadata for booking integration.
- **Request Body**:
  ```json
  {
    "flightNumber": "AI101",
    "date": "2026-07-24"
  }
  ```
- **Response Payload**:
  ```json
  {
    "success": true,
    "data": {
      "valid": true,
      "flightNumber": "AI101",
      "airline": "Air India",
      "origin": "DEL",
      "destination": "BOM",
      "departureTime": "2026-07-24T06:00:00Z",
      "arrivalTime": "2026-07-24T08:15:00Z",
      "status": "Scheduled",
      "terminal": "3",
      "gate": "25",
      "aircraft": "Boeing 787-9 Dreamliner",
      "isCached": false
    },
    "error": null
  }
  ```

---

### 2. Live Flight Status
- **Endpoint**: `GET /api/flights/status/{flightNumber}?date=2026-07-24`
- **Description**: Fetches normalized status, timings, delay analysis, terminal/gate, and codeshare info.

---

### 3. Airline Metadata
- **Endpoint**: `GET /api/flights/airline/{iata}`
- **Description**: Fetches airline details, logo URL, and country.
- **Example**: `GET /api/flights/airline/AI`

---

### 4. Airport Metadata
- **Endpoint**: `GET /api/flights/airport/{iata}`
- **Description**: Fetches airport operational details, ICAO, city, country, coordinates, timezone, elevation.
- **Example**: `GET /api/flights/airport/DEL`

---

### 5. Aircraft Metadata
- **Endpoint**: `GET /api/flights/aircraft/{registration}`
- **Description**: Fetches aircraft type, ICAO type, wake category, manufacturer, and model.
- **Example**: `GET /api/flights/aircraft/VT-ANX`

---

### 6. Search Flights
- **Endpoint**: `GET /api/flights/search?query=AI101`
- **Description**: Searches flights by number, route, callsign, or airline.

---

### 7. Live Flight Tracking Telemetry
- **Endpoint**: `GET /api/flights/live/{flightNumber}`
- **Description**: Returns live telemetry coordinates (latitude, longitude, altitude, heading, ground speed).
