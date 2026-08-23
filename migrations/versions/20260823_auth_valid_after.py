"""Add a per-account authentication token cutoff.

Revision ID: 20260823_auth_valid_after
Revises: 20260822_scheduler_plans
"""

from alembic import op
import sqlalchemy as sa


revision = "20260823_auth_valid_after"
down_revision = "20260822_scheduler_plans"
branch_labels = None
depends_on = None


def upgrade():
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    if "auth_valid_after" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "auth_valid_after",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )


def downgrade():
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    if "auth_valid_after" in columns:
        op.drop_column("users", "auth_valid_after")
