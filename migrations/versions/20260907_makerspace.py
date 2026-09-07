"""Create MakerSpace control-plane tables; no creator data is imported."""
from alembic import op
import sqlalchemy as sa

revision = "20260907_makerspace"
down_revision = "20260903_recruitment_admin"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('maker_spaces',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('slug', sa.String(40), nullable=False, unique=True),
        sa.Column('owner_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('title_zh', sa.String(100), nullable=False),
        sa.Column('title_en', sa.String(100), nullable=False),
        sa.Column('description_zh', sa.Text, nullable=False),
        sa.Column('description_en', sa.Text, nullable=False),
        sa.Column('category', sa.String(24), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('external_path', sa.String(160)),
        sa.Column('repository', sa.String(200), nullable=False),
        sa.Column('branch', sa.String(100), nullable=False),
        sa.Column('settings', sa.JSON, nullable=False),
        sa.Column('public_key', sa.Text),
        sa.Column('encrypted_private_key', sa.Text),
        sa.Column('encrypted_webhook_secret', sa.Text),
        sa.Column('encrypted_environment', sa.Text),
        sa.Column('auto_deploy', sa.Boolean, nullable=False),
        sa.Column('pending_source_sha', sa.String(40)),
        sa.Column('published_deployment_id', sa.String(32)),
        sa.Column('published_metadata', sa.JSON),
        sa.Column('version', sa.Integer, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_maker_spaces_owner_id", "maker_spaces", ["owner_id"])
    op.create_index("ix_maker_spaces_status", "maker_spaces", ["status"])
    op.create_table('maker_deployments',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('space_id', sa.String(32), sa.ForeignKey('maker_spaces.id'), nullable=False),
        sa.Column('requested_by', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('source_sha', sa.String(40)),
        sa.Column('source_ref', sa.String(100), nullable=False),
        sa.Column('snapshot', sa.JSON, nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('review_status', sa.String(20), nullable=False),
        sa.Column('review_note', sa.Text),
        sa.Column('reviewed_by', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('reviewed_at', sa.DateTime(timezone=True)),
        sa.Column('submitted_at', sa.DateTime(timezone=True)),
        sa.Column('log', sa.Text, nullable=False),
        sa.Column('error_code', sa.String(80)),
        sa.Column('artifact_digest', sa.String(64)),
        sa.Column('worker_id', sa.String(64)),
        sa.Column('lease_hash', sa.String(64)),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True)),
        sa.Column('runtime_port', sa.Integer),
        sa.Column('publication_status', sa.String(20), nullable=False),
        sa.Column('public_runtime_port', sa.Integer),
        sa.Column('encrypted_environment', sa.Text),
        sa.Column('resource_usage', sa.JSON, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
    )
    op.create_index("ix_maker_deployments_space_id", "maker_deployments", ["space_id"])
    op.create_index("ix_maker_deployments_status", "maker_deployments", ["status"])
    op.create_index("ix_maker_deployments_review_status", "maker_deployments", ["review_status"])
    op.create_index("ix_maker_deployments_publication_status", "maker_deployments", ["publication_status"])
    op.create_table('maker_audit_events',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('space_id', sa.String(32), sa.ForeignKey('maker_spaces.id'), nullable=False),
        sa.Column('actor_id', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('action', sa.String(40), nullable=False),
        sa.Column('details', sa.JSON, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_maker_audit_events_space_id", "maker_audit_events", ["space_id"])
    op.create_table('maker_runtime_sessions',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('cookie_hash', sa.String(64), nullable=False),
        sa.Column('runtime_cookie_hash', sa.String(64)),
        sa.Column('bootstrap_hash', sa.String(64)),
        sa.Column('bootstrap_expires_at', sa.DateTime(timezone=True)),
        sa.Column('space_id', sa.String(32), sa.ForeignKey('maker_spaces.id'), nullable=False),
        sa.Column('deployment_id', sa.String(32), sa.ForeignKey('maker_deployments.id'), nullable=False),
        sa.Column('viewer_id', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('private', sa.Boolean, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_maker_runtime_sessions_space_id", "maker_runtime_sessions", ["space_id"])
    op.create_index("ix_maker_runtime_sessions_expires_at", "maker_runtime_sessions", ["expires_at"])
    op.create_table('maker_webhook_deliveries',
        sa.Column('id', sa.String(100), primary_key=True),
        sa.Column('space_id', sa.String(32), sa.ForeignKey('maker_spaces.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_maker_webhook_deliveries_space_id", "maker_webhook_deliveries", ["space_id"])
    op.create_table('maker_workers',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('capabilities', sa.JSON, nullable=False),
    )

def downgrade():
    raise RuntimeError("MakerSpace data requires an explicit backup/restore plan; automatic downgrade is disabled")
