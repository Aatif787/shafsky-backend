"""add_airport_meet_and_assist_tables

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-07-31 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'e8f9a0b1c2d3'
down_revision = 'd7e8f9a0b1c2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. airport_bookings
    op.create_table(
        'airport_bookings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('booking_reference', sa.String(), nullable=False, unique=True, index=True),
        sa.Column('customer_id', sa.String(), nullable=False, index=True),
        sa.Column('service_package', sa.String(), nullable=False, server_default='STANDARD_MEET_GREET'),
        sa.Column('status', sa.String(), nullable=False, server_default='DRAFT', index=True),
        sa.Column('total_price', sa.Numeric(10, 2), nullable=False, server_default='0.00'),
        sa.Column('currency', sa.String(3), nullable=False, server_default='USD'),
        sa.Column('special_instructions', sa.Text(), nullable=True),
        sa.Column('workflow_instance_id', postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_airport_bookings_customer', 'airport_bookings', ['customer_id'])
    op.create_index('ix_airport_bookings_status', 'airport_bookings', ['status'])

    # 2. airport_passengers
    op.create_table(
        'airport_passengers',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('booking_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('airport_bookings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('full_name', sa.String(), nullable=False),
        sa.Column('gender', sa.String(10), nullable=True),
        sa.Column('dob', sa.String(20), nullable=True),
        sa.Column('nationality', sa.String(100), nullable=True),
        sa.Column('passport_number', sa.String(50), nullable=True),
        sa.Column('contact_email', sa.String(), nullable=True),
        sa.Column('contact_phone', sa.String(), nullable=True),
        sa.Column('is_primary', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_airport_passengers_booking', 'airport_passengers', ['booking_id'])

    # 3. airport_flight_details
    op.create_table(
        'airport_flight_details',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('booking_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('airport_bookings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('airline', sa.String(), nullable=False),
        sa.Column('flight_number', sa.String(), nullable=False, index=True),
        sa.Column('departure_airport', sa.String(5), nullable=False),
        sa.Column('arrival_airport', sa.String(5), nullable=False),
        sa.Column('terminal', sa.String(20), nullable=True),
        sa.Column('scheduled_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('flight_type', sa.String(20), server_default='ARRIVAL', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_airport_flights_booking', 'airport_flight_details', ['booking_id'])
    op.create_index('ix_airport_flights_number', 'airport_flight_details', ['flight_number'])

    # 4. airport_service_addons
    op.create_table(
        'airport_service_addons',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('booking_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('airport_bookings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('service_code', sa.String(), nullable=False),
        sa.Column('quantity', sa.Integer(), server_default='1', nullable=False),
        sa.Column('unit_price', sa.Numeric(10, 2), server_default='0.00', nullable=False),
        sa.Column('total_price', sa.Numeric(10, 2), server_default='0.00', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_airport_addons_booking', 'airport_service_addons', ['booking_id'])


def downgrade() -> None:
    op.drop_table('airport_service_addons')
    op.drop_table('airport_flight_details')
    op.drop_table('airport_passengers')
    op.drop_table('airport_bookings')
