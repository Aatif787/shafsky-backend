"""add_workflow_engine_core_tables

Revision ID: b1a2c3d4e5f6
Revises: e7f12a345678
Create Date: 2026-07-31 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'b1a2c3d4e5f6'
down_revision = 'e7f12a345678'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create workflow_definitions table
    op.create_table(
        'workflow_definitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('service_type', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('initial_state', sa.String(), nullable=False),
        sa.Column('states_config', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False)
    )
    op.create_index('ix_workflow_definitions_service_type', 'workflow_definitions', ['service_type'])

    # 2. Create workflow_instances table
    op.create_table(
        'workflow_instances',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('workflow_definition_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workflow_definitions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('service_type', sa.String(), nullable=False),
        sa.Column('entity_id', sa.String(), nullable=False),
        sa.Column('current_state', sa.String(), nullable=False),
        sa.Column('context_data', sa.JSON(), nullable=False),
        sa.Column('is_completed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False)
    )
    op.create_index('ix_workflow_instances_service_type', 'workflow_instances', ['service_type'])
    op.create_index('ix_workflow_instances_entity_id', 'workflow_instances', ['entity_id'])
    op.create_index('ix_workflow_instances_current_state', 'workflow_instances', ['current_state'])

    # 3. Create workflow_history table
    op.create_table(
        'workflow_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('instance_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workflow_instances.id', ondelete='CASCADE'), nullable=False),
        sa.Column('from_state', sa.String(), nullable=False),
        sa.Column('to_state', sa.String(), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('actor_id', sa.String(), nullable=True),
        sa.Column('actor_role', sa.String(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('transition_metadata', sa.JSON(), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False)
    )
    op.create_index('ix_workflow_history_instance_id', 'workflow_history', ['instance_id'])

    # 4. Create workflow_audit_logs table
    op.create_table(
        'workflow_audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('instance_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workflow_instances.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('actor_id', sa.String(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False)
    )
    op.create_index('ix_workflow_audit_logs_instance_id', 'workflow_audit_logs', ['instance_id'])
    op.create_index('ix_workflow_audit_logs_event_type', 'workflow_audit_logs', ['event_type'])


def downgrade() -> None:
    op.drop_table('workflow_audit_logs')
    op.drop_table('workflow_history')
    op.drop_table('workflow_instances')
    op.drop_table('workflow_definitions')
