"""add scheduler section, lecture, map, and cart tables

Revision ID: 20260531_add_scheduler_section_lecture_map_cart
Revises: 20260531_add_scheduler_fields
Create Date: 2026-05-31
"""

from alembic import op
import sqlalchemy as sa

revision = "20260531_add_scheduler_section_lecture_map_cart"
down_revision = "20260531_add_scheduler_fields"
branch_labels = None
depends_on = None


def upgrade():
    scheduler_tables = {
        "scheduler_sections",
        "scheduler_lectures",
        "scheduler_map_components",
        "scheduler_map_lines",
        "scheduler_user_course_carts",
        "scheduler_user_bundle_carts",
    }
    if scheduler_tables.issubset(set(sa.inspect(op.get_bind()).get_table_names())):
        return

    # scheduler_sections
    op.create_table(
        "scheduler_sections",
        sa.Column("semester_id", sa.String(length=16), nullable=False),
        sa.Column("section_id", sa.String(length=16), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("bundle", sa.Integer(), nullable=False),
        sa.Column("layer", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("quota", sa.Integer(), nullable=False),
        sa.Column("section_type", sa.String(length=16), nullable=False),
        sa.Column("is_main", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("semester_id", "section_id"),
    )
    op.create_index("idx_scheduler_sections_course", "scheduler_sections", ["course_id", "semester_id"])

    # scheduler_lectures
    op.create_table(
        "scheduler_lectures",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("semester_id", sa.String(length=32), nullable=False),
        sa.Column("section_id", sa.String(length=16), nullable=False),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Integer(), nullable=False),
        sa.Column("end_time", sa.Integer(), nullable=False),
        sa.Column("room", sa.String(length=255), nullable=False),
        sa.Column("instructor", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["semester_id", "section_id"],
            ["scheduler_sections.semester_id", "scheduler_sections.section_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("idx_scheduler_lectures_section", "scheduler_lectures", ["semester_id", "section_id"])

    # scheduler_map_components
    op.create_table(
        "scheduler_map_components",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("node_type", sa.Boolean(), nullable=True),
        sa.Column("x_coordinate", sa.Integer(), nullable=False),
        sa.Column("y_coordinate", sa.Integer(), nullable=False),
        sa.Column("category", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # scheduler_map_lines
    op.create_table(
        "scheduler_map_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("start_id", sa.String(length=255), sa.ForeignKey("scheduler_map_components.id", ondelete="CASCADE"), nullable=False),
        sa.Column("end_id", sa.String(length=255), sa.ForeignKey("scheduler_map_components.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_type", sa.Boolean(), nullable=True),
        sa.Column("x_coordinate", sa.Integer(), nullable=False),
        sa.Column("category", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # scheduler_user_course_carts
    op.create_table(
        "scheduler_user_course_carts",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("semester_id", sa.String(length=32), nullable=False),
        sa.Column("course_code", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "semester_id", "course_code"),
    )
    op.create_index("idx_scheduler_cart_user_semester", "scheduler_user_course_carts", ["user_id", "semester_id"])

    # scheduler_user_bundle_carts
    op.create_table(
        "scheduler_user_bundle_carts",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("semester_id", sa.String(length=32), nullable=False),
        sa.Column("course_code", sa.String(length=16), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("layer", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "semester_id", "course_code", "id", "layer"),
        sa.ForeignKeyConstraint(
            ["user_id", "semester_id", "course_code"],
            ["scheduler_user_course_carts.user_id", "scheduler_user_course_carts.semester_id", "scheduler_user_course_carts.course_code"],
            ondelete="CASCADE",
        ),
    )


def downgrade():
    op.drop_table("scheduler_user_bundle_carts")
    op.drop_table("scheduler_user_course_carts")
    op.drop_table("scheduler_map_lines")
    op.drop_table("scheduler_map_components")
    op.drop_table("scheduler_lectures")
    op.drop_table("scheduler_sections")
