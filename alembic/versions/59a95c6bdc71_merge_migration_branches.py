"""merge_migration_branches

Revision ID: 59a95c6bdc71
Revises: 994f8199e8c5, e8f9a0b1c2d3
Create Date: 2026-08-01 10:44:53.095714

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '59a95c6bdc71'
down_revision: Union[str, Sequence[str], None] = ('994f8199e8c5', 'e8f9a0b1c2d3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
