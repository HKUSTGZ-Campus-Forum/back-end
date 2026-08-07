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
