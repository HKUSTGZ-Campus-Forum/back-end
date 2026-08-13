"""add truth metadata to scheduler popularity samples

Revision ID: 20260813_pop_history_truth
Revises: 20260812_pop_history
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260813_pop_history_truth"
down_revision = "20260812_pop_history"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"]
        for column in inspector.get_columns("scheduler_popularity_snapshot_runs")
    }
    # This migration is intentionally fail-closed for existing sample rows.
    # Legacy sparse rows did not record a universe or actual observation time,
    # so backfilling them would manufacture facts. Production must apply this
    # before the first history sample is taken.
    existing_rows = bind.execute(sa.text(
        "SELECT count(*) FROM scheduler_popularity_snapshot_runs"
    )).scalar_one()
    if existing_rows:
        raise RuntimeError(
            "cannot add popularity truth metadata after sampling started; "
            "legacy rows have no provable observation time or universe"
        )

    additions = (
        ("observed_at", sa.DateTime(timezone=True)),
        ("universe_sha256", sa.String(length=64)),
        ("universe_course_count", sa.Integer()),
        ("universe_section_count", sa.Integer()),
        ("universe_meeting_count", sa.Integer()),
    )
    for name, column_type in additions:
        if name not in columns:
            op.add_column(
                "scheduler_popularity_snapshot_runs",
                sa.Column(name, column_type, nullable=True),
            )

    inspector = sa.inspect(bind)
    columns = {
        column["name"]: column
        for column in inspector.get_columns("scheduler_popularity_snapshot_runs")
    }
    for name, _column_type in additions:
        if columns[name]["nullable"] is False:
            continue
        op.alter_column(
            "scheduler_popularity_snapshot_runs",
            name,
            existing_nullable=True,
            nullable=False,
        )
    checks = {
        constraint["name"]
        for constraint in sa.inspect(bind).get_check_constraints(
            "scheduler_popularity_snapshot_runs"
        )
    }
    if "valid_scheduler_popularity_universe_sha256" not in checks:
        op.create_check_constraint(
            "valid_scheduler_popularity_universe_sha256",
            "scheduler_popularity_snapshot_runs",
            "length(universe_sha256) = 64",
        )
    if "valid_scheduler_popularity_universe_counts" not in checks:
        op.create_check_constraint(
            "valid_scheduler_popularity_universe_counts",
            "scheduler_popularity_snapshot_runs",
            "universe_course_count >= 0 AND universe_section_count >= 0 "
            "AND universe_meeting_count >= 0",
        )


def downgrade():
    op.drop_constraint(
        "valid_scheduler_popularity_universe_counts",
        "scheduler_popularity_snapshot_runs",
        type_="check",
    )
    op.drop_constraint(
        "valid_scheduler_popularity_universe_sha256",
        "scheduler_popularity_snapshot_runs",
        type_="check",
    )
    for name in (
        "universe_meeting_count",
        "universe_section_count",
        "universe_course_count",
        "universe_sha256",
        "observed_at",
    ):
        op.drop_column("scheduler_popularity_snapshot_runs", name)
