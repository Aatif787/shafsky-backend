"""add_flight_type_and_features_to_airport_services

Revision ID: 8b9c0d1e2f3a
Revises: 7a8b9c0d1e2f
Create Date: 2026-08-06 13:07:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b9c0d1e2f3a'
down_revision: Union[str, Sequence[str], None] = '7a8b9c0d1e2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add flight_type, short_description, and features columns to airport_services."""
    op.add_column('airport_services', sa.Column('flight_type', sa.String(length=20), nullable=False, server_default='DOMESTIC'))
    op.add_column('airport_services', sa.Column('short_description', sa.String(length=255), nullable=True))
    op.add_column('airport_services', sa.Column('features', sa.JSON(), nullable=False, server_default='[]'))
    op.create_index('ix_airport_services_flight_type', 'airport_services', ['flight_type'], unique=False)
    
    # Update constraint
    try:
        op.drop_constraint('uq_airport_service_journey', 'airport_services', type_='unique')
    except Exception:
        pass
    op.create_unique_constraint('uq_airport_service_journey_flight', 'airport_services', ['airport_id', 'service_id', 'journey_type', 'flight_type'])


def downgrade() -> None:
    """Remove columns."""
    op.drop_constraint('uq_airport_service_journey_flight', 'airport_services', type_='unique')
    op.drop_index('ix_airport_services_flight_type', table_name='airport_services')
    op.drop_column('airport_services', 'features')
    op.drop_column('airport_services', 'short_description')
    op.drop_column('airport_services', 'flight_type')
