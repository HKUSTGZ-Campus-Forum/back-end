"""add scheduler popularity time-series snapshots

Revision ID: 20260812_pop_history
Revises: 20260807_sched_popularity
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260812_pop_history"
down_revision = "20260807_sched_popularity"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "scheduler_popularity_snapshot_runs" not in tables:
        op.create_table(
            "scheduler_popularity_snapshot_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("semester_id", sa.String(length=16), nullable=False),
            sa.Column("bucket_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "completed_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "semester_id",
                "bucket_at",
                name="uq_scheduler_popularity_snapshot_run_bucket",
            ),
        )
        op.create_index(
            "idx_scheduler_popularity_snapshot_runs_semester_bucket",
            "scheduler_popularity_snapshot_runs",
            ["semester_id", "bucket_at"],
        )

    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "scheduler_popularity_course_snapshots" not in tables:
        op.create_table(
            "scheduler_popularity_course_snapshots",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=False),
            sa.Column("course_code", sa.String(length=32), nullable=False),
            sa.Column("display_course_code", sa.String(length=32), nullable=False),
            sa.Column("looking_count", sa.Integer(), nullable=False),
            sa.Column("scheduling_count", sa.Integer(), nullable=False),
            sa.CheckConstraint(
                "looking_count >= 0 AND scheduling_count >= 0",
                name="valid_scheduler_popularity_course_snapshot_counts",
            ),
            sa.ForeignKeyConstraint(
                ["run_id"],
                ["scheduler_popularity_snapshot_runs.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "run_id",
                "course_code",
                name="uq_scheduler_popularity_course_snapshot",
            ),
        )
        op.create_index(
            "idx_scheduler_popularity_course_snapshots_code_run",
            "scheduler_popularity_course_snapshots",
            ["course_code", "run_id"],
        )

    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "scheduler_popularity_section_snapshots" not in tables:
        op.create_table(
            "scheduler_popularity_section_snapshots",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=False),
            sa.Column("course_code", sa.String(length=32), nullable=False),
            sa.Column("display_course_code", sa.String(length=32), nullable=False),
            sa.Column("section_source_id", sa.String(length=32), nullable=False),
            sa.Column("looking_count", sa.Integer(), nullable=False),
            sa.Column("scheduling_count", sa.Integer(), nullable=False),
            sa.CheckConstraint(
                "looking_count >= 0 AND scheduling_count >= 0",
                name="valid_scheduler_popularity_section_snapshot_counts",
            ),
            sa.ForeignKeyConstraint(
                ["run_id"],
                ["scheduler_popularity_snapshot_runs.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "run_id",
                "course_code",
                "section_source_id",
                name="uq_scheduler_popularity_section_snapshot",
            ),
        )
        op.create_index(
            "idx_scheduler_popularity_section_snapshots_scope_run",
            "scheduler_popularity_section_snapshots",
            ["course_code", "section_source_id", "run_id"],
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table_name in (
        "scheduler_popularity_section_snapshots",
        "scheduler_popularity_course_snapshots",
        "scheduler_popularity_snapshot_runs",
    ):
        if table_name in tables:
            op.drop_table(table_name)
