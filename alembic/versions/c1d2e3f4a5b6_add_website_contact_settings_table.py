"""Add website_contact_settings table for dynamic service contact management

Revision ID: c1d2e3f4a5b6
Revises: a2b3c4d5e6f7
Create Date: 2026-10-14 14:00:00.000000
"""

from datetime import datetime, timezone
import uuid
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c1d2e3f4a5b6"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None

CANONICAL_SERVICES = [
    ("meet_greet_lounge", "Meet & Greet & Lounge"),
    ("transport", "Transport"),
    ("hotel", "Hotel"),
    ("private_charter", "Private Charter"),
    ("ticketing", "Ticketing"),
    ("contact", "Contact"),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "website_contact_settings" not in tables:
        op.create_table(
            "website_contact_settings",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
            sa.Column("service_key", sa.String(50), nullable=False),
            sa.Column("service_name", sa.String(100), nullable=False),
            sa.Column("slot_index", sa.Integer(), nullable=False),
            sa.Column("country_code", sa.String(10), nullable=True, server_default=""),
            sa.Column("phone_number", sa.String(30), nullable=True, server_default=""),
            sa.Column("full_phone_number", sa.String(40), nullable=True, server_default=""),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("service_key", "slot_index", name="uq_website_contact_service_slot"),
        )
        op.create_index(
            "ix_website_contact_settings_service_key",
            "website_contact_settings",
            ["service_key"],
        )
    else:
        try:
            op.alter_column("website_contact_settings", "created_at", server_default=sa.func.now())
            op.alter_column("website_contact_settings", "updated_at", server_default=sa.func.now())
        except Exception:
            pass

    # Seed the 6 services x 2 slots = 12 records if not already populated
    now = datetime.now(timezone.utc)
    contact_table = sa.table(
        "website_contact_settings",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("service_key", sa.String),
        sa.column("service_name", sa.String),
        sa.column("slot_index", sa.Integer),
        sa.column("country_code", sa.String),
        sa.column("phone_number", sa.String),
        sa.column("full_phone_number", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    for service_key, service_name in CANONICAL_SERVICES:
        for slot in (1, 2):
            existing = bind.execute(
                sa.text(
                    "SELECT 1 FROM website_contact_settings WHERE service_key = :sk AND slot_index = :slot"
                ),
                {"sk": service_key, "slot": slot},
            ).scalar()
            if not existing:
                bind.execute(
                    contact_table.insert().values(
                        id=uuid.uuid4(),
                        service_key=service_key,
                        service_name=service_name,
                        slot_index=slot,
                        country_code="",
                        phone_number="",
                        full_phone_number="",
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "website_contact_settings" in tables:
        op.drop_index("ix_website_contact_settings_service_key", table_name="website_contact_settings")
        op.drop_table("website_contact_settings")
