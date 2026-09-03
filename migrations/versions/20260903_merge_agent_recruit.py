"""Merge the Agent chat and recruitment dashboard migration branches.

Revision ID: 20260903_merge_agent_recruit
Revises: 20260826_agent_chat, 20260903_recruitment_admin
"""


revision = "20260903_merge_agent_recruit"
down_revision = ("20260826_agent_chat", "20260903_recruitment_admin")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
