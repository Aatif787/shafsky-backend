"""WhatsApp reliability hardening: webhook event lifecycle and conversation activity fields.

Revision ID: e1f2a3b4c5d6
Revises: d8e9f0a1b2c3
Create Date: 2026-08-29 01:00:00.000000

Adds atomic claim lifecycle fields to whatsapp_webhook_events for exactly-one-worker
processing, crash recovery, and observability. Adds session activity tracking and
UI state separation fields to whatsapp_conversations.
"""
from alembic import op


revision = "e1f2a3b4c5d6"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade():
    # --- WhatsApp Webhook Events: atomic claim lifecycle fields ---
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_webhook_events' AND column_name = 'processing_started_at'
            ) THEN
                ALTER TABLE whatsapp_webhook_events ADD COLUMN processing_started_at TIMESTAMPTZ;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_webhook_events' AND column_name = 'processed_at'
            ) THEN
                ALTER TABLE whatsapp_webhook_events ADD COLUMN processed_at TIMESTAMPTZ;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_webhook_events' AND column_name = 'processing_worker_id'
            ) THEN
                ALTER TABLE whatsapp_webhook_events ADD COLUMN processing_worker_id VARCHAR(100);
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_webhook_events' AND column_name = 'error_message'
            ) THEN
                ALTER TABLE whatsapp_webhook_events ADD COLUMN error_message TEXT;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_webhook_events' AND column_name = 'attempt_count'
            ) THEN
                ALTER TABLE whatsapp_webhook_events ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
            END IF;
        END $$;
        """
    )

    # --- WhatsApp Conversations: session activity + UI state separation ---
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_conversations' AND column_name = 'last_user_activity_at'
            ) THEN
                ALTER TABLE whatsapp_conversations ADD COLUMN last_user_activity_at TIMESTAMPTZ;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_conversations' AND column_name = 'whatsapp_state_json'
            ) THEN
                ALTER TABLE whatsapp_conversations ADD COLUMN whatsapp_state_json JSON;
            END IF;
        END $$;
        """
    )

    # Backfill last_user_activity_at from updated_at for existing conversations
    op.execute(
        """
        UPDATE whatsapp_conversations
        SET last_user_activity_at = updated_at
        WHERE last_user_activity_at IS NULL AND updated_at IS NOT NULL
        """
    )


def downgrade():
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_webhook_events' AND column_name = 'processing_started_at'
            ) THEN
                ALTER TABLE whatsapp_webhook_events DROP COLUMN processing_started_at;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_webhook_events' AND column_name = 'processed_at'
            ) THEN
                ALTER TABLE whatsapp_webhook_events DROP COLUMN processed_at;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_webhook_events' AND column_name = 'processing_worker_id'
            ) THEN
                ALTER TABLE whatsapp_webhook_events DROP COLUMN processing_worker_id;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_webhook_events' AND column_name = 'error_message'
            ) THEN
                ALTER TABLE whatsapp_webhook_events DROP COLUMN error_message;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_webhook_events' AND column_name = 'attempt_count'
            ) THEN
                ALTER TABLE whatsapp_webhook_events DROP COLUMN attempt_count;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_conversations' AND column_name = 'last_user_activity_at'
            ) THEN
                ALTER TABLE whatsapp_conversations DROP COLUMN last_user_activity_at;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'whatsapp_conversations' AND column_name = 'whatsapp_state_json'
            ) THEN
                ALTER TABLE whatsapp_conversations DROP COLUMN whatsapp_state_json;
            END IF;
        END $$;
        """
    )
