"""Add saved scheduler plans and sharing.

Revision ID: 20260822_scheduler_plans
Revises: 20260822_sso_onboarding
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260822_scheduler_plans"
down_revision = "20260822_sso_onboarding"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "scheduler_plans" not in tables:
        op.create_table(
            "scheduler_plans",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("public_id", sa.String(length=36), nullable=False),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("semester_id", sa.String(length=16), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("description", sa.String(length=500), server_default="", nullable=False),
            sa.Column("visibility", sa.String(length=16), server_default="private", nullable=False),
            sa.Column("content_version", sa.Integer(), server_default="1", nullable=False),
            sa.Column(
                "private_constraints",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
            sa.Column("source_plan_id", sa.Integer(), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("content_version >= 1", name="valid_scheduler_plan_content_version"),
            sa.CheckConstraint(
                "visibility IN ('private', 'unlisted', 'public')",
                name="valid_scheduler_plan_visibility",
            ),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_plan_id"], ["scheduler_plans.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("public_id"),
        )
        op.create_index("idx_scheduler_plans_owner_updated", "scheduler_plans", ["owner_id", "updated_at"])
        op.create_index(
            "idx_scheduler_plans_public_semester_updated",
            "scheduler_plans",
            ["visibility", "semester_id", "updated_at"],
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "scheduler_plan_courses" not in tables:
        op.create_table(
            "scheduler_plan_courses",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("plan_id", sa.Integer(), nullable=False),
            sa.Column("offering_id", sa.Integer(), nullable=True),
            sa.Column("normalized_course_code", sa.String(length=32), nullable=False),
            sa.Column("display_order", sa.Integer(), nullable=False),
            sa.Column(
                "snapshot",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
            sa.CheckConstraint("display_order >= 0", name="valid_scheduler_plan_course_order"),
            sa.ForeignKeyConstraint(["offering_id"], ["course_offerings.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["plan_id"], ["scheduler_plans.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("plan_id", "offering_id", name="uq_scheduler_plan_course_offering"),
            sa.UniqueConstraint(
                "plan_id",
                "normalized_course_code",
                name="uq_scheduler_plan_course_code",
            ),
        )
        op.create_index("idx_scheduler_plan_courses_plan_order", "scheduler_plan_courses", ["plan_id", "display_order"])
        op.create_index("idx_scheduler_plan_courses_code", "scheduler_plan_courses", ["normalized_course_code"])

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "scheduler_plan_sections" not in tables:
        op.create_table(
            "scheduler_plan_sections",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("plan_course_id", sa.Integer(), nullable=False),
            sa.Column("section_id", sa.Integer(), nullable=True),
            sa.Column("source_section_id", sa.String(length=32), nullable=False),
            sa.Column("bundle", sa.Integer(), nullable=False),
            sa.Column("layer", sa.Integer(), nullable=False),
            sa.Column(
                "snapshot",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["plan_course_id"], ["scheduler_plan_courses.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["section_id"], ["course_sections.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "plan_course_id",
                "source_section_id",
                name="uq_scheduler_plan_section_source",
            ),
        )
        op.create_index(
            "idx_scheduler_plan_sections_course_layer",
            "scheduler_plan_sections",
            ["plan_course_id", "layer", "bundle"],
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "scheduler_plan_sections" in tables:
        op.drop_table("scheduler_plan_sections")
    if "scheduler_plan_courses" in tables:
        op.drop_table("scheduler_plan_courses")
    if "scheduler_plans" in tables:
        op.drop_table("scheduler_plans")
