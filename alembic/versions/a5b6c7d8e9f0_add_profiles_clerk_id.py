"""add nullable unique profiles.clerk_id for Clerk identity mapping

Revision ID: a5b6c7d8e9f0
Revises: f4b5c6d7e8a9
Create Date: 2026-09-22 10:50:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "a5b6c7d8e9f0"
down_revision = "f4b5c6d7e8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("profiles")}
    if "clerk_id" not in columns:
        op.add_column("profiles", sa.Column("clerk_id", sa.String(), nullable=True))

    indexes = {index["name"] for index in inspector.get_indexes("profiles")}
    if "ix_profiles_clerk_id" not in indexes:
        op.create_index("ix_profiles_clerk_id", "profiles", ["clerk_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("profiles")}
    if "ix_profiles_clerk_id" in indexes:
        op.drop_index("ix_profiles_clerk_id", table_name="profiles")

    columns = {column["name"] for column in inspector.get_columns("profiles")}
    if "clerk_id" in columns:
        op.drop_column("profiles", "clerk_id")
