"""extend_booking_multi_category_services

Revision ID: f1a2b3c4d5e6
Revises: 59a95c6bdc71
Create Date: 2026-08-04 15:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = '59a95c6bdc71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _column_names(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # 1. Add columns to bookings
    booking_cols = _column_names("bookings") if "bookings" in _table_names() else set()
    if "service_category" not in booking_cols:
        op.add_column('bookings', sa.Column('service_category', sa.String(), nullable=False, server_default='Airport Assistance'))
        op.create_index(op.f('ix_bookings_service_category'), 'bookings', ['service_category'], unique=False)
    if "service_options" not in booking_cols:
        op.add_column('bookings', sa.Column('service_options', sa.JSON(), nullable=False, server_default='{}'))
    if "metadata_json" not in booking_cols:
        op.add_column('bookings', sa.Column('metadata_json', sa.JSON(), nullable=False, server_default='{}'))

    # Make flight columns nullable for non-flight services
    op.alter_column('bookings', 'flight_num', existing_type=sa.String(), nullable=True)
    op.alter_column('bookings', 'origin_code', existing_type=sa.String(), nullable=True)
    op.alter_column('bookings', 'dest_code', existing_type=sa.String(), nullable=True)
    op.alter_column('bookings', 'departure_time', existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column('bookings', 'arrival_time', existing_type=sa.DateTime(timezone=True), nullable=True)

    # 2. services_config — historically created via create_all; ensure table exists on fresh DBs
    if "services_config" not in _table_names():
        op.create_table(
            "services_config",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("category", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("base_price", sa.Numeric(10, 2), server_default="0"),
            sa.Column("currency", sa.String(), server_default="INR"),
            sa.Column("is_active", sa.Boolean(), server_default="true"),
            sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("sort_order", sa.Integer(), server_default="0"),
            sa.Column("features", sa.JSON(), server_default="[]"),
            sa.Column("options_schema", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
    else:
        cols = _column_names("services_config")
        if "is_hidden" not in cols:
            op.add_column('services_config', sa.Column('is_hidden', sa.Boolean(), nullable=False, server_default='false'))
        if "options_schema" not in cols:
            op.add_column('services_config', sa.Column('options_schema', sa.JSON(), nullable=False, server_default='{}'))


def downgrade() -> None:
    # Only drop additive columns when table pre-existed with other data; keep table if we created it.
    cols = _column_names("services_config") if "services_config" in _table_names() else set()
    if "options_schema" in cols:
        op.drop_column('services_config', 'options_schema')
    if "is_hidden" in cols:
        op.drop_column('services_config', 'is_hidden')

    op.alter_column('bookings', 'arrival_time', existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column('bookings', 'departure_time', existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column('bookings', 'dest_code', existing_type=sa.String(), nullable=False)
    op.alter_column('bookings', 'origin_code', existing_type=sa.String(), nullable=False)
    op.alter_column('bookings', 'flight_num', existing_type=sa.String(), nullable=False)

    booking_cols = _column_names("bookings")
    if "metadata_json" in booking_cols:
        op.drop_column('bookings', 'metadata_json')
    if "service_options" in booking_cols:
        op.drop_column('bookings', 'service_options')
    if "service_category" in booking_cols:
        op.drop_index(op.f('ix_bookings_service_category'), table_name='bookings')
        op.drop_column('bookings', 'service_category')
