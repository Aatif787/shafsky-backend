"""add_is_frozen_to_workflow_instances

Revision ID: d7e8f9a0b1c2
Revises: c5d6e7f8g9h0
Create Date: 2026-07-31 18:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7e8f9a0b1c2'
down_revision = 'c5d6e7f8g9h0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('workflow_instances', sa.Column('is_frozen', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('workflow_instances', 'is_frozen')
