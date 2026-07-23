"""Update booking status enum

Revision ID: 45dc91f8a958
Revises: 2d8168827ac0
Create Date: 2026-07-23 15:47:52.549209

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '45dc91f8a958'
down_revision: Union[str, Sequence[str], None] = '2d8168827ac0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE bookingstatus ADD VALUE IF NOT EXISTS 'ASSIGNED'")
        op.execute("ALTER TYPE bookingstatus ADD VALUE IF NOT EXISTS 'IN_PROGRESS'")
        op.execute("ALTER TYPE bookingstatus ADD VALUE IF NOT EXISTS 'REJECTED'")

def downgrade() -> None:
    pass
