"""add academic map tables

Revision ID: 20260529_academic_map
Revises:
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260529_academic_map"
down_revision = None
branch_labels = None
depends_on = None


def _json_type():
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade():
    op.create_table(
        "curriculum_programs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name_en", sa.String(length=255), nullable=False),
        sa.Column("name_zh", sa.String(length=255), nullable=True),
        sa.Column("cohort", sa.String(length=20), nullable=False),
        sa.Column("total_min_credits", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("common_core_min_credits", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("major_min_credits", sa.Integer(), nullable=True),
        sa.Column("home_areas", _json_type(), nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "cohort", name="uq_curriculum_program_code_cohort"),
    )
    op.create_index("idx_curriculum_programs_code_cohort", "curriculum_programs", ["code", "cohort"])

    op.create_table(
        "curriculum_requirement_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("name_en", sa.String(length=255), nullable=False),
        sa.Column("name_zh", sa.String(length=255), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("min_credits", sa.Integer(), nullable=True),
        sa.Column("min_courses", sa.Integer(), nullable=True),
        sa.Column("rule", _json_type(), nullable=False, server_default="{}"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["program_id"], ["curriculum_programs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("program_id", "key", name="uq_curriculum_requirement_group_key"),
    )

    op.create_table(
        "user_academic_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("cohort", sa.String(length=20), nullable=True),
        sa.Column("target_majors", _json_type(), nullable=False, server_default="[]"),
        sa.Column("grade_policy", sa.String(length=30), nullable=False, server_default="keep_private"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("idx_user_academic_profiles_user_id", "user_academic_profiles", ["user_id"])

    op.create_table(
        "user_course_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=True),
        sa.Column("course_code", sa.String(length=32), nullable=False),
        sa.Column("course_title", sa.String(length=255), nullable=True),
        sa.Column("term_label", sa.String(length=80), nullable=True),
        sa.Column("term_code", sa.String(length=20), nullable=True),
        sa.Column("units", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="completed"),
        sa.Column("grade", sa.String(length=12), nullable=True),
        sa.Column("keep_grade", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("import_source", sa.String(length=30), nullable=False, server_default="manual"),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("review_reason", sa.String(length=255), nullable=True),
        sa.Column("raw_payload", _json_type(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_user_course_records_user_id", "user_course_records", ["user_id"])
    op.create_index("idx_user_course_records_code", "user_course_records", ["course_code"])
    op.create_index("idx_user_course_records_status", "user_course_records", ["status"])


def downgrade():
    op.drop_index("idx_user_course_records_status", table_name="user_course_records")
    op.drop_index("idx_user_course_records_code", table_name="user_course_records")
    op.drop_index("idx_user_course_records_user_id", table_name="user_course_records")
    op.drop_table("user_course_records")
    op.drop_index("idx_user_academic_profiles_user_id", table_name="user_academic_profiles")
    op.drop_table("user_academic_profiles")
    op.drop_table("curriculum_requirement_groups")
    op.drop_index("idx_curriculum_programs_code_cohort", table_name="curriculum_programs")
    op.drop_table("curriculum_programs")
