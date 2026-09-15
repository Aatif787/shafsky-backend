"""reconcile refresh token columns with the rotation implementation

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-15 11:50:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("refresh_tokens")}

    additions = (
        ("family_id", sa.UUID(), True),
        ("token_hash", sa.String(), True),
        ("device_id", sa.String(), True),
        ("browser", sa.String(), True),
        ("platform", sa.String(), True),
        ("ip_address", sa.String(), True),
    )
    for name, column_type, nullable in additions:
        if name not in columns:
            op.add_column("refresh_tokens", sa.Column(name, column_type, nullable=nullable))

    # Preserve existing legacy tokens during an in-place upgrade. New records
    # always use token_hash through the ORM.
    if "token" in columns:
        op.execute(
            "UPDATE refresh_tokens SET token_hash = token "
            "WHERE token_hash IS NULL"
        )

    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("refresh_tokens")}
    if "ix_refresh_tokens_family_revoked" not in indexes:
        op.create_index(
            "ix_refresh_tokens_family_revoked", "refresh_tokens", ["family_id", "revoked"]
        )
    if "ix_refresh_tokens_user_revoked" not in indexes:
        op.create_index(
            "ix_refresh_tokens_user_revoked", "refresh_tokens", ["user_id", "revoked"]
        )
    if "ix_refresh_tokens_token_hash" not in indexes:
        op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("refresh_tokens")}
    for name in (
        "ix_refresh_tokens_token_hash",
        "ix_refresh_tokens_user_revoked",
        "ix_refresh_tokens_family_revoked",
    ):
        if name in indexes:
            op.drop_index(name, table_name="refresh_tokens")

    columns = {column["name"] for column in inspector.get_columns("refresh_tokens")}
    for name in ("ip_address", "platform", "browser", "device_id", "token_hash", "family_id"):
        if name in columns:
            op.drop_column("refresh_tokens", name)
