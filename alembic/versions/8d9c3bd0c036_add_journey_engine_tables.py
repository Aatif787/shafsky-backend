"""add_journey_engine_tables

Revision ID: 8d9c3bd0c036
Revises: f1a2b3c4d5e6
Create Date: 2026-08-06 10:32:07.724963

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8d9c3bd0c036'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create supported_airports, services, and airport_services tables."""

    # 1. Services table
    op.create_table('services',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('slug', sa.String(length=80), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('icon', sa.String(length=50), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )
    op.create_index('ix_services_active', 'services', ['is_active'], unique=False)
    op.create_index('ix_services_slug', 'services', ['slug'], unique=True)

    # 2. Supported Airports table
    op.create_table('supported_airports',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('airport_name', sa.String(length=255), nullable=False),
        sa.Column('iata_code', sa.String(length=3), nullable=False),
        sa.Column('icao_code', sa.String(length=4), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=False),
        sa.Column('country', sa.String(length=100), nullable=False),
        sa.Column('timezone', sa.String(length=50), nullable=False),
        sa.Column('is_supported', sa.Boolean(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('iata_code'),
    )
    op.create_index('ix_supported_airports_active', 'supported_airports', ['is_active'], unique=False)
    op.create_index('ix_supported_airports_iata', 'supported_airports', ['iata_code'], unique=True)

    # 3. Airport↔Service mapping table
    op.create_table('airport_services',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('airport_id', sa.UUID(), nullable=False),
        sa.Column('service_id', sa.UUID(), nullable=False),
        sa.Column('journey_type', sa.String(length=20), nullable=False),
        sa.Column('min_booking_notice_hours', sa.Integer(), nullable=False),
        sa.Column('is_available', sa.Boolean(), nullable=False),
        sa.Column('display_priority', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['airport_id'], ['supported_airports.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['service_id'], ['services.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('airport_id', 'service_id', 'journey_type', name='uq_airport_service_journey'),
    )
    op.create_index('ix_airport_services_airport', 'airport_services', ['airport_id'], unique=False)
    op.create_index('ix_airport_services_available', 'airport_services', ['is_available'], unique=False)
    op.create_index('ix_airport_services_journey_type', 'airport_services', ['journey_type'], unique=False)
    op.create_index('ix_airport_services_service', 'airport_services', ['service_id'], unique=False)


def downgrade() -> None:
    """Drop journey engine tables."""
    op.drop_table('airport_services')
    op.drop_table('supported_airports')
    op.drop_table('services')
