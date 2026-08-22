from datetime import datetime, timezone
import uuid

from sqlalchemy.dialects.postgresql import JSONB

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class SchedulerPlan(db.Model):
    __tablename__ = "scheduler_plans"

    VISIBILITY_PRIVATE = "private"
    VISIBILITY_UNLISTED = "unlisted"
    VISIBILITY_PUBLIC = "public"
    VISIBILITIES = {VISIBILITY_PRIVATE, VISIBILITY_UNLISTED, VISIBILITY_PUBLIC}

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(
        db.String(36),
        nullable=False,
        unique=True,
        default=lambda: str(uuid.uuid4()),
    )
    owner_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    semester_id = db.Column(db.String(16), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(500), nullable=False, default="")
    visibility = db.Column(db.String(16), nullable=False, default=VISIBILITY_PRIVATE)
    content_version = db.Column(db.Integer, nullable=False, default=1)
    private_constraints = db.Column(JSONB, nullable=False, default=dict)
    source_plan_id = db.Column(
        db.Integer,
        db.ForeignKey("scheduler_plans.id", ondelete="SET NULL"),
        nullable=True,
    )
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_deleted = db.Column(db.Boolean, nullable=False, default=False)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    owner = db.relationship(
        "User",
        backref=db.backref("scheduler_plans", lazy="dynamic", cascade="all, delete-orphan"),
    )
    source_plan = db.relationship("SchedulerPlan", remote_side=[id], uselist=False)
    courses = db.relationship(
        "SchedulerPlanCourse",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="SchedulerPlanCourse.display_order",
    )

    __table_args__ = (
        db.CheckConstraint(
            "visibility IN ('private', 'unlisted', 'public')",
            name="valid_scheduler_plan_visibility",
        ),
        db.CheckConstraint("content_version >= 1", name="valid_scheduler_plan_content_version"),
        db.Index("idx_scheduler_plans_owner_updated", "owner_id", "updated_at"),
        db.Index(
            "idx_scheduler_plans_public_semester_updated",
            "visibility",
            "semester_id",
            "updated_at",
        ),
    )


class SchedulerPlanCourse(db.Model):
    __tablename__ = "scheduler_plan_courses"

    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(
        db.Integer,
        db.ForeignKey("scheduler_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    offering_id = db.Column(
        db.Integer,
        db.ForeignKey("course_offerings.id", ondelete="SET NULL"),
        nullable=True,
    )
    normalized_course_code = db.Column(db.String(32), nullable=False)
    display_order = db.Column(db.Integer, nullable=False)
    snapshot = db.Column(JSONB, nullable=False, default=dict)

    plan = db.relationship("SchedulerPlan", back_populates="courses")
    offering = db.relationship("CourseOffering")
    sections = db.relationship(
        "SchedulerPlanSection",
        back_populates="plan_course",
        cascade="all, delete-orphan",
        order_by=lambda: (
            SchedulerPlanSection.layer,
            SchedulerPlanSection.bundle,
            SchedulerPlanSection.id,
        ),
    )

    __table_args__ = (
        db.UniqueConstraint("plan_id", "offering_id", name="uq_scheduler_plan_course_offering"),
        db.UniqueConstraint(
            "plan_id",
            "normalized_course_code",
            name="uq_scheduler_plan_course_code",
        ),
        db.CheckConstraint("display_order >= 0", name="valid_scheduler_plan_course_order"),
        db.Index("idx_scheduler_plan_courses_plan_order", "plan_id", "display_order"),
        db.Index("idx_scheduler_plan_courses_code", "normalized_course_code"),
    )


class SchedulerPlanSection(db.Model):
    __tablename__ = "scheduler_plan_sections"

    id = db.Column(db.Integer, primary_key=True)
    plan_course_id = db.Column(
        db.Integer,
        db.ForeignKey("scheduler_plan_courses.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id = db.Column(
        db.Integer,
        db.ForeignKey("course_sections.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_section_id = db.Column(db.String(32), nullable=False)
    bundle = db.Column(db.Integer, nullable=False)
    layer = db.Column(db.Integer, nullable=False)
    snapshot = db.Column(JSONB, nullable=False, default=dict)

    plan_course = db.relationship("SchedulerPlanCourse", back_populates="sections")
    section = db.relationship("CourseSection")

    __table_args__ = (
        db.UniqueConstraint(
            "plan_course_id",
            "source_section_id",
            name="uq_scheduler_plan_section_source",
        ),
        db.Index(
            "idx_scheduler_plan_sections_course_layer",
            "plan_course_id",
            "layer",
            "bundle",
        ),
    )
