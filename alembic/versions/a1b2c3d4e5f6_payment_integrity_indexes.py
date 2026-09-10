"""Payment integrity indexes for live gateway IDs and unpaid invoice PDF ops.

Revision ID: a1b2c3d4e5f6
Revises: 9c0d1e2f3a4b
Create Date: 2026-08-26 17:20:00.000000

"""
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "9c0d1e2f3a4b"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade():
    tables = _tables()

    # Payment tables historically created via SQLAlchemy create_all — ensure on fresh DBs.
    if "payment_transactions" not in tables:
        op.execute(
            """
            CREATE TABLE payment_transactions (
                id UUID PRIMARY KEY,
                transaction_ref VARCHAR(50) NOT NULL UNIQUE,
                entity_type VARCHAR(50) NOT NULL,
                entity_id VARCHAR(100) NOT NULL,
                customer_id VARCHAR(100),
                amount DOUBLE PRECISION NOT NULL,
                currency VARCHAR(10) NOT NULL DEFAULT 'INR',
                payment_method VARCHAR(50) NOT NULL DEFAULT 'CREDIT_CARD',
                status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
                gateway_provider VARCHAR(50) NOT NULL DEFAULT 'MOCK_PAYMENT',
                gateway_payment_id VARCHAR(100),
                gateway_signature VARCHAR(255),
                gateway_response JSON,
                is_duplicate BOOLEAN NOT NULL DEFAULT FALSE,
                notes TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_payment_transactions_transaction_ref ON payment_transactions (transaction_ref)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_payment_transactions_entity_type ON payment_transactions (entity_type)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_payment_transactions_entity_id ON payment_transactions (entity_id)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_payment_transactions_customer_id ON payment_transactions (customer_id)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_payment_transactions_status ON payment_transactions (status)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_payment_transactions_gateway_payment_id ON payment_transactions (gateway_payment_id)")

    if "invoices" not in tables:
        op.execute(
            """
            CREATE TABLE invoices (
                id UUID PRIMARY KEY,
                invoice_number VARCHAR(50) NOT NULL UNIQUE,
                transaction_id UUID NOT NULL REFERENCES payment_transactions(id) ON DELETE CASCADE,
                customer_name VARCHAR(255) NOT NULL,
                customer_email VARCHAR(255) NOT NULL,
                customer_tax_id VARCHAR(50),
                subtotal_amount DOUBLE PRECISION NOT NULL,
                tax_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                total_amount DOUBLE PRECISION NOT NULL,
                currency VARCHAR(10) NOT NULL DEFAULT 'INR',
                status VARCHAR(50) NOT NULL DEFAULT 'ISSUED',
                pdf_url VARCHAR(500),
                issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                paid_at TIMESTAMPTZ
            )
            """
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_invoices_invoice_number ON invoices (invoice_number)")

    if "refunds" not in tables:
        op.execute(
            """
            CREATE TABLE refunds (
                id UUID PRIMARY KEY,
                refund_ref VARCHAR(50) NOT NULL UNIQUE,
                transaction_id UUID NOT NULL REFERENCES payment_transactions(id) ON DELETE CASCADE,
                amount DOUBLE PRECISION NOT NULL,
                currency VARCHAR(10) NOT NULL DEFAULT 'INR',
                reason TEXT,
                status VARCHAR(50) NOT NULL DEFAULT 'PROCESSING',
                gateway_refund_id VARCHAR(100),
                processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_refunds_refund_ref ON refunds (refund_ref)")

    if "payment_webhook_events" not in tables:
        op.execute(
            """
            CREATE TABLE payment_webhook_events (
                id UUID PRIMARY KEY,
                event_id VARCHAR(255) NOT NULL UNIQUE,
                event_type VARCHAR(100) NOT NULL,
                gateway_provider VARCHAR(50) NOT NULL DEFAULT 'RAZORPAY',
                payload JSON,
                status VARCHAR(50) NOT NULL DEFAULT 'PROCESSED',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_payment_webhook_events_event_id ON payment_webhook_events (event_id)")

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
