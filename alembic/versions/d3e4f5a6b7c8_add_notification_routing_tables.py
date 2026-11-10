"""Add WhatsApp notification routing tables. Default routing_mode is env.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-11-10 23:20:00.000000

Called by Alembic (alembic upgrade head). No existing revision creates these tables.
"""

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

SETTINGS_ROW_ID = uuid.UUID("00000000-0000-4000-8000-00000000000a")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "notification_recipients" not in tables:
        op.create_table(
            "notification_recipients",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("label", sa.String(120), nullable=True),
            sa.Column("phone_number", sa.String(20), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("phone_number", name="uq_notification_recipients_phone_number"),
        )
        op.create_index(
            "ix_notification_recipients_is_active",
            "notification_recipients",
            ["is_active"],
        )

    if "notification_service_routes" not in tables:
        op.create_table(
            "notification_service_routes",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("service_key", sa.String(128), nullable=False),
            sa.Column("recipient_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(
                ["recipient_id"],
                ["notification_recipients.id"],
                name="fk_notification_service_routes_recipient_id",
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint(
                "service_key",
                "recipient_id",
                name="uq_notification_service_routes_service_recipient",
            ),
        )
        op.create_index(
            "ix_notification_service_routes_service_key",
            "notification_service_routes",
            ["service_key"],
        )
        op.create_index(
            "ix_notification_service_routes_recipient_id",
            "notification_service_routes",
            ["recipient_id"],
        )
        op.create_index(
            "ix_notification_service_routes_service_active",
            "notification_service_routes",
            ["service_key", "is_active"],
        )

    if "notification_settings" not in tables:
        op.create_table(
            "notification_settings",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("routing_mode", sa.String(16), nullable=False, server_default="env"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint(
                "routing_mode IN ('env', 'database')",
                name="ck_notification_settings_routing_mode",
            ),
            sa.CheckConstraint(
                "replace(cast(id as text), '-', '') = '0000000000004000800000000000000a'",
                name="ck_notification_settings_singleton",
            ),
        )

    existing = bind.execute(
        sa.text(
            "SELECT 1 FROM notification_settings "
            "WHERE replace(cast(id as text), '-', '') = :id"
        ),
        {"id": SETTINGS_ROW_ID.hex},
    ).scalar()
    if not existing:
        now = datetime.now(timezone.utc)
        settings = sa.table(
            "notification_settings",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("routing_mode", sa.String),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        bind.execute(
            settings.insert().values(
                id=SETTINGS_ROW_ID,
                routing_mode="env",
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "notification_service_routes" in tables:
        op.drop_index(
            "ix_notification_service_routes_service_active",
            table_name="notification_service_routes",
        )
        op.drop_index(
            "ix_notification_service_routes_recipient_id",
            table_name="notification_service_routes",
        )
        op.drop_index(
            "ix_notification_service_routes_service_key",
            table_name="notification_service_routes",
        )
        op.drop_table("notification_service_routes")

    if "notification_recipients" in tables:
        op.drop_index("ix_notification_recipients_is_active", table_name="notification_recipients")
        op.drop_table("notification_recipients")

    if "notification_settings" in tables:
        op.drop_table("notification_settings")
