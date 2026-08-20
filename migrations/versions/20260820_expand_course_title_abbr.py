"""Expand course title abbreviations for official SISN values.

Revision ID: 20260820_title_abbr_255
Revises: 20260819_sisn_automation
"""

from alembic import op
import sqlalchemy as sa


revision = "20260820_title_abbr_255"
down_revision = "20260819_sisn_automation"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "courses",
        "course_title_abbr",
        existing_type=sa.String(length=48),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "course_catalog_versions",
        "title_abbr",
        existing_type=sa.String(length=48),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade():
    op.execute(
        sa.text(
            "UPDATE courses SET course_title_abbr = LEFT(course_title_abbr, 48) "
            "WHERE char_length(course_title_abbr) > 48"
        )
    )
    op.execute(
        sa.text(
            "UPDATE course_catalog_versions SET title_abbr = LEFT(title_abbr, 48) "
            "WHERE char_length(title_abbr) > 48"
        )
    )
    op.alter_column(
        "course_catalog_versions",
        "title_abbr",
        existing_type=sa.String(length=255),
        type_=sa.String(length=48),
        existing_nullable=True,
    )
    op.alter_column(
        "courses",
        "course_title_abbr",
        existing_type=sa.String(length=255),
        type_=sa.String(length=48),
        existing_nullable=True,
    )
