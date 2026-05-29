from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import JSONB

from app.extensions import db


class CurriculumProgram(db.Model):
    __tablename__ = "curriculum_programs"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), nullable=False)
    name_en = db.Column(db.String(255), nullable=False)
    name_zh = db.Column(db.String(255), nullable=True)
    cohort = db.Column(db.String(20), nullable=False)
    total_min_credits = db.Column(db.Integer, default=120, nullable=False)
    common_core_min_credits = db.Column(db.Integer, default=30, nullable=False)
    major_min_credits = db.Column(db.Integer, nullable=True)
    home_areas = db.Column(JSONB, default=list, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint("code", "cohort", name="uq_curriculum_program_code_cohort"),
        db.Index("idx_curriculum_programs_code_cohort", "code", "cohort"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "name_en": self.name_en,
            "name_zh": self.name_zh,
            "cohort": self.cohort,
            "total_min_credits": self.total_min_credits,
            "common_core_min_credits": self.common_core_min_credits,
            "major_min_credits": self.major_min_credits,
            "home_areas": self.home_areas or [],
            "is_active": self.is_active,
        }


class CurriculumRequirementGroup(db.Model):
    __tablename__ = "curriculum_requirement_groups"

    id = db.Column(db.Integer, primary_key=True)
    program_id = db.Column(db.Integer, db.ForeignKey("curriculum_programs.id"), nullable=False)
    key = db.Column(db.String(80), nullable=False)
    name_en = db.Column(db.String(255), nullable=False)
    name_zh = db.Column(db.String(255), nullable=True)
    category = db.Column(db.String(40), nullable=False)
    min_credits = db.Column(db.Integer, nullable=True)
    min_courses = db.Column(db.Integer, nullable=True)
    rule = db.Column(JSONB, default=dict, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    program = db.relationship(
        "CurriculumProgram",
        backref=db.backref("requirement_groups", lazy="dynamic", cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.UniqueConstraint("program_id", "key", name="uq_curriculum_requirement_group_key"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "program_id": self.program_id,
            "key": self.key,
            "name_en": self.name_en,
            "name_zh": self.name_zh,
            "category": self.category,
            "min_credits": self.min_credits,
            "min_courses": self.min_courses,
            "rule": self.rule or {},
            "sort_order": self.sort_order,
        }


class UserAcademicProfile(db.Model):
    __tablename__ = "user_academic_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    cohort = db.Column(db.String(20), nullable=True)
    target_majors = db.Column(JSONB, default=list, nullable=False)
    grade_policy = db.Column(db.String(30), default="keep_private", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user = db.relationship(
        "User",
        backref=db.backref("academic_profile", uselist=False, cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.Index("idx_user_academic_profiles_user_id", "user_id"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "cohort": self.cohort,
            "target_majors": self.target_majors or [],
            "grade_policy": self.grade_policy,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def get_or_create_for_user(cls, user_id):
        profile = cls.query.filter_by(user_id=user_id).first()
        if profile is None:
            profile = cls(user_id=user_id)
            db.session.add(profile)
            db.session.flush()
        return profile


class UserCourseRecord(db.Model):
    __tablename__ = "user_course_records"

    STATUS_COMPLETED = "completed"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_PLANNED = "planned"
    STATUS_INTERESTED = "interested"
    STATUS_NOT_INTERESTED = "not_interested"

    SOURCE_PASTE = "paste"
    SOURCE_SCREENSHOT = "screenshot"
    SOURCE_CHECKLIST = "checklist"
    SOURCE_MANUAL = "manual"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=True)
    course_code = db.Column(db.String(32), nullable=False)
    course_title = db.Column(db.String(255), nullable=True)
    term_label = db.Column(db.String(80), nullable=True)
    term_code = db.Column(db.String(20), nullable=True)
    units = db.Column(db.Numeric(5, 2), nullable=True)
    status = db.Column(db.String(30), default=STATUS_COMPLETED, nullable=False)
    grade = db.Column(db.String(12), nullable=True)
    keep_grade = db.Column(db.Boolean, default=False, nullable=False)
    import_source = db.Column(db.String(30), default=SOURCE_MANUAL, nullable=False)
    needs_review = db.Column(db.Boolean, default=False, nullable=False)
    review_reason = db.Column(db.String(255), nullable=True)
    raw_payload = db.Column(JSONB, default=dict, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user = db.relationship(
        "User",
        backref=db.backref("academic_course_records", lazy="dynamic", cascade="all, delete-orphan"),
    )
    course = db.relationship("Course", backref=db.backref("academic_user_records", lazy="dynamic"))

    __table_args__ = (
        db.Index("idx_user_course_records_user_id", "user_id"),
        db.Index("idx_user_course_records_code", "course_code"),
        db.Index("idx_user_course_records_status", "status"),
    )

    def to_dict(self, include_grade=False):
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "course_id": self.course_id,
            "course_code": self.course_code,
            "course_title": self.course_title,
            "term_label": self.term_label,
            "term_code": self.term_code,
            "units": float(self.units) if self.units is not None else None,
            "status": self.status,
            "keep_grade": self.keep_grade,
            "import_source": self.import_source,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_grade and self.keep_grade:
            data["grade"] = self.grade
        return data
