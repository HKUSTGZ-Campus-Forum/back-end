"""add admin audit logs

Revision ID: 20260607_admin_audit
Revises: 20260607_course_domain
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260607_admin_audit"
down_revision = "20260607_course_domain"
branch_labels = None
depends_on = None


def _json_type():
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "admin_audit_logs" in inspector.get_table_names():
        return

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("target_label", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("metadata", _json_type(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_admin_audit_logs_action", "admin_audit_logs", ["action"])
    op.create_index("idx_admin_audit_logs_target_type", "admin_audit_logs", ["target_type"])
    op.create_index("idx_admin_audit_logs_target_id", "admin_audit_logs", ["target_id"])
    op.create_index("idx_admin_audit_logs_created_at", "admin_audit_logs", ["created_at"])
    op.create_index("idx_admin_audit_actor_created", "admin_audit_logs", ["actor_user_id", "created_at"])
    op.create_index("idx_admin_audit_target", "admin_audit_logs", ["target_type", "target_id"])


def downgrade():
    op.drop_index("idx_admin_audit_target", table_name="admin_audit_logs")
    op.drop_index("idx_admin_audit_actor_created", table_name="admin_audit_logs")
    op.drop_index("idx_admin_audit_logs_created_at", table_name="admin_audit_logs")
    op.drop_index("idx_admin_audit_logs_target_id", table_name="admin_audit_logs")
    op.drop_index("idx_admin_audit_logs_target_type", table_name="admin_audit_logs")
    op.drop_index("idx_admin_audit_logs_action", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")

