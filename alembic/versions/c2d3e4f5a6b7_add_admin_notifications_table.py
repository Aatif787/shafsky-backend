"""Create admin_notifications table for enterprise real-time alerts

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-11-02 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "admin_notifications" in inspector.get_table_names():
        return

    op.create_table(
        "admin_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("booking_ref", sa.String(64), nullable=False),
        sa.Column("service_category", sa.String(128), nullable=False),
        sa.Column("service_type", sa.String(128), nullable=False),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("customer_phone", sa.String(64), nullable=True),
        sa.Column("status", sa.String(64), nullable=False, server_default="PENDING"),
        sa.Column("event_type", sa.String(64), nullable=False, server_default="BOOKING_CREATED"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("link", sa.String(255), nullable=False, server_default="/admin/operations"),
        sa.Column("metadata_json", sa.JSON(), nullable=True, server_default=sa.text("'{}'::json")),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_admin_notifications_created_at", "admin_notifications", ["created_at"])
    op.create_index("ix_admin_notifications_is_read", "admin_notifications", ["is_read"])
    op.create_index("ix_admin_notifications_booking_ref", "admin_notifications", ["booking_ref"])
    op.create_index("ix_admin_notifications_unread_created", "admin_notifications", ["is_read", "created_at"])
    op.create_unique_constraint(
        "uq_admin_notifications_ref_event",
        "admin_notifications",
        ["booking_ref", "event_type"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "admin_notifications" not in inspector.get_table_names():
        return

    op.drop_constraint("uq_admin_notifications_ref_event", "admin_notifications", type_="unique")
    op.drop_index("ix_admin_notifications_unread_created", table_name="admin_notifications")
    op.drop_index("ix_admin_notifications_booking_ref", table_name="admin_notifications")
    op.drop_index("ix_admin_notifications_is_read", table_name="admin_notifications")
    op.drop_index("ix_admin_notifications_created_at", table_name="admin_notifications")
    op.drop_table("admin_notifications")

