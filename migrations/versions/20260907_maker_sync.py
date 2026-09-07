"""Add directional exchange approvals and metadata-only receipts/audit.

No grants or student records are seeded. Production requires current approval.
"""
from alembic import op
import sqlalchemy as sa

revision = '20260907_maker_sync'
down_revision = '20260907_maker_social'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('maker_identities',
        sa.Column('space_id', sa.String(32), sa.ForeignKey('maker_spaces.id'), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('subject', sa.String(32), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('maker_sync_grants',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('space_id', sa.String(32), sa.ForeignKey('maker_spaces.id'), nullable=False),
        sa.Column('deployment_id', sa.String(32), sa.ForeignKey('maker_deployments.id'), nullable=False),
        sa.Column('requested_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('policy', sa.JSON(), nullable=False), sa.Column('policy_digest', sa.String(64), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('reviewed_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('reviewed_at', sa.DateTime(timezone=True)), sa.Column('review_note', sa.Text()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('token_hash', sa.String(64)), sa.Column('token_rotated_at', sa.DateTime(timezone=True)),
        sa.Column('window_start', sa.DateTime(timezone=True)), sa.Column('window_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_maker_sync_grants_space_id', 'maker_sync_grants', ['space_id'])
    op.create_index('ix_maker_sync_grants_status', 'maker_sync_grants', ['status'])
    op.create_table('maker_sync_receipts',
        sa.Column('grant_id', sa.String(32), sa.ForeignKey('maker_sync_grants.id'), primary_key=True),
        sa.Column('event_id', sa.String(64), primary_key=True),
        sa.Column('payload_hash', sa.String(64), nullable=False), sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('maker_sync_audits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('grant_id', sa.String(32), sa.ForeignKey('maker_sync_grants.id'), nullable=False),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('action', sa.String(32), nullable=False), sa.Column('record_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_maker_sync_audits_grant_id', 'maker_sync_audits', ['grant_id'])


def downgrade():
    op.drop_table('maker_sync_audits')
    op.drop_table('maker_sync_receipts')
    op.drop_table('maker_sync_grants')
    op.drop_table('maker_identities')
