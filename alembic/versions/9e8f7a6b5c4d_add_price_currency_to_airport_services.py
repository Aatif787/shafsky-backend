"""add_price_currency_to_airport_services

Revision ID: 9e8f7a6b5c4d
Revises: 8d9c3bd0c036
Create Date: 2026-08-06 10:46:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e8f7a6b5c4d'
down_revision: Union[str, Sequence[str], None] = '8d9c3bd0c036'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add price and currency columns to airport_services table."""
    op.add_column('airport_services', sa.Column('price', sa.Numeric(precision=10, scale=2), server_default='2499.00', nullable=False))
    op.add_column('airport_services', sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False))


def downgrade() -> None:
    """Remove price and currency columns from airport_services table."""
    op.drop_column('airport_services', 'currency')
    op.drop_column('airport_services', 'price')
