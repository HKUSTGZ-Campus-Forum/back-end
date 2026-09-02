"""Add per-user Agent conversations and messages.

Revision ID: 20260826_agent_chat
Revises: 20260829_meetcampus_runtime
"""

from alembic import op
import sqlalchemy as sa


revision = "20260826_agent_chat"
down_revision = "20260829_meetcampus_runtime"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "agent_conversations" not in tables:
        op.create_table(
            "agent_conversations",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("public_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=80), nullable=False),
            sa.Column(
                "is_deleted",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "last_message_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("public_id"),
        )
        op.create_index(
            "idx_agent_conversations_user_activity",
            "agent_conversations",
            ["user_id", "is_deleted", "last_message_at"],
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "agent_messages" not in tables:
        op.create_table(
            "agent_messages",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("conversation_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "role IN ('user', 'assistant')",
                name="valid_agent_message_role",
            ),
            sa.ForeignKeyConstraint(
                ["conversation_id"],
                ["agent_conversations.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "idx_agent_messages_conversation_order",
            "agent_messages",
            ["conversation_id", "id"],
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "agent_messages" in tables:
        op.drop_table("agent_messages")
    if "agent_conversations" in tables:
        op.drop_table("agent_conversations")
