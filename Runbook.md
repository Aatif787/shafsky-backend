# SRE Incident Response Runbook & Procedures - Shafsky Aviation

This runbook provides step-by-step procedures for Site Reliability Engineers (SRE) and operations teams during incidents.

---

## 1. Database Outage or Corruption Procedure

1. **Diagnosis**: Check `/health` endpoint or alert `DatabaseConnectivityFailure`.
2. **Neon PITR Recovery**:
   - Access Neon Management Console.
   - Navigate to **Point-In-Time Recovery**.
   - Select a restore timestamp prior to the corruption incident.
3. **Connection String Update**: Update `DATABASE_URL` in `.env` if point-in-time branch was spawned.
4. **Verification**: Run `POST /api/admin/dr/restore-verify` to validate database integrity.

---

## 2. External Flight API Outage Procedure

1. **Trigger**: Third-party AeroDataBox API timeout or HTTP 5xx errors.
2. **Automated Mitigation**: Circuit breaker engages automatically. System serves cached flight status from `FlightStatusRecord`.
3. **Manual Simulation**: `POST /api/admin/dr/simulate-incident?scenario=FLIGHT_API_OUTAGE`.

---

## 3. Notification Gateway Outage Procedure

1. **Trigger**: Resend Email / WhatsApp Meta Cloud API timeout.
2. **Automated Mitigation**: Payloads buffered in `NotificationRecord` with `PENDING` state.
3. **Recovery Verification**: Background task automatically flushes queue once provider recovers.
