# Shafsky Aviation — REST API Backend Engine (`shafsky-backend`)

Enterprise Node.js/TypeScript REST API backend microservice providing business logic, flight duration resolution, booking eligibility validation, notification dispatching, and security middleware.

---

## 📁 Repository Structure

```
shafsky-backend/
├── src/
│   ├── services/
│   │   └── flight/
│   │       ├── providers/       # Amadeus, AeroDataBox, AviationStack Adapters
│   │       ├── FlightDurationResolver.ts # Single Source Duration Resolver
│   │       └── FlightTimeUtils.ts       # Timezone & 6h Cutoff Utilities
│   └── server.ts                # Express REST API Server
├── tsconfig.json                # TypeScript Engine Configuration
└── package.json                 # Backend Microservice Manifest
```

---

## 🚀 API Endpoints

- `GET /health` — Microservice Health Check
- `POST /api/flight/duration` — Multi-Provider Flight Duration Resolver
- `POST /api/flight/validate` — 6-Hour Advance Notice & Past Departure Eligibility Validator

---

## 🛠️ Local Execution

```bash
# Install dependencies
pnpm install

# Start Dev Watcher
pnpm dev

# Build Production Output
pnpm build

# Start Production Microservice
pnpm start
```
