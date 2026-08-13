from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class SchedulerPopularityEvent(db.Model):
    """Anonymous transition log for scheduler popularity trends.

    Current popularity is always computed from cart and section-selection state.
    These events intentionally contain no user identifier and are not exposed by
    a public API.
    """

    __tablename__ = "scheduler_popularity_events"

    id = db.Column(db.Integer, primary_key=True)
    offering_id = db.Column(
        db.Integer,
        db.ForeignKey("course_offerings.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id = db.Column(
        db.Integer,
        db.ForeignKey("course_sections.id", ondelete="SET NULL"),
        nullable=True,
    )
    section_source_id = db.Column(db.String(32), nullable=True)
    from_state = db.Column(db.String(16), nullable=True)
    to_state = db.Column(db.String(16), nullable=True)
    reason = db.Column(db.String(32), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    offering = db.relationship("CourseOffering", backref=db.backref("popularity_events", lazy="dynamic"))
    section = db.relationship("CourseSection", backref=db.backref("popularity_events", lazy="dynamic"))

    __table_args__ = (
        db.CheckConstraint(
            "from_state IS NULL OR from_state IN ('looking', 'scheduling')",
            name="valid_scheduler_popularity_from_state",
        ),
        db.CheckConstraint(
            "to_state IS NULL OR to_state IN ('looking', 'scheduling')",
            name="valid_scheduler_popularity_to_state",
        ),
        db.CheckConstraint(
            "(from_state IS NOT NULL OR to_state IS NOT NULL) "
            "AND (from_state IS NULL OR to_state IS NULL OR from_state <> to_state)",
            name="valid_scheduler_popularity_transition",
        ),
        db.CheckConstraint(
            "reason IN ('cart_added', 'cart_removed', 'course_toggled', "
            "'bundle_toggled', 'layer_toggled')",
            name="valid_scheduler_popularity_reason",
        ),
        db.Index(
            "idx_scheduler_popularity_offering_created",
            "offering_id",
            "created_at",
        ),
        db.Index(
            "idx_scheduler_popularity_section_created",
            "section_id",
            "created_at",
        ),
        db.Index(
            "idx_scheduler_popularity_source_created",
            "offering_id",
            "section_source_id",
            "created_at",
        ),
    )


class SchedulerPopularitySnapshotRun(db.Model):
    """A successfully completed popularity sample.

    ``bucket_at`` is the scheduled slot while ``observed_at`` is when the
    database state was actually observed.  Facts are intentionally sparse,
    but an absent fact means zero only because every run records the exact
    immutable course/section/meeting universe it covered.  If the run itself
    is absent, the sample is missing and consumers must render a gap.
    """

    __tablename__ = "scheduler_popularity_snapshot_runs"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    semester_id = db.Column(db.String(16), nullable=False)
    bucket_at = db.Column(db.DateTime(timezone=True), nullable=False)
    observed_at = db.Column(db.DateTime(timezone=True), nullable=False)
    universe_sha256 = db.Column(db.String(64), nullable=False)
    universe_course_count = db.Column(db.Integer, nullable=False)
    universe_section_count = db.Column(db.Integer, nullable=False)
    universe_meeting_count = db.Column(db.Integer, nullable=False)
    completed_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint(
            "semester_id",
            "bucket_at",
            name="uq_scheduler_popularity_snapshot_run_bucket",
        ),
        db.Index(
            "idx_scheduler_popularity_snapshot_runs_semester_bucket",
            "semester_id",
            "bucket_at",
        ),
        db.CheckConstraint(
            "length(universe_sha256) = 64",
            name="valid_scheduler_popularity_universe_sha256",
        ),
        db.CheckConstraint(
            "universe_course_count >= 0 AND universe_section_count >= 0 "
            "AND universe_meeting_count >= 0",
            name="valid_scheduler_popularity_universe_counts",
        ),
    )


class SchedulerPopularityCourseSnapshot(db.Model):
    """Sparse anonymous course-level counts for one completed sample."""

    __tablename__ = "scheduler_popularity_course_snapshots"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_id = db.Column(
        db.Integer,
        db.ForeignKey("scheduler_popularity_snapshot_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    course_code = db.Column(db.String(32), nullable=False)
    display_course_code = db.Column(db.String(32), nullable=False)
    looking_count = db.Column(db.Integer, nullable=False)
    scheduling_count = db.Column(db.Integer, nullable=False)

    run = db.relationship(
        "SchedulerPopularitySnapshotRun",
        backref=db.backref("course_snapshots", cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "run_id",
            "course_code",
            name="uq_scheduler_popularity_course_snapshot",
        ),
        db.CheckConstraint(
            "looking_count >= 0 AND scheduling_count >= 0",
            name="valid_scheduler_popularity_course_snapshot_counts",
        ),
        db.Index(
            "idx_scheduler_popularity_course_snapshots_code_run",
            "course_code",
            "run_id",
        ),
    )


class SchedulerPopularitySectionSnapshot(db.Model):
    """Sparse anonymous section-level counts for one completed sample."""

    __tablename__ = "scheduler_popularity_section_snapshots"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    run_id = db.Column(
        db.Integer,
        db.ForeignKey("scheduler_popularity_snapshot_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    course_code = db.Column(db.String(32), nullable=False)
    display_course_code = db.Column(db.String(32), nullable=False)
    section_source_id = db.Column(db.String(32), nullable=False)
    looking_count = db.Column(db.Integer, nullable=False)
    scheduling_count = db.Column(db.Integer, nullable=False)

    run = db.relationship(
        "SchedulerPopularitySnapshotRun",
        backref=db.backref("section_snapshots", cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "run_id",
            "course_code",
            "section_source_id",
            name="uq_scheduler_popularity_section_snapshot",
        ),
        db.CheckConstraint(
            "looking_count >= 0 AND scheduling_count >= 0",
            name="valid_scheduler_popularity_section_snapshot_counts",
        ),
        db.Index(
            "idx_scheduler_popularity_section_snapshots_scope_run",
            "course_code",
            "section_source_id",
            "run_id",
        ),
    )
