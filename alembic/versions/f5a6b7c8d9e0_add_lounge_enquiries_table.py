"""Add lounge_enquiries table for Lounge enquiry-only service

Revision ID: f5a6b7c8d9e0
Revises: e3f4a5b6c7d8
Create Date: 2026-10-24 18:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f5a6b7c8d9e0"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "lounge_enquiries" not in tables:
        op.create_table(
            "lounge_enquiries",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("enquiry_reference", sa.String(100), nullable=False),
            sa.Column("airport", sa.String(255), nullable=False),
            sa.Column("lounge_type", sa.String(100), nullable=False),
            sa.Column("travel_date", sa.String(50), nullable=False),
            sa.Column("preferred_time", sa.String(100), nullable=False),
            sa.Column("number_of_passengers", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("flight_number", sa.String(50), nullable=True),
            sa.Column("full_name", sa.String(150), nullable=False),
            sa.Column("mobile_number", sa.String(50), nullable=False),
            sa.Column("email_address", sa.String(255), nullable=False),
            sa.Column("whatsapp_number", sa.String(50), nullable=True),
            sa.Column("additional_requirements", sa.Text(), nullable=True),
            sa.Column(
                "status",
                sa.Enum(
                    "PENDING",
                    "CONTACTED",
                    "IN_REVIEW",
                    "QUOTE_SENT",
                    "CONFIRMED",
                    "CANCELLED",
                    "CLOSED",
                    name="loungeenquirystatus",
                    create_type=False,
                ),
                nullable=False,
                server_default="PENDING",
            ),
            sa.Column("assigned_staff_id", sa.String(100), nullable=True),
            sa.Column("assigned_staff_name", sa.String(150), nullable=True),
            sa.Column("internal_notes", sa.Text(), nullable=True),
            sa.Column("client_ip", sa.String(50), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_lounge_enq_ref", "lounge_enquiries", ["enquiry_reference"], unique=True)
        op.create_index("ix_lounge_enq_email", "lounge_enquiries", ["email_address"])
        op.create_index("ix_lounge_enq_status", "lounge_enquiries", ["status"])
        op.create_index("ix_lounge_enq_airport", "lounge_enquiries", ["airport"])
        op.create_index("ix_lounge_enq_created_at", "lounge_enquiries", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "lounge_enquiries" in tables:
        op.drop_index("ix_lounge_enq_created_at", table_name="lounge_enquiries")
        op.drop_index("ix_lounge_enq_airport", table_name="lounge_enquiries")
        op.drop_index("ix_lounge_enq_status", table_name="lounge_enquiries")
        op.drop_index("ix_lounge_enq_email", table_name="lounge_enquiries")
        op.drop_index("ix_lounge_enq_ref", table_name="lounge_enquiries")
        op.drop_table("lounge_enquiries")
