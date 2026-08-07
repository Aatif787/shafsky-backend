"""Add additional_benefits column to airport_services

Revision ID: 9c0d1e2f3a4b
Revises: 8b9c0d1e2f3a
Create Date: 2026-08-06 14:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c0d1e2f3a4b'
down_revision = '8b9c0d1e2f3a'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'airport_services',
        sa.Column('additional_benefits', sa.JSON(), nullable=True, server_default='[]')
    )


def downgrade():
    op.drop_column('airport_services', 'additional_benefits')
