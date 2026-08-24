"""Expand source section labels for reviewed KLMS module names.

Revision ID: 20260824_section_label_255
Revises: 20260823_auth_valid_after
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_section_label_255"
down_revision = "20260823_auth_valid_after"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "course_sections",
        "source_section_label",
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade():
    op.execute(
        sa.text(
            "UPDATE course_sections "
            "SET source_section_label = LEFT(source_section_label, 64) "
            "WHERE char_length(source_section_label) > 64"
        )
    )
    op.alter_column(
        "course_sections",
        "source_section_label",
        existing_type=sa.String(length=255),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
