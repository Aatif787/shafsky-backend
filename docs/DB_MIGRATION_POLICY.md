# Database Migration Policy

**Status:** adopted 2026-10-10 as part of the backend hardening pass (see
`BACKEND_FIXES_REPORT.md` / `BACKEND_ISSUES_REPORT.md` at the workspace root).

## Rules

1. **Every schema change ships as a hand-written Alembic migration.**
   The migration's DDL must be complete, literal, and frozen: it may never be
   produced dynamically from the current state of `app/models/`.
2. **Never generate migrations from `Base.metadata.create_all`.**
   Dynamic "catch-up" migrations make deployed schemas depend on which model
   code existed at migration time: two environments running the same migration
   history can end up with different DDL, and `downgrade` is meaningless.
3. **Never rewrite an applied migration.** History that has reached any shared
   environment (staging/production) is immutable. Corrections are new
   migrations on top.
4. **Migrations and models must converge.** After a model change:
   - add the hand-written migration,
   - verify `alembic upgrade head` on a fresh database AND on a copy of
     production-shaped data,
   - verify `Base.metadata` and the migrated schema match (e.g. via
     `alembic check` when available, or a schema-diff script in CI).

## Known caveat (do not replicate)

`alembic/versions/f4b5c6d7e8a9_*.py` ("create remaining ORM tables missing
from Alembic history") creates whatever tables are missing from the live
database using `Base.metadata.create_all(bind, tables=missing)`. It is **kept
as-is** because it is already applied to deployed environments and rewriting
history would break them. Consequences to be aware of:

- the exact DDL of the tables it created can differ between environments that
  ran it at different times (model-code drift),
- its `downgrade()` drops those tables — **never run it outside of a fully
  disposable test database**.

New work must not add further migrations of this style.

## Data-safety notes for new migrations

- Adding a unique index (e.g. on `payment_transactions.gateway_payment_id`)
  must be preceded by a deduplication pass; creating the index on a table that
  already contains duplicates fails the deploy. Prefer
  `CREATE UNIQUE INDEX CONCURRENTLY` semantics on Postgres where the migration
  framework allows, or ship the dedup as a separate, reviewed data migration.
- Any DDL on large tables must be reviewed for lock duration before deploy.
