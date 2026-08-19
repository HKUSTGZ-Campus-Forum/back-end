"""Add SISN live-sync metadata and audit ledger.

Revision ID: 20260819_sisn_automation
Revises: 20260819_campus_oidc
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260819_sisn_automation"
down_revision = "20260819_campus_oidc"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "course_catalog_versions",
        sa.Column(
            "source_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "course_sections",
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
    )
    op.add_column("course_sections", sa.Column("source_class_type", sa.String(length=16)))
    op.add_column("course_sections", sa.Column("source_section_label", sa.String(length=64)))
    op.add_column("course_sections", sa.Column("associated_class", sa.Integer()))
    op.add_column(
        "course_sections",
        sa.Column("consent_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("course_sections", sa.Column("remarks", sa.Text()))
    op.add_column(
        "course_sections",
        sa.Column(
            "reserve_cap",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "valid_course_section_status",
        "course_sections",
        "status IN ('active', 'cancelled')",
    )
    op.create_index(
        "idx_course_sections_offering_status",
        "course_sections",
        ["offering_id", "status"],
    )

    op.add_column("course_meetings", sa.Column("facility_id", sa.String(length=64)))
    op.add_column(
        "course_meetings",
        sa.Column(
            "date_ranges",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    op.create_table(
        "sisn_sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("semester_id", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("source_payload_sha256", sa.String(length=64)),
        sa.Column("candidate_sha256", sa.String(length=64)),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.Column(
            "counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "plan",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("mode IN ('dry-run', 'apply')", name="valid_sisn_sync_run_mode"),
        sa.CheckConstraint(
            "status IN ('started', 'dry-run', 'applied', 'skipped', 'blocked', 'failed')",
            name="valid_sisn_sync_run_status",
        ),
    )
    op.create_index(
        "idx_sisn_sync_runs_semester_started",
        "sisn_sync_runs",
        ["semester_id", "started_at"],
    )
    op.create_index(
        "idx_sisn_sync_runs_source_hash",
        "sisn_sync_runs",
        ["source_payload_sha256"],
    )


def downgrade():
    op.drop_index("idx_sisn_sync_runs_source_hash", table_name="sisn_sync_runs")
    op.drop_index("idx_sisn_sync_runs_semester_started", table_name="sisn_sync_runs")
    op.drop_table("sisn_sync_runs")

    op.drop_column("course_meetings", "date_ranges")
    op.drop_column("course_meetings", "facility_id")

    op.drop_index("idx_course_sections_offering_status", table_name="course_sections")
    op.drop_constraint("valid_course_section_status", "course_sections", type_="check")
    op.drop_column("course_sections", "reserve_cap")
    op.drop_column("course_sections", "remarks")
    op.drop_column("course_sections", "consent_required")
    op.drop_column("course_sections", "associated_class")
    op.drop_column("course_sections", "source_section_label")
    op.drop_column("course_sections", "source_class_type")
    op.drop_column("course_sections", "status")
    op.drop_column("course_catalog_versions", "source_metadata")
