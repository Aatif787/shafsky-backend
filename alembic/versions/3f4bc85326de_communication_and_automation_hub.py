"""Communication and Automation Hub

Revision ID: 3f4bc85326de
Revises: 9a01ef5d60c3
Create Date: 2026-07-23 16:04:52.042682

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '3f4bc85326de'
down_revision: Union[str, Sequence[str], None] = '9a01ef5d60c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'notification_records',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('recipient_email', sa.String(), nullable=True),
        sa.Column('recipient_phone', sa.String(), nullable=True),
        sa.Column('template_type', sa.String(), nullable=False),
        sa.Column('channel', sa.String(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.Enum('QUEUED', 'SENDING', 'DELIVERED', 'FAILED', 'OPENED', 'READ', 'BYPASSED', 'PENDING', 'PROCESSING', 'COMPLETED', name='notificationstatus'), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('error_log', sa.Text(), nullable=True),
        sa.Column('message_id', sa.String(), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notification_records_created_at'), 'notification_records', ['created_at'], unique=False)
    op.create_index(op.f('ix_notification_records_recipient_email'), 'notification_records', ['recipient_email'], unique=False)
    op.create_index(op.f('ix_notification_records_recipient_phone'), 'notification_records', ['recipient_phone'], unique=False)
    op.create_index(op.f('ix_notification_records_template_type'), 'notification_records', ['template_type'], unique=False)

def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_notification_records_template_type'), table_name='notification_records')
    op.drop_index(op.f('ix_notification_records_recipient_phone'), table_name='notification_records')
    op.drop_index(op.f('ix_notification_records_recipient_email'), table_name='notification_records')
    op.drop_index(op.f('ix_notification_records_created_at'), table_name='notification_records')
    op.drop_table('notification_records')
