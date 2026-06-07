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
    existing_course_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("courses")
    }
    scheduler_columns = [
        ("subject", sa.Column("subject", sa.String(length=4), nullable=True)),
        ("catalog_number", sa.Column("catalog_number", sa.String(length=16), nullable=True)),
        ("course_title_abbr", sa.Column("course_title_abbr", sa.String(length=48), nullable=True)),
        ("pre_requirement", sa.Column("pre_requirement", sa.Text(), nullable=True)),
        ("co_requirement", sa.Column("co_requirement", sa.Text(), nullable=True)),
        ("exclusion", sa.Column("exclusion", sa.Text(), nullable=True)),
        ("pg_course", sa.Column("pg_course", sa.Boolean(), nullable=True, server_default=sa.text("false"))),
        ("klms_course", sa.Column("klms_course", sa.Boolean(), nullable=True, server_default=sa.text("false"))),
        ("vector", sa.Column("vector", sa.String(length=16), nullable=True)),
    ]
    for column_name, column in scheduler_columns:
        if column_name not in existing_course_columns:
            op.add_column("courses", column)


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
