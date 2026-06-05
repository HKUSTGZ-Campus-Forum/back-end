"""add scheduler fields to courses table

Revision ID: 20260531_add_scheduler_fields
Revises: 20260529_academic_map
Create Date: 2026-05-31
"""

from alembic import op
import sqlalchemy as sa

revision = "20260531_add_scheduler_fields"
down_revision = "20260529_academic_map"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("courses", sa.Column("subject", sa.String(length=4), nullable=True))
    op.add_column("courses", sa.Column("catalog_number", sa.String(length=16), nullable=True))
    op.add_column("courses", sa.Column("course_title_abbr", sa.String(length=48), nullable=True))
    op.add_column("courses", sa.Column("pre_requirement", sa.Text(), nullable=True))
    op.add_column("courses", sa.Column("co_requirement", sa.Text(), nullable=True))
    op.add_column("courses", sa.Column("exclusion", sa.Text(), nullable=True))
    op.add_column("courses", sa.Column("pg_course", sa.Boolean(), nullable=True, server_default=sa.text("false")))
    op.add_column("courses", sa.Column("klms_course", sa.Boolean(), nullable=True, server_default=sa.text("false")))
    op.add_column("courses", sa.Column("vector", sa.String(length=16), nullable=True))


def downgrade():
    op.drop_column("courses", "vector")
    op.drop_column("courses", "klms_course")
    op.drop_column("courses", "pg_course")
    op.drop_column("courses", "exclusion")
    op.drop_column("courses", "co_requirement")
    op.drop_column("courses", "pre_requirement")
    op.drop_column("courses", "course_title_abbr")
    op.drop_column("courses", "catalog_number")
    op.drop_column("courses", "subject")
