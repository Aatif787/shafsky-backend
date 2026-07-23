# Enterprise Platform Architecture - Shafsky Aviation

This document details the production backend architecture for the **Shafsky Aviation Concierge Platform**.

---

## 1. High-Level Architecture Overview

```mermaid
graph TD
    Frontend["React + TanStack Frozen Production Frontend"] -->|REST / HTTPS| Gateway["FastAPI Core Engine (app/main.py)"]
    
    subgraph Middleware Stack
        Gateway --> ObsMW["ObservabilityMiddleware (Tracing & Response Timing)"]
        Gateway --> SecMW["SecurityMiddleware (OWASP Headers & Rate Limits)"]
    end

    subgraph Service Modules
        Gateway --> Auth["Auth Service (app/services/auth_service.py)"]
        Gateway --> Booking["Booking Service (app/services/booking_service.py)"]
        Gateway --> CRM["CRM Service (app/services/crm_service.py)"]
        Gateway --> Flight["Flight Service (app/services/flight_service.py)"]
        Gateway --> Notification["Notification Hub (app/services/notification_service.py)"]
        Gateway --> Admin["Admin Service (app/services/admin_service.py)"]
        Gateway --> DR["Disaster Recovery Engine (app/disaster_recovery/)"]
    end

    subgraph Infrastructure & Persistence
        Auth & Booking & CRM & Notification --> NeonDB[("Neon PostgreSQL DB (Alembic 994f8199e8c5)")]
        Flight --> FlightCache[("Flight Status TTL Cache")]
        Notification --> ResendAPI["Resend Email & Meta WhatsApp API"]
        DR --> EncryptedStorage[("AES-256 Encrypted Backups / S3 Sync")]
    end
```

---

## 2. Core Principles & Stack Standard

- **Framework**: FastAPI (Python 3.13)
- **Database**: Neon PostgreSQL with SQLAlchemy 2.x ORM & Alembic Migrations
- **Security**: PyJWT with SHA-256 Hashed Refresh Token Rotation, Device Fingerprinting, and 10-tier RBAC Matrix
- **Observability**: Structured JSON Logging, `contextvars`-backed Request Correlation Tracing (`X-Correlation-ID`), Prometheus Metrics Exporter (`/metrics`), Deep Health Engine (`/health`, `/ready`, `/live`)
- **Disaster Recovery**: Neon Point-In-Time Recovery (PITR), AES-256 Encrypted Backups, SHA-256 Checksum Validation, and Graceful Degradation Circuit Breakers
