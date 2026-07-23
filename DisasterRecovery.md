# Disaster Recovery & Business Continuity Specification - Shafsky Aviation

This document details the Disaster Recovery (DR) topology, backup strategy, and business continuity mechanisms for the **Shafsky Aviation** platform.

---

## 1. RPO & RTO Targets

| System Component | Target RPO | Target RTO | Backup Method |
| :--- | :---: | :---: | :--- |
| **Booking Engine** | **$\le$ 5 minutes** | **$\le$ 15 minutes** | Neon Continuous WAL Archiving / PITR |
| **Authentication & Users** | **$\le$ 15 minutes** | **$\le$ 30 minutes** | AES-256 Encrypted Daily Snapshots |
| **CRM & Analytics** | **$\le$ 30 minutes** | **$\le$ 45 minutes** | Encrypted S3 Multi-Region Dump |

---

## 2. Backup & Verification Strategy

- **Encrypted Database Dumps**: Generated with AES-256 encryption.
- **Integrity Validation**: SHA-256 checksum stored in metadata (`.meta.json`). Verified before any restore action.
- **Point-In-Time Recovery (PITR)**: Supported by Neon PostgreSQL WAL archiving, allowing state restoration to any second.

---

## 3. Business Continuity & Circuit Breakers

- **Flight Provider Outage**: Circuit breaker falls back to local `FlightStatusRecord` TTL cache. Booking creation succeeds with warning.
- **Notification Provider Outage**: Enqueues dispatches in `NotificationRecord` database table. Background worker retries delivery every 60 seconds.
- **Payment Provider Outage**: Switches booking status to `PENDING_PAYMENT` without dropping booking.
