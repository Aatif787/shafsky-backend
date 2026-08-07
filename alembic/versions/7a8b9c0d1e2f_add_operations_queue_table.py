"""add_operations_queue_table

Revision ID: 7a8b9c0d1e2f
Revises: 9e8f7a6b5c4d
Create Date: 2026-08-06 11:37:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7a8b9c0d1e2f'
down_revision: Union[str, Sequence[str], None] = '9e8f7a6b5c4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create operations_queue table."""
    op.create_table(
        'operations_queue',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('booking_reference', sa.String(length=50), nullable=False),
        sa.Column('airport_code', sa.String(length=3), nullable=False),
        sa.Column('journey_type', sa.String(length=20), nullable=False, server_default='ARRIVAL'),
        sa.Column('service_date', sa.String(length=10), nullable=False),
        sa.Column('service_time', sa.String(length=5), nullable=False, server_default='12:00'),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='NEW'),
        sa.Column('assigned_staff_id', sa.UUID(), nullable=True),
        sa.Column('assigned_staff_name', sa.String(length=150), nullable=True),
        sa.Column('customer_name', sa.String(length=150), nullable=False),
        sa.Column('customer_phone', sa.String(length=50), nullable=False),
        sa.Column('customer_email', sa.String(length=150), nullable=False),
        sa.Column('guest_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('flight_number', sa.String(length=30), nullable=True),
        sa.Column('selected_services', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('special_requests', sa.Text(), nullable=True),
        sa.Column('email_notification_sent', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('whatsapp_notification_sent', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('booking_reference')
    )
    op.create_index('ix_operations_queue_airport', 'operations_queue', ['airport_code'], unique=False)
    op.create_index('ix_operations_queue_ref', 'operations_queue', ['booking_reference'], unique=True)
    op.create_index('ix_operations_queue_staff', 'operations_queue', ['assigned_staff_id'], unique=False)
    op.create_index('ix_operations_queue_status', 'operations_queue', ['status'], unique=False)


def downgrade() -> None:
    """Drop operations_queue table."""
    op.drop_table('operations_queue')
