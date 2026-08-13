"""empty message

Revision ID: 5202003d1ec0
Revises: b79b55da2342
Create Date: 2026-04-20 17:24:04.665654

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5202003d1ec0'
down_revision = 'b79b55da2342'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"]
        for column in inspector.get_columns("gugu_messages")
    }
    if "reply_to_message_id" not in columns:
        op.add_column(
            "gugu_messages",
            sa.Column("reply_to_message_id", sa.Integer(), nullable=True),
        )

    inspector = sa.inspect(op.get_bind())
    has_reply_foreign_key = any(
        foreign_key.get("constrained_columns") == ["reply_to_message_id"]
        and foreign_key.get("referred_table") == "gugu_messages"
        and foreign_key.get("referred_columns") == ["id"]
        for foreign_key in inspector.get_foreign_keys("gugu_messages")
    )
    if not has_reply_foreign_key:
        with op.batch_alter_table('gugu_messages', schema=None) as batch_op:
            batch_op.create_foreign_key(
                'fk_gugu_messages_reply_to_message_id',
                'gugu_messages',
                ['reply_to_message_id'],
                ['id'],
            )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    reply_foreign_keys = [
        foreign_key
        for foreign_key in inspector.get_foreign_keys("gugu_messages")
        if foreign_key.get("constrained_columns") == ["reply_to_message_id"]
        and foreign_key.get("referred_table") == "gugu_messages"
        and foreign_key.get("referred_columns") == ["id"]
    ]
    with op.batch_alter_table('gugu_messages', schema=None) as batch_op:
        for foreign_key in reply_foreign_keys:
            batch_op.drop_constraint(
                foreign_key["name"],
                type_="foreignkey",
            )

    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("gugu_messages")
    }
    if "reply_to_message_id" in columns:
        op.drop_column("gugu_messages", "reply_to_message_id")
