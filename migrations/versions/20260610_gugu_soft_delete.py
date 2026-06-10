"""add soft delete fields to gugu messages

Revision ID: 20260610_gugu_soft_delete
Revises: 20260607_admin_audit
Create Date: 2026-06-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260610_gugu_soft_delete"
down_revision = "20260607_admin_audit"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "gugu_messages" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("gugu_messages")}
    if "is_deleted" not in columns:
        op.add_column(
            "gugu_messages",
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "deleted_at" not in columns:
        op.add_column("gugu_messages", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "gugu_messages" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("gugu_messages")}
    if "deleted_at" in columns:
        op.drop_column("gugu_messages", "deleted_at")
    if "is_deleted" in columns:
        op.drop_column("gugu_messages", "is_deleted")
