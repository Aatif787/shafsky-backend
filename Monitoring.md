# Enterprise Observability & Monitoring Specification - Shafsky Aviation

This document details the observability, metrics, logging, and health monitoring infrastructure for the **Shafsky Aviation** platform.

---

## 1. Structured JSON Logging Format

Every application log emits in standardized JSON format:

```json
{
  "timestamp": "2026-07-23T16:35:00.123456Z",
  "request_id": "req_88f9a2b1c4e7",
  "correlation_id": "corr_3f9a2c1b7d5e",
  "trace_id": "trace_9918273645a1",
  "service_name": "Shafsky Aviation FastAPI Backend Engine",
  "environment": "production",
  "hostname": "shafsky-node-01",
  "user_id": "usr_99887766-5544-3322-1100-a1b2c3d4e5f6",
  "booking_id": "SHF-20260723-9988",
  "endpoint": "/api/bookings",
  "http_method": "POST",
  "status_code": 200,
  "response_time_ms": 42.15,
  "client_ip": "103.22.180.4",
  "log_level": "INFO",
  "message": "Booking successfully created and notification enqueued"
}
```

**Sensitive Data Redactor**: Automatically replaces passwords, JWT tokens, API keys, and authorization headers with `[REDACTED]`.

---

## 2. Distributed Correlation ID Tracing

- Requests accept incoming `X-Correlation-ID` headers or generate a new unique correlation ID (`corr_...`).
- Propagated across context variables (`contextvars`) and injected into all response headers (`X-Correlation-ID`, `X-Request-ID`, `X-Response-Time-Ms`).

---

## 3. Production Health Check Endpoints

- `GET /health` (`< 20ms` SLA): Deep health check evaluating Neon PostgreSQL DB, Redis, Flight provider, Notification provider, Memory, and Disk space.
- `GET /ready` (`< 10ms` SLA): Application readiness check.
- `GET /live` (`< 5ms` SLA): Liveness check.
- `GET /metrics`: Prometheus formatted metrics (`http_requests_total`, `http_request_duration_seconds_sum`, `bookings_created_total`, `revenue_generated_total`).
- `GET /api/admin/observability/dashboard`: SRE operational telemetry dashboard.
