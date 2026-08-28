"""booking recycle bin actor columns

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-08-28 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b3c4d5e6f7a8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "bookings",
        sa.Column("deleted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("bookings", sa.Column("deleted_by_email", sa.String(), nullable=True))
    op.add_column("bookings", sa.Column("deleted_by_role", sa.String(), nullable=True))
    op.create_index("ix_bookings_deleted_at", "bookings", ["deleted_at"], unique=False)
    op.create_index("ix_bookings_deleted_by_email", "bookings", ["deleted_by_email"], unique=False)
    op.create_foreign_key(
        "fk_bookings_deleted_by_user_id",
        "bookings",
        "user_auth",
        ["deleted_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint("fk_bookings_deleted_by_user_id", "bookings", type_="foreignkey")
    op.drop_index("ix_bookings_deleted_by_email", table_name="bookings")
    op.drop_index("ix_bookings_deleted_at", table_name="bookings")
    op.drop_column("bookings", "deleted_by_role")
    op.drop_column("bookings", "deleted_by_email")
    op.drop_column("bookings", "deleted_by_user_id")
