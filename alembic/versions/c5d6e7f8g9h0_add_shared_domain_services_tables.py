"""add_shared_domain_services_tables

Revision ID: c5d6e7f8g9h0
Revises: b1a2c3d4e5f6
Create Date: 2026-07-31 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c5d6e7f8g9h0'
down_revision = 'b1a2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Assignments
    op.create_table(
        'assignments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('entity_type', sa.String(), nullable=False, index=True),
        sa.Column('entity_id', sa.String(), nullable=False, index=True),
        sa.Column('staff_id', postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('assigned_by', sa.String(), nullable=True),
        sa.Column('role_type', sa.String(), nullable=False, server_default='GENERAL'),
        sa.Column('status', sa.String(), nullable=False, server_default='ASSIGNED'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_assignments_entity', 'assignments', ['entity_type', 'entity_id'])
    op.create_index('ix_assignments_staff_status', 'assignments', ['staff_id', 'status'])

    # 2. Assignment History
    op.create_table(
        'assignment_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('assignment_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('assignments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('from_status', sa.String(), nullable=True),
        sa.Column('to_status', sa.String(), nullable=False),
        sa.Column('actor_id', sa.String(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('metadata_json', postgresql.JSON(), server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_assignment_history_assignment', 'assignment_history', ['assignment_id'])

    # 3. Timeline Entries
    op.create_table(
        'timeline_entries',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('entity_type', sa.String(), nullable=False, index=True),
        sa.Column('entity_id', sa.String(), nullable=False, index=True),
        sa.Column('event_type', sa.String(), nullable=False, index=True),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('details', postgresql.JSON(), server_default='{}'),
        sa.Column('actor_id', sa.String(), nullable=True),
        sa.Column('actor_role', sa.String(), nullable=True),
        sa.Column('reference_type', sa.String(), nullable=True),
        sa.Column('reference_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )
    op.create_index('ix_timeline_entity', 'timeline_entries', ['entity_type', 'entity_id'])
    op.create_index('ix_timeline_entity_ts', 'timeline_entries', ['entity_type', 'entity_id', 'created_at'])

    # 4. Notes
    op.create_table(
        'notes',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('entity_type', sa.String(), nullable=False, index=True),
        sa.Column('entity_id', sa.String(), nullable=False, index=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('visibility', sa.String(), nullable=False, server_default='INTERNAL'),
        sa.Column('author_id', sa.String(), nullable=True),
        sa.Column('mentions', postgresql.JSON(), server_default='[]'),
        sa.Column('is_deleted', sa.Boolean(), server_default='false'),
        sa.Column('deleted_by', sa.String(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_notes_entity', 'notes', ['entity_type', 'entity_id'])
    op.create_index('ix_notes_visibility', 'notes', ['entity_type', 'entity_id', 'visibility'])

    # 5. Note Revisions
    op.create_table(
        'note_revisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('note_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('notes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('content_snapshot', sa.Text(), nullable=False),
        sa.Column('edited_by', sa.String(), nullable=True),
        sa.Column('revision_number', sa.Integer(), server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_note_revisions_note', 'note_revisions', ['note_id'])

    # 6. Attachments
    op.create_table(
        'attachments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('entity_type', sa.String(), nullable=False, index=True),
        sa.Column('entity_id', sa.String(), nullable=False, index=True),
        sa.Column('filename', sa.String(), nullable=False),
        sa.Column('storage_path', sa.String(), nullable=False),
        sa.Column('file_size', sa.BigInteger(), nullable=True),
        sa.Column('mime_type', sa.String(), nullable=True),
        sa.Column('category', sa.String(), nullable=False, server_default='GENERAL'),
        sa.Column('uploaded_by', sa.String(), nullable=True),
        sa.Column('access_level', sa.String(), nullable=False, server_default='STAFF'),
        sa.Column('is_deleted', sa.Boolean(), server_default='false'),
        sa.Column('deleted_by', sa.String(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )
    op.create_index('ix_attachments_entity', 'attachments', ['entity_type', 'entity_id'])
    op.create_index('ix_attachments_category', 'attachments', ['entity_type', 'category'])

    # 7. SLA Definitions
    op.create_table(
        'sla_definitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('service_type', sa.String(), nullable=False, index=True),
        sa.Column('priority', sa.String(), nullable=False, server_default='NORMAL'),
        sa.Column('response_time_minutes', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('resolution_time_minutes', sa.Integer(), nullable=False, server_default='480'),
        sa.Column('escalation_rules', postgresql.JSON(), server_default='{}'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_sla_def_service_priority', 'sla_definitions', ['service_type', 'priority'], unique=True)

    # 8. SLA Instances
    op.create_table(
        'sla_instances',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('sla_definition_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('sla_definitions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('entity_type', sa.String(), nullable=False, index=True),
        sa.Column('entity_id', sa.String(), nullable=False, index=True),
        sa.Column('status', sa.String(), nullable=False, server_default='ACTIVE'),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('escalated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('breached_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_by', sa.String(), nullable=True),
        sa.Column('resolved_by', sa.String(), nullable=True),
        sa.Column('escalated_by', sa.String(), nullable=True),
        sa.Column('escalation_reason', sa.Text(), nullable=True),
        sa.Column('metadata_json', postgresql.JSON(), server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_sla_inst_entity', 'sla_instances', ['entity_type', 'entity_id'])
    op.create_index('ix_sla_inst_status', 'sla_instances', ['status'])
    op.create_index('ix_sla_inst_deadline', 'sla_instances', ['deadline_at'])


def downgrade() -> None:
    op.drop_table('sla_instances')
    op.drop_table('sla_definitions')
    op.drop_table('attachments')
    op.drop_table('note_revisions')
    op.drop_table('notes')
    op.drop_table('timeline_entries')
    op.drop_table('assignment_history')
    op.drop_table('assignments')
