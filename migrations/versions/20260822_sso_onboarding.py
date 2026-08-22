"""Add durable first-login profile confirmation state.

Revision ID: 20260822_sso_onboarding
Revises: 20260820_title_abbr_255
"""

from alembic import op
import sqlalchemy as sa


revision = "20260822_sso_onboarding"
down_revision = "20260820_title_abbr_255"
branch_labels = None
depends_on = None


def upgrade():
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    if "onboarding_completed_at" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "onboarding_completed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
    # Grandfather every account at rollout time. Using historical account
    # timestamps would falsely imply that the user completed this flow before
    # it existed. New SSO-created users receive NULL and must confirm once.
    op.execute(
        sa.text(
            "UPDATE users "
            "SET onboarding_completed_at = CURRENT_TIMESTAMP "
            "WHERE onboarding_completed_at IS NULL"
        )
    )


def downgrade():
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    if "onboarding_completed_at" in columns:
        op.drop_column("users", "onboarding_completed_at")
