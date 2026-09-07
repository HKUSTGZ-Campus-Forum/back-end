"""Add MakerSpace covers, likes and private favorites without changing existing content."""
from alembic import op
import sqlalchemy as sa

revision = "20260907_maker_social"
down_revision = "20260907_teamup_makerspace"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("maker_spaces", sa.Column("cover_file_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_maker_spaces_cover_file", "maker_spaces", "files", ["cover_file_id"], ["id"])
    op.create_index("ix_maker_spaces_cover_file_id", "maker_spaces", ["cover_file_id"])
    for table in ("maker_likes", "maker_favorites"):
        op.create_table(table,
            sa.Column("space_id", sa.String(32), sa.ForeignKey("maker_spaces.id"), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])


def downgrade():
    raise RuntimeError("Retain creator reactions on application rollback; data removal needs a separate approved plan")
