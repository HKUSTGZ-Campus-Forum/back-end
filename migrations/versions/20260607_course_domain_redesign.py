"""add redesigned course domain tables

Revision ID: 20260607_course_domain
Revises: 20260531_scheduler_map_cart
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260607_course_domain"
down_revision = "20260531_scheduler_map_cart"
branch_labels = None
depends_on = None


def _json_type():
    return postgresql.JSONB(astext_type=sa.Text())


def _has_unique_constraint_or_index(inspector, table_name, expected_name, expected_columns):
    expected_columns = list(expected_columns)
    for constraint in inspector.get_unique_constraints(table_name):
        if constraint.get("name") == expected_name:
            return True
        if list(constraint.get("column_names") or []) == expected_columns:
            return True
    for index in inspector.get_indexes(table_name):
        if not index.get("unique"):
            continue
        if index.get("name") == expected_name:
            return True
        if list(index.get("column_names") or []) == expected_columns:
            return True
    return False


def upgrade():
    inspector = sa.inspect(op.get_bind())
    existing_course_columns = {
        column["name"]
        for column in inspector.get_columns("courses")
    }
    if "normalized_code" not in existing_course_columns:
        op.add_column("courses", sa.Column("normalized_code", sa.String(length=32), nullable=True))
    if "display_code" not in existing_course_columns:
        op.add_column("courses", sa.Column("display_code", sa.String(length=32), nullable=True))
    if "canonical_title" not in existing_course_columns:
        op.add_column("courses", sa.Column("canonical_title", sa.String(length=255), nullable=True))

    inspector = sa.inspect(op.get_bind())
    if not _has_unique_constraint_or_index(
        inspector,
        "courses",
        "uq_courses_normalized_code",
        ["normalized_code"],
    ):
        op.create_unique_constraint("uq_courses_normalized_code", "courses", ["normalized_code"])

    domain_tables = {
        "course_catalog_versions",
        "course_catalog_requirements",
        "course_requirement_edges",
        "course_offerings",
        "course_sections",
        "course_meetings",
        "user_course_attempts",
        "user_course_states",
        "user_offering_carts",
        "user_section_selections",
        "course_post_offering_targets",
    }
    if domain_tables.issubset(set(inspector.get_table_names())):
        return

    op.create_table(
        "course_catalog_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("source_version", sa.String(length=80), nullable=True),
        sa.Column("catalog_year", sa.String(length=20), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("title_abbr", sa.String(length=48), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("pre_requirement_raw", sa.Text(), nullable=True),
        sa.Column("co_requirement_raw", sa.Text(), nullable=True),
        sa.Column("exclusion_raw", sa.Text(), nullable=True),
        sa.Column("pg_course", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("klms_course", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("vector", sa.String(length=16), nullable=True),
        sa.Column("effective_from_semester_id", sa.String(length=16), nullable=True),
        sa.Column("effective_to_semester_id", sa.String(length=16), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_course_catalog_versions_course", "course_catalog_versions", ["course_id"])

    op.create_table(
        "course_catalog_requirements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("catalog_version_id", sa.Integer(), nullable=False),
        sa.Column("relation_type", sa.String(length=24), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("requirement_kind", sa.String(length=24), nullable=False, server_default="empty"),
        sa.Column("expression_json", _json_type(), nullable=False, server_default="{}"),
        sa.Column("parser_version", sa.String(length=40), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("relation_type IN ('prerequisite', 'corequisite', 'exclusion')", name="valid_course_requirement_relation_type"),
        sa.CheckConstraint("requirement_kind IN ('course', 'non_course', 'mixed', 'empty')", name="valid_course_requirement_kind"),
        sa.ForeignKeyConstraint(["catalog_version_id"], ["course_catalog_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_course_catalog_requirements_version", "course_catalog_requirements", ["catalog_version_id"])
    op.create_index("idx_course_catalog_requirements_relation", "course_catalog_requirements", ["relation_type"])

    op.create_table(
        "course_offerings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("semester_id", sa.String(length=16), nullable=False),
        sa.Column("catalog_version_id", sa.Integer(), nullable=True),
        sa.Column("offering_code", sa.String(length=32), nullable=False),
        sa.Column("title_snapshot", sa.String(length=255), nullable=False),
        sa.Column("credits_snapshot", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("import_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="offered"),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('offered', 'tentative', 'cancelled', 'archived')", name="valid_course_offering_status"),
        sa.ForeignKeyConstraint(["catalog_version_id"], ["course_catalog_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "semester_id", name="uq_course_offering_course_semester"),
    )
    op.create_index("idx_course_offerings_semester", "course_offerings", ["semester_id"])
    op.create_index("idx_course_offerings_course", "course_offerings", ["course_id"])

    op.create_table(
        "course_requirement_edges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Integer(), nullable=False),
        sa.Column("from_course_id", sa.Integer(), nullable=False),
        sa.Column("to_course_id", sa.Integer(), nullable=False),
        sa.Column("relation_type", sa.String(length=24), nullable=False),
        sa.Column("edge_role", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("relation_type IN ('prerequisite', 'corequisite', 'exclusion')", name="valid_course_requirement_edge_relation_type"),
        sa.ForeignKeyConstraint(["from_course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requirement_id"], ["course_catalog_requirements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_course_requirement_edges_from", "course_requirement_edges", ["from_course_id"])
    op.create_index("idx_course_requirement_edges_to", "course_requirement_edges", ["to_course_id"])
    op.create_index("idx_course_requirement_edges_requirement", "course_requirement_edges", ["requirement_id"])

    op.create_table(
        "course_sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("offering_id", sa.Integer(), nullable=False),
        sa.Column("source_section_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("section_type", sa.String(length=16), nullable=False),
        sa.Column("bundle", sa.Integer(), nullable=False),
        sa.Column("layer", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("quota", sa.Integer(), nullable=False),
        sa.Column("enrol", sa.Integer(), nullable=True),
        sa.Column("avail", sa.Integer(), nullable=True),
        sa.Column("wait", sa.Integer(), nullable=True),
        sa.Column("is_main", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["offering_id"], ["course_offerings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("offering_id", "source_section_id", name="uq_course_section_offering_source_section"),
    )
    op.create_index("idx_course_sections_offering", "course_sections", ["offering_id"])
    op.create_index("idx_course_sections_bundle_layer", "course_sections", ["offering_id", "bundle", "layer"])

    op.create_table(
        "course_meetings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Integer(), nullable=False),
        sa.Column("end_time", sa.Integer(), nullable=False),
        sa.Column("room", sa.String(length=255), nullable=False),
        sa.Column("instructor_text", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("day >= 1 AND day <= 7", name="valid_course_meeting_day"),
        sa.CheckConstraint("start_time >= 0 AND start_time <= 2359", name="valid_course_meeting_start_time"),
        sa.CheckConstraint("end_time >= 0 AND end_time <= 2359", name="valid_course_meeting_end_time"),
        sa.CheckConstraint("start_time < end_time", name="valid_course_meeting_time_order"),
        sa.ForeignKeyConstraint(["section_id"], ["course_sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_course_meetings_section", "course_meetings", ["section_id"])

    op.create_table(
        "user_course_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("offering_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("grade_letter", sa.String(length=12), nullable=True),
        sa.Column("grade_points", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("term_label", sa.String(length=80), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("raw_payload", _json_type(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('in_progress', 'completed', 'failed', 'withdrawn')", name="valid_user_course_attempt_status"),
        sa.CheckConstraint("source IN ('transcript_import', 'manual')", name="valid_user_course_attempt_source"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["offering_id"], ["course_offerings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_user_course_attempts_user_course", "user_course_attempts", ["user_id", "course_id"])
    op.create_index("idx_user_course_attempts_user_offering", "user_course_attempts", ["user_id", "offering_id"])

    op.create_table(
        "user_course_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("best_attempt_id", sa.Integer(), nullable=True),
        sa.Column("best_grade_points", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("best_grade_letter", sa.String(length=12), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="derived"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('not_taken', 'interested', 'in_progress', 'completed')", name="valid_user_course_state_status"),
        sa.CheckConstraint("source IN ('derived', 'manual', 'import')", name="valid_user_course_state_source"),
        sa.ForeignKeyConstraint(["best_attempt_id"], ["user_course_attempts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "course_id", name="uq_user_course_state"),
    )
    op.create_index("idx_user_course_states_user", "user_course_states", ["user_id"])
    op.create_index("idx_user_course_states_course", "user_course_states", ["course_id"])

    op.create_table(
        "user_offering_carts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("offering_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["offering_id"], ["course_offerings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "offering_id", name="uq_user_offering_cart"),
    )
    op.create_index("idx_user_offering_carts_user", "user_offering_carts", ["user_id"])

    op.create_table(
        "user_section_selections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("offering_id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source IN ('cart', 'final', 'import')", name="valid_user_section_selection_source"),
        sa.ForeignKeyConstraint(["offering_id"], ["course_offerings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["section_id"], ["course_sections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "offering_id", "section_id", name="uq_user_section_selection"),
    )
    op.create_index("idx_user_section_selections_user_offering", "user_section_selections", ["user_id", "offering_id"])

    op.create_table(
        "course_post_offering_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("course_offering_id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=True),
        sa.Column("instructor_text", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["course_offering_id"], ["course_offerings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["section_id"], ["course_sections.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", name="uq_course_post_offering_target_post"),
    )
    op.create_index("idx_course_post_offering_targets_offering", "course_post_offering_targets", ["course_offering_id"])


def downgrade():
    op.drop_index("idx_course_post_offering_targets_offering", table_name="course_post_offering_targets")
    op.drop_table("course_post_offering_targets")
    op.drop_index("idx_user_section_selections_user_offering", table_name="user_section_selections")
    op.drop_table("user_section_selections")
    op.drop_index("idx_user_offering_carts_user", table_name="user_offering_carts")
    op.drop_table("user_offering_carts")
    op.drop_index("idx_user_course_states_course", table_name="user_course_states")
    op.drop_index("idx_user_course_states_user", table_name="user_course_states")
    op.drop_table("user_course_states")
    op.drop_index("idx_user_course_attempts_user_offering", table_name="user_course_attempts")
    op.drop_index("idx_user_course_attempts_user_course", table_name="user_course_attempts")
    op.drop_table("user_course_attempts")
    op.drop_index("idx_course_meetings_section", table_name="course_meetings")
    op.drop_table("course_meetings")
    op.drop_index("idx_course_sections_bundle_layer", table_name="course_sections")
    op.drop_index("idx_course_sections_offering", table_name="course_sections")
    op.drop_table("course_sections")
    op.drop_index("idx_course_requirement_edges_requirement", table_name="course_requirement_edges")
    op.drop_index("idx_course_requirement_edges_to", table_name="course_requirement_edges")
    op.drop_index("idx_course_requirement_edges_from", table_name="course_requirement_edges")
    op.drop_table("course_requirement_edges")
    op.drop_index("idx_course_offerings_course", table_name="course_offerings")
    op.drop_index("idx_course_offerings_semester", table_name="course_offerings")
    op.drop_table("course_offerings")
    op.drop_index("idx_course_catalog_requirements_relation", table_name="course_catalog_requirements")
    op.drop_index("idx_course_catalog_requirements_version", table_name="course_catalog_requirements")
    op.drop_table("course_catalog_requirements")
    op.drop_index("idx_course_catalog_versions_course", table_name="course_catalog_versions")
    op.drop_table("course_catalog_versions")
    op.drop_constraint("uq_courses_normalized_code", "courses", type_="unique")
    op.drop_column("courses", "canonical_title")
    op.drop_column("courses", "display_code")
    op.drop_column("courses", "normalized_code")
