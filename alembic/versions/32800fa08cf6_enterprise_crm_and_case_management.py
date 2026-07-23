"""Enterprise CRM and Case Management

Revision ID: 32800fa08cf6
Revises: 3f4bc85326de
Create Date: 2026-07-23 16:16:01.127020

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM

revision: str = '32800fa08cf6'
down_revision: Union[str, Sequence[str], None] = '3f4bc85326de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Upgrade schema."""
    with op.get_context().autocommit_block():
        op.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'viptier') THEN CREATE TYPE viptier AS ENUM ('VVIP', 'VIP', 'CORPORATE', 'AIRLINE_CREW', 'DIPLOMATIC', 'PRIVATE_CHARTER', 'MEDICAL_ASSISTANCE', 'REGULAR'); END IF; END $$;")
        op.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'casestatus') THEN CREATE TYPE casestatus AS ENUM ('OPEN', 'ASSIGNED', 'PENDING', 'IN_PROGRESS', 'RESOLVED', 'CLOSED', 'CANCELLED'); END IF; END $$;")
        op.execute("ALTER TYPE casestatus ADD VALUE IF NOT EXISTS 'ASSIGNED';")
        op.execute("ALTER TYPE casestatus ADD VALUE IF NOT EXISTS 'PENDING';")
        op.execute("ALTER TYPE casestatus ADD VALUE IF NOT EXISTS 'CANCELLED';")

    op.create_table(
        'customer_cases',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('case_number', sa.String(), nullable=False),
        sa.Column('customer_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('status', PG_ENUM('OPEN', 'ASSIGNED', 'PENDING', 'IN_PROGRESS', 'RESOLVED', 'CLOSED', 'CANCELLED', name='casestatus', create_type=False), nullable=False),
        sa.Column('assigned_to_id', sa.UUID(), nullable=True),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['assigned_to_id'], ['user_auth.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['customer_id'], ['profiles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_customer_cases_case_number'), 'customer_cases', ['case_number'], unique=True)
    op.create_index(op.f('ix_customer_cases_created_at'), 'customer_cases', ['created_at'], unique=False)

    op.create_table(
        'customer_interactions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('customer_id', sa.UUID(), nullable=False),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('actor_email', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['customer_id'], ['profiles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_customer_interactions_created_at'), 'customer_interactions', ['created_at'], unique=False)
    op.create_index(op.f('ix_customer_interactions_event_type'), 'customer_interactions', ['event_type'], unique=False)

    op.add_column('profiles', sa.Column('vip_tier', PG_ENUM('VVIP', 'VIP', 'CORPORATE', 'AIRLINE_CREW', 'DIPLOMATIC', 'PRIVATE_CHARTER', 'MEDICAL_ASSISTANCE', 'REGULAR', name='viptier', create_type=False), nullable=False, server_default='REGULAR'))
    op.add_column('profiles', sa.Column('passport_number', sa.String(), nullable=True))
    op.add_column('profiles', sa.Column('tags', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('profiles', sa.Column('documents_config', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('profiles', sa.Column('notes', sa.Text(), nullable=True))
    op.create_index(op.f('ix_profiles_passport_number'), 'profiles', ['passport_number'], unique=False)

def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_profiles_passport_number'), table_name='profiles')
    op.drop_column('profiles', 'notes')
    op.drop_column('profiles', 'documents_config')
    op.drop_column('profiles', 'tags')
    op.drop_column('profiles', 'passport_number')
    op.drop_column('profiles', 'vip_tier')
    op.drop_index(op.f('ix_customer_interactions_event_type'), table_name='customer_interactions')
    op.drop_index(op.f('ix_customer_interactions_created_at'), table_name='customer_interactions')
    op.drop_table('customer_interactions')
    op.drop_index(op.f('ix_customer_cases_created_at'), table_name='customer_cases')
    op.drop_index(op.f('ix_customer_cases_case_number'), table_name='customer_cases')
    op.drop_table('customer_cases')
