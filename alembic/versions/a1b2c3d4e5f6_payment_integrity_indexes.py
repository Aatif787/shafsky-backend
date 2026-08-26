"""Payment integrity indexes for live gateway IDs and unpaid invoice PDF ops.

Revision ID: a1b2c3d4e5f6
Revises: 9c0d1e2f3a4b
Create Date: 2026-08-26 17:20:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "9c0d1e2f3a4b"
branch_labels = None
depends_on = None


def upgrade():
    # Prevent duplicate live Razorpay payment IDs (excludes known test/mock prefixes).
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_tx_live_gateway_payment_id
        ON payment_transactions (gateway_payment_id)
        WHERE gateway_payment_id IS NOT NULL
          AND btrim(gateway_payment_id) <> ''
          AND gateway_payment_id !~* '^(pay_first_|pay_second_|pay_mock_|pay_retry_|pay_over_|pay_ref_|GW-|order_initial|plink_)'
        """
    )
    # Speeds up invoice PDF backfill / ops queries for PAID rows missing storage path.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_invoices_paid_missing_pdf
        ON invoices (paid_at DESC NULLS LAST)
        WHERE pdf_url IS NULL AND status = 'PAID'
        """
    )
    # Helpful for soft-link lookups booking_ref <-> payment.entity_id
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_payment_transactions_entity_id_status
        ON payment_transactions (entity_id, status)
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_payment_transactions_entity_id_status")
    op.execute("DROP INDEX IF EXISTS ix_invoices_paid_missing_pdf")
    op.execute("DROP INDEX IF EXISTS uq_payment_tx_live_gateway_payment_id")
