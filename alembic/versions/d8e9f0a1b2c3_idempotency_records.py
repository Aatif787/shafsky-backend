"""Shared idempotency records for HTTP POST replay when Redis is down.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-08-29 00:45:00.000000
"""
from alembic import op


revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency_records (
            idempotency_key VARCHAR(256) PRIMARY KEY,
            fingerprint VARCHAR(64),
            status_code INTEGER,
            headers JSON,
            body TEXT,
            lock_token VARCHAR(64),
            lock_expires_at TIMESTAMPTZ,
            response_expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_idempotency_records_response_expires
        ON idempotency_records (response_expires_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_idempotency_records_lock_expires
        ON idempotency_records (lock_expires_at)
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS idempotency_records")
