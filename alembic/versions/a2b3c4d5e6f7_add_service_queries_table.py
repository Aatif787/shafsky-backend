"""Add service_queries table for multi-service unavailable requests

Revision ID: a2b3c4d5e6f7
Revises: f4b5c6d7e8a9
Create Date: 2026-10-09 13:15:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a2b3c4d5e6f7"
down_revision = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "service_queries" not in tables:
        op.create_table(
            "service_queries",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("query_ref", sa.String(50), nullable=False),
            sa.Column("passenger_name", sa.String(150), nullable=False),
            sa.Column("passenger_email", sa.String(255), nullable=False),
            sa.Column("passenger_phone", sa.String(50), nullable=False),
            sa.Column("flight_num", sa.String(50), nullable=True),
            sa.Column("service_date", sa.String(50), nullable=True),
            sa.Column("booking_ref", sa.String(50), nullable=True),
            sa.Column("session_id", sa.String(100), nullable=True),
            sa.Column("itinerary", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("requested_services", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("unavailable_services", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("source", sa.String(50), nullable=False, server_default="WEB"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_service_queries_query_ref", "service_queries", ["query_ref"], unique=True)
        op.create_index("ix_service_queries_passenger_email", "service_queries", ["passenger_email"])
        op.create_index("ix_service_queries_booking_ref", "service_queries", ["booking_ref"])
        op.create_index("ix_service_queries_status", "service_queries", ["status"])
        op.create_index("ix_service_queries_created_at", "service_queries", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "service_queries" in tables:
        op.drop_index("ix_service_queries_created_at", table_name="service_queries")
        op.drop_index("ix_service_queries_status", table_name="service_queries")
        op.drop_index("ix_service_queries_booking_ref", table_name="service_queries")
        op.drop_index("ix_service_queries_passenger_email", table_name="service_queries")
        op.drop_index("ix_service_queries_query_ref", table_name="service_queries")
        op.drop_table("service_queries")
