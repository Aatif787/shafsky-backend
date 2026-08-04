"""extend_booking_multi_category_services

Revision ID: f1a2b3c4d5e6
Revises: 59a95c6bdc71
Create Date: 2026-08-04 15:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = '59a95c6bdc71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Add columns to bookings
    op.add_column('bookings', sa.Column('service_category', sa.String(), nullable=False, server_default='Airport Assistance'))
    op.create_index(op.f('ix_bookings_service_category'), 'bookings', ['service_category'], unique=False)
    op.add_column('bookings', sa.Column('service_options', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('bookings', sa.Column('metadata_json', sa.JSON(), nullable=False, server_default='{}'))

    # Make flight columns nullable for non-flight services
    op.alter_column('bookings', 'flight_num', existing_type=sa.String(), nullable=True)
    op.alter_column('bookings', 'origin_code', existing_type=sa.String(), nullable=True)
    op.alter_column('bookings', 'dest_code', existing_type=sa.String(), nullable=True)
    op.alter_column('bookings', 'departure_time', existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column('bookings', 'arrival_time', existing_type=sa.DateTime(timezone=True), nullable=True)

    # 2. Add columns to services_config
    op.add_column('services_config', sa.Column('is_hidden', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('services_config', sa.Column('options_schema', sa.JSON(), nullable=False, server_default='{}'))

def downgrade() -> None:
    op.drop_column('services_config', 'options_schema')
    op.drop_column('services_config', 'is_hidden')

    op.alter_column('bookings', 'arrival_time', existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column('bookings', 'departure_time', existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column('bookings', 'dest_code', existing_type=sa.String(), nullable=False)
    op.alter_column('bookings', 'origin_code', existing_type=sa.String(), nullable=False)
    op.alter_column('bookings', 'flight_num', existing_type=sa.String(), nullable=False)

    op.drop_column('bookings', 'metadata_json')
    op.drop_column('bookings', 'service_options')
    op.drop_index(op.f('ix_bookings_service_category'), table_name='bookings')
    op.drop_column('bookings', 'service_category')
