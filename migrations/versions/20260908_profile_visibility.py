"""Add explicit opt-in profile collection visibility; preserve existing public sections."""
from alembic import op
import sqlalchemy as sa

revision = '20260908_profile_visibility'
down_revision = '20260907_maker_social'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('show_favorite_spaces', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('show_created_spaces', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('users', sa.Column('show_recent_posts', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    raise RuntimeError('Profile visibility preferences must be retained; rollback requires a reviewed privacy-preserving plan')
