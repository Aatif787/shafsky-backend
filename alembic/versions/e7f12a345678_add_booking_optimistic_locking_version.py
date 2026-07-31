"""Add Booking optimistic locking version column

Revision ID: e7f12a345678
Revises: 45dc91f8a958
Create Date: 2026-07-31 15:59:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7f12a345678'
down_revision = '45dc91f8a958'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'bookings',
        sa.Column('version', sa.Integer(), server_default='1', nullable=False)
    )


def downgrade():
    op.drop_column('bookings', 'version')
