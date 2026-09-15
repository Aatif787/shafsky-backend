"""reconcile user and airport service schema with ORM models

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-15 11:40:00.000000

The application has used ``UserAuth.is_active`` and
``AirportService.terminal`` since the journey-service work landed, but an
empty database created solely through Alembic did not receive those columns.
This migration makes the schema produced by ``alembic upgrade head`` match
the mapped models; it is deliberately safe for already-provisioned databases.
"""

from alembic import op
import sqlalchemy as sa


revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    user_columns = {column["name"] for column in inspector.get_columns("user_auth")}
    if "is_active" not in user_columns:
        op.add_column(
            "user_auth",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )

    airport_service_columns = {
        column["name"] for column in inspector.get_columns("airport_services")
    }
    if "terminal" not in airport_service_columns:
        op.add_column("airport_services", sa.Column("terminal", sa.String(length=50), nullable=True))

    indexes = {index["name"] for index in inspector.get_indexes("airport_services")}
    if "ix_airport_services_terminal" not in indexes:
        op.create_index("ix_airport_services_terminal", "airport_services", ["terminal"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("airport_services")}
    if "ix_airport_services_terminal" in indexes:
        op.drop_index("ix_airport_services_terminal", table_name="airport_services")

    airport_service_columns = {
        column["name"] for column in inspector.get_columns("airport_services")
    }
    if "terminal" in airport_service_columns:
        op.drop_column("airport_services", "terminal")

    user_columns = {column["name"] for column in inspector.get_columns("user_auth")}
    if "is_active" in user_columns:
        op.drop_column("user_auth", "is_active")
