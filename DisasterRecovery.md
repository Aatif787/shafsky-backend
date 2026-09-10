# Disaster Recovery & Business Continuity — Shafsky Aviation

This runbook describes the current AWS production recovery design. Recovery
targets are objectives, not guarantees, until restore drills measure them.

## Source of truth

- PostgreSQL recovery uses **Amazon RDS automated backups and point-in-time
  recovery (PITR)**. Configure retention, encryption, Multi-AZ, and deletion
  protection in AWS before launch.
- The application endpoint `/api/admin/dr/backup` creates **drill metadata
  only**. It is not a database dump, backup, or restore point.
- No application code currently creates AES-encrypted dumps or uploads database
  backups to S3. Do not rely on those mechanisms.

## Required production controls

1. Enable RDS automated backups and PITR with a retention period approved by
   operations.
2. Encrypt RDS, snapshots, and replicas with KMS.
3. Restrict snapshot restore and deletion permissions to dedicated operators.
4. Take a final snapshot before destructive migrations or major releases.
5. Run and document a restore drill at least quarterly in an isolated account
   or VPC.
6. Record the measured recovery point and recovery time from each drill.

## Restore procedure

1. Stop write traffic or place the API in maintenance mode.
2. Select the required RDS restore timestamp and restore into a new instance.
3. Validate schema revision with `alembic current`.
4. Run read-only integrity checks for users, bookings, payments, and invoices.
5. Point a staging task at the restored database and run smoke tests.
6. Update the production secret/endpoint only after approval.
7. Resume traffic, verify `/ready`, and monitor payment reconciliation.

## Business continuity

- Flight provider failure may fall back to cached flight data where available.
- Notification failures remain recorded for retry and must not roll back paid
  bookings.
- Payment provider failure leaves a booking pending; verified reconciliation
  must run before an operator manually confirms payment.
