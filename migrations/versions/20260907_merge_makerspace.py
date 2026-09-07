"""Merge MakerSpace and the pre-existing main-only Agent migration branch.

The MakerSpace production backport intentionally excludes this merge and the
Agent branch. Main keeps one Alembic head without bundling unrelated SQL into
the MakerSpace school release.
"""
revision = "20260907_merge_makerspace"
down_revision = ("20260907_teamup_makerspace", "20260903_merge_agent_recruit")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
