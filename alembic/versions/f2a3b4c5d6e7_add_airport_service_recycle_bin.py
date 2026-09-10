"""add airport service recycle bin columns

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-10 11:50:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "airport_services",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "airport_services",
        sa.Column("deleted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "airport_services",
        sa.Column("deleted_by_email", sa.String(255), nullable=True),
    )
    op.add_column(
        "airport_services",
        sa.Column("deleted_by_role", sa.String(50), nullable=True),
    )
    op.create_index(
        "ix_airport_services_deleted_at",
        "airport_services",
        ["deleted_at"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_airport_services_deleted_by_user_id",
        "airport_services",
        "user_auth",
        ["deleted_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint("fk_airport_services_deleted_by_user_id", "airport_services", type_="foreignkey")
    op.drop_index("ix_airport_services_deleted_at", table_name="airport_services")
    op.drop_column("airport_services", "deleted_by_role")
    op.drop_column("airport_services", "deleted_by_email")
    op.drop_column("airport_services", "deleted_by_user_id")
    op.drop_column("airport_services", "deleted_at")
