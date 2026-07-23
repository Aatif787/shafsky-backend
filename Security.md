# Cloud Security & OWASP Hardening Specification - Shafsky Aviation

This document details the security model and protection mechanisms implemented across the **Shafsky Aviation** enterprise platform.

---

## 1. Authentication & Hashed Refresh Token Rotation

- **Access Tokens**: Short-lived (15 minutes) PyJWT tokens containing `sub`, `user_id`, `role`, and expiration `exp`.
- **Refresh Tokens**: Long-lived (30 days) unique random tokens (`rt_...`).
- **Cryptographic Hashing**: Raw refresh tokens are **NEVER** stored in Neon PostgreSQL; only SHA-256 hashes (`token_hash`) are persisted.
- **Replay Attack Isolation**: If a revoked or reused refresh token is presented, the platform automatically revokes **ALL** active device sessions for that user.

---

## 2. Device Fingerprinting & Session Management

The platform extracts client device metadata on every authentication attempt:
- `device_id`: Deterministic fingerprint or custom header (`X-Device-ID`).
- `browser`: Parsed User-Agent (`Chrome`, `Safari`, `Firefox`, `Edge`).
- `platform`: Parsed OS (`Windows`, `macOS`, `iOS`, `Android`, `Linux`).
- `ip_address`: Extracted from `X-Forwarded-For` or client host.
- `last_activity`: Updated on every token rotation.

---

## 3. Role-Based Access Control (RBAC) Matrix

Access control enforces a **deny-by-default** policy across 10 granular roles:
1. `SUPER_ADMIN` (Full Platform Access)
2. `ADMIN` (Operational & User Administration)
3. `FINANCE` (Payment & Revenue Analytics)
4. `CRM` (Customer Profile & Activity Timeline Management)
5. `OPERATIONS_MANAGER` (Shift & Roster Control)
6. `DUTY_OFFICER` (Airport Ground Operations)
7. `MEET_AND_ASSIST_STAFF` (Passenger Concierge Greeting)
8. `DRIVER` (Chauffeur Transfers)
9. `CONCIERGE_TEAM` (Special Requests & Lounge Reservations)
10. `CUSTOMER` (Passenger Self-Service)

---

## 4. OWASP Security Headers & Rate Limiting

The `SecurityMiddleware` enforces the following headers on every response:
- `Strict-Transport-Security`: `max-age=31536000; includeSubDomains; preload`
- `X-Content-Type-Options`: `nosniff`
- `X-Frame-Options`: `DENY`
- `Referrer-Policy`: `strict-origin-when-cross-origin`
- `Permissions-Policy`: `camera=(), microphone=(), geolocation=()`
- `Content-Security-Policy`: `default-src 'self'; img-src 'self' data: https:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';`

**Rate Limiting**: Protects sensitive endpoints against brute-force attacks (`10 req/min` on auth login, `200 req/min` on general APIs).
