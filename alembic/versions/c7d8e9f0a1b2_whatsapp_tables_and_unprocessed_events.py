"""WhatsApp conversation tables and unprocessed webhook events.

Revision ID: c7d8e9f0a1b2
Revises: b3c4d5e6f7a8
Create Date: 2026-08-29 00:30:00.000000

Tables were previously created via SQLAlchemy create_all on import.
This migration is additive and safe on databases that already have them.
"""
from alembic import op


revision = "c7d8e9f0a1b2"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_conversations (
            id UUID PRIMARY KEY,
            phone_number VARCHAR(50) NOT NULL UNIQUE,
            current_state VARCHAR(50) NOT NULL DEFAULT 'START',
            selected_category VARCHAR(100),
            selected_service_id VARCHAR(100),
            selected_service_name VARCHAR(150),
            requires_airport BOOLEAN NOT NULL DEFAULT TRUE,
            requires_flight BOOLEAN NOT NULL DEFAULT TRUE,
            requires_date BOOLEAN NOT NULL DEFAULT TRUE,
            requires_passenger_count BOOLEAN NOT NULL DEFAULT TRUE,
            selected_airport_iata VARCHAR(10),
            selected_airport_name VARCHAR(255),
            selected_airport_city VARCHAR(100),
            selected_airport_country VARCHAR(100),
            flight_num VARCHAR(50),
            flight_details_json JSON,
            booking_date VARCHAR(50),
            passenger_count INTEGER NOT NULL DEFAULT 1,
            customer_name VARCHAR(150),
            customer_email VARCHAR(255),
            customer_phone VARCHAR(50),
            additional_requirements TEXT,
            total_amount NUMERIC(10, 2),
            currency VARCHAR(3) NOT NULL DEFAULT 'INR',
            booking_id UUID,
            booking_ref VARCHAR(50),
            razorpay_order_id VARCHAR(100),
            razorpay_payment_id VARCHAR(100),
            razorpay_payment_link_id VARCHAR(100),
            razorpay_payment_url TEXT,
            payment_status VARCHAR(50) NOT NULL DEFAULT 'UNPAID',
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_whatsapp_conversations_phone ON whatsapp_conversations (phone_number)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_whatsapp_conversations_state ON whatsapp_conversations (current_state)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_whatsapp_conversations_ref ON whatsapp_conversations (booking_ref)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_whatsapp_conversations_rzp_order ON whatsapp_conversations (razorpay_order_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_messages (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES whatsapp_conversations(id) ON DELETE CASCADE,
            message_id VARCHAR(100),
            direction VARCHAR(20) NOT NULL DEFAULT 'INBOUND',
            message_type VARCHAR(50) NOT NULL DEFAULT 'text',
            content TEXT,
            raw_payload JSON,
            created_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_conv ON whatsapp_messages (conversation_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_whatsapp_messages_meta_id ON whatsapp_messages (message_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_webhook_events (
            id UUID PRIMARY KEY,
            event_id VARCHAR(255) NOT NULL UNIQUE,
            event_type VARCHAR(100) NOT NULL DEFAULT 'message',
            payload JSON,
            processed BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_whatsapp_webhook_events_eid ON whatsapp_webhook_events (event_id)"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_webhook_events' AND column_name = 'processed'
            ) THEN
                ALTER TABLE whatsapp_webhook_events ALTER COLUMN processed SET DEFAULT false;
            END IF;
        END $$;
        """
    )


def downgrade():
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_webhook_events' AND column_name = 'processed'
            ) THEN
                ALTER TABLE whatsapp_webhook_events ALTER COLUMN processed SET DEFAULT true;
            END IF;
        END $$;
        """
    )
