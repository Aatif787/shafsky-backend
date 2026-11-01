"""Create booking_drafts for persistent incomplete bookings / cart

Revision ID: b1c2d3e4f5a6
Revises: f5a6b7c8d9e0
Create Date: 2026-10-31 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b1c2d3e4f5a6"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "booking_drafts" in inspector.get_table_names():
        return

    op.create_table(
        "booking_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("draft_ref", sa.String(40), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("guest_id", sa.String(64), nullable=True),
        sa.Column("client_key", sa.String(80), nullable=False),
        sa.Column("service_key", sa.String(60), nullable=False),
        sa.Column("service_name", sa.String(150), nullable=False),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("step_key", sa.String(80), nullable=True),
        sa.Column("completed_steps", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("form_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("resume_url", sa.String(2048), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("booking_ref", sa.String(40), nullable=True),
        sa.Column("access_token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_saved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_booking_drafts_draft_ref", "booking_drafts", ["draft_ref"], unique=True)
    op.create_index("ix_booking_drafts_user_id", "booking_drafts", ["user_id"])
    op.create_index("ix_booking_drafts_guest_id", "booking_drafts", ["guest_id"])
    op.create_index("ix_booking_drafts_service_key", "booking_drafts", ["service_key"])
    op.create_index("ix_booking_drafts_status", "booking_drafts", ["status"])
    op.create_index(
        "ix_booking_drafts_user_status",
        "booking_drafts",
        ["user_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_booking_drafts_guest_status",
        "booking_drafts",
        ["guest_id", "status", "updated_at"],
    )
    op.create_index("ix_booking_drafts_client_owner", "booking_drafts", ["client_key", "user_id"])
    op.create_index("ix_booking_drafts_client_guest", "booking_drafts", ["client_key", "guest_id"])
    op.create_index("ix_booking_drafts_expires", "booking_drafts", ["expires_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "booking_drafts" not in inspector.get_table_names():
        return
    for index_name in (
        "ix_booking_drafts_expires",
        "ix_booking_drafts_client_guest",
        "ix_booking_drafts_client_owner",
        "ix_booking_drafts_guest_status",
        "ix_booking_drafts_user_status",
        "ix_booking_drafts_status",
        "ix_booking_drafts_service_key",
        "ix_booking_drafts_guest_id",
        "ix_booking_drafts_user_id",
        "ix_booking_drafts_draft_ref",
    ):
        op.drop_index(index_name, table_name="booking_drafts")
    op.drop_table("booking_drafts")
