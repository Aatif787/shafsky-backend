"""add flight_api_cache used by the unified flight cache

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-15 14:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "flight_api_cache" in inspector.get_table_names():
        return

    op.create_table(
        "flight_api_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("flight_iata", sa.String(length=20), nullable=False),
        sa.Column("flight_date", sa.String(length=20), nullable=False),
        sa.Column("response_data", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_flight_api_cache_provider_iata_date",
        "flight_api_cache",
        ["provider", "flight_iata", "flight_date"],
        unique=True,
    )
    op.create_index(
        "ix_flight_api_cache_provider_iata",
        "flight_api_cache",
        ["provider", "flight_iata"],
        unique=False,
    )
    op.create_index(
        "ix_flight_api_cache_expires_at",
        "flight_api_cache",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "flight_api_cache" not in inspector.get_table_names():
        return
    op.drop_index("ix_flight_api_cache_expires_at", table_name="flight_api_cache")
    op.drop_index("ix_flight_api_cache_provider_iata", table_name="flight_api_cache")
    op.drop_index("ix_flight_api_cache_provider_iata_date", table_name="flight_api_cache")
    op.drop_table("flight_api_cache")
