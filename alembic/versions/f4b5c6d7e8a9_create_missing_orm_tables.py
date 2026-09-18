"""create remaining ORM tables missing from Alembic history

Revision ID: f4b5c6d7e8a9
Revises: d0e1f2a3b4c5
Create Date: 2026-09-15 16:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f4b5c6d7e8a9"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def _import_models():
    from app.database import Base
    import app.models.schema  # noqa: F401
    import app.models.payment  # noqa: F401
    import app.models.whatsapp_models  # noqa: F401
    import app.models.airport  # noqa: F401
    import app.models.shared_domain  # noqa: F401
    import app.models.operations_models  # noqa: F401
    import app.models.charter_models  # noqa: F401
    import app.models.journey_models  # noqa: F401
    import app.models.ticketing  # noqa: F401
    import app.models.system_events  # noqa: F401
    return Base


def upgrade() -> None:
    Base = _import_models()
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    missing = [table for table in Base.metadata.sorted_tables if table.name not in existing]
    if missing:
        Base.metadata.create_all(bind=bind, tables=missing)


def downgrade() -> None:
    Base = _import_models()
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    created_here = {
        "air_ticket_passengers",
        "air_ticket_bookings",
        "airport_service_availabilities",
        "booking_documents",
        "booking_passengers",
        "branding_profiles",
        "case_audit_logs",
        "case_messages",
        "contact_messages",
        "coupons",
        "feature_flags",
        "ip_restrictions",
        "lounges",
        "notification_logs",
        "notification_preferences",
        "notifications",
        "package_included_services",
        "passengers",
        "payments",
        "private_charter_requests",
        "saved_replies",
        "service_packages",
        "support_cases",
        "system_events",
        "system_settings",
        "workflow_event_records",
    }
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in created_here and table.name in existing:
            table.drop(bind=bind)
