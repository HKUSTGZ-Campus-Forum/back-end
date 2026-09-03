"""Persist recruitment attempts for the private event dashboard.

Revision ID: 20260903_recruitment_admin
Revises: 20260829_meetcampus_runtime
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260903_recruitment_admin"
down_revision = "20260829_meetcampus_runtime"
branch_labels = None
depends_on = None


json_type = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)


def upgrade():
    op.create_table(
        "recruitment_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("username_snapshot", sa.String(length=50), nullable=False),
        sa.Column("email_snapshot", sa.String(length=100), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("feedback", json_type, nullable=False),
        sa.Column("agent_message", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("error", sa.String(length=80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('running', 'complete', 'failed')",
            name="ck_recruitment_attempt_state",
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_recruitment_attempt_score",
        ),
        sa.CheckConstraint(
            "tool_calls >= 0",
            name="ck_recruitment_attempt_tool_calls",
        ),
    )
    op.create_index(
        "idx_recruitment_attempt_user_completed",
        "recruitment_attempts",
        ["user_id", "completed_at"],
    )
    op.create_index(
        "idx_recruitment_attempt_state_score",
        "recruitment_attempts",
        ["state", "score"],
    )


def downgrade():
    op.drop_index(
        "idx_recruitment_attempt_state_score",
        table_name="recruitment_attempts",
    )
    op.drop_index(
        "idx_recruitment_attempt_user_completed",
        table_name="recruitment_attempts",
    )
    op.drop_table("recruitment_attempts")
