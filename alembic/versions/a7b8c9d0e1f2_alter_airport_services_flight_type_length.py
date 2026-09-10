"""alter airport_services flight_type length to 50

Revision ID: a7b8c9d0e1f2
Revises: f2a3b4c5d6e7
Create Date: 2026-09-10 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "airport_services",
        "flight_type",
        type_=sa.String(length=50),
        existing_type=sa.String(length=20),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "airport_services",
        "flight_type",
        type_=sa.String(length=20),
        existing_type=sa.String(length=50),
        existing_nullable=False,
    )
