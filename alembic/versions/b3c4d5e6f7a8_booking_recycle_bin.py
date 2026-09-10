"""booking recycle bin actor columns

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-08-28 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "b3c4d5e6f7a8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    cols = _columns("bookings")
    idxs = _indexes("bookings")

    if "deleted_by_user_id" not in cols:
        op.add_column(
            "bookings",
            sa.Column("deleted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    if "deleted_by_email" not in cols:
        op.add_column("bookings", sa.Column("deleted_by_email", sa.String(), nullable=True))
    if "deleted_by_role" not in cols:
        op.add_column("bookings", sa.Column("deleted_by_role", sa.String(), nullable=True))

    if "ix_bookings_deleted_at" not in idxs:
        op.create_index("ix_bookings_deleted_at", "bookings", ["deleted_at"], unique=False)
    if "ix_bookings_deleted_by_email" not in idxs:
        op.create_index("ix_bookings_deleted_by_email", "bookings", ["deleted_by_email"], unique=False)

    fks = {fk["name"] for fk in inspect(op.get_bind()).get_foreign_keys("bookings")}
    if "fk_bookings_deleted_by_user_id" not in fks:
        op.create_foreign_key(
            "fk_bookings_deleted_by_user_id",
            "bookings",
            "user_auth",
            ["deleted_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    fks = {fk["name"] for fk in inspect(op.get_bind()).get_foreign_keys("bookings")}
    if "fk_bookings_deleted_by_user_id" in fks:
        op.drop_constraint("fk_bookings_deleted_by_user_id", "bookings", type_="foreignkey")
    idxs = _indexes("bookings")
    if "ix_bookings_deleted_by_email" in idxs:
        op.drop_index("ix_bookings_deleted_by_email", table_name="bookings")
    if "ix_bookings_deleted_at" in idxs:
        op.drop_index("ix_bookings_deleted_at", table_name="bookings")
    cols = _columns("bookings")
    if "deleted_by_role" in cols:
        op.drop_column("bookings", "deleted_by_role")
    if "deleted_by_email" in cols:
        op.drop_column("bookings", "deleted_by_email")
    if "deleted_by_user_id" in cols:
        op.drop_column("bookings", "deleted_by_user_id")
