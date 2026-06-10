from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import JSONB

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class CourseCatalogVersion(db.Model):
    __tablename__ = "course_catalog_versions"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    source = db.Column(db.String(80), nullable=False)
    source_version = db.Column(db.String(80), nullable=True)
    catalog_year = db.Column(db.String(20), nullable=True)
    title = db.Column(db.String(255), nullable=False)
    title_abbr = db.Column(db.String(48), nullable=True)
    description = db.Column(db.Text, nullable=True)
    credits = db.Column(db.Integer, nullable=False)
    pre_requirement_raw = db.Column(db.Text, nullable=True)
    co_requirement_raw = db.Column(db.Text, nullable=True)
    exclusion_raw = db.Column(db.Text, nullable=True)
    pg_course = db.Column(db.Boolean, default=False, nullable=False)
    klms_course = db.Column(db.Boolean, default=False, nullable=False)
    vector = db.Column(db.String(16), nullable=True)
    effective_from_semester_id = db.Column(db.String(16), nullable=True)
    effective_to_semester_id = db.Column(db.String(16), nullable=True)
    imported_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    course = db.relationship(
        "Course",
        backref=db.backref("catalog_versions", lazy="dynamic", cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.Index("idx_course_catalog_versions_course", "course_id"),
    )


class CourseCatalogRequirement(db.Model):
    __tablename__ = "course_catalog_requirements"

    id = db.Column(db.Integer, primary_key=True)
    catalog_version_id = db.Column(db.Integer, db.ForeignKey("course_catalog_versions.id", ondelete="CASCADE"), nullable=False)
    relation_type = db.Column(db.String(24), nullable=False)
    raw_text = db.Column(db.Text, nullable=True)
    normalized_text = db.Column(db.Text, nullable=True)
    requirement_kind = db.Column(db.String(24), default="empty", nullable=False)
    expression_json = db.Column(JSONB, default=dict, nullable=False)
    parser_version = db.Column(db.String(40), nullable=True)
    source = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    catalog_version = db.relationship(
        "CourseCatalogVersion",
        backref=db.backref("requirements", lazy="dynamic", cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.CheckConstraint(
            "relation_type IN ('prerequisite', 'corequisite', 'exclusion')",
            name="valid_course_requirement_relation_type",
        ),
        db.CheckConstraint(
            "requirement_kind IN ('course', 'non_course', 'mixed', 'empty')",
            name="valid_course_requirement_kind",
        ),
        db.Index("idx_course_catalog_requirements_version", "catalog_version_id"),
        db.Index("idx_course_catalog_requirements_relation", "relation_type"),
    )


class CourseRequirementEdge(db.Model):
    __tablename__ = "course_requirement_edges"

    id = db.Column(db.Integer, primary_key=True)
    requirement_id = db.Column(db.Integer, db.ForeignKey("course_catalog_requirements.id", ondelete="CASCADE"), nullable=False)
    from_course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    to_course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    relation_type = db.Column(db.String(24), nullable=False)
    edge_role = db.Column(db.String(40), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    requirement = db.relationship(
        "CourseCatalogRequirement",
        backref=db.backref("edges", lazy="dynamic", cascade="all, delete-orphan"),
    )
    from_course = db.relationship("Course", foreign_keys=[from_course_id], backref=db.backref("outgoing_requirement_edges", lazy="dynamic"))
    to_course = db.relationship("Course", foreign_keys=[to_course_id], backref=db.backref("incoming_requirement_edges", lazy="dynamic"))

    __table_args__ = (
        db.CheckConstraint(
            "relation_type IN ('prerequisite', 'corequisite', 'exclusion')",
            name="valid_course_requirement_edge_relation_type",
        ),
        db.Index("idx_course_requirement_edges_from", "from_course_id"),
        db.Index("idx_course_requirement_edges_to", "to_course_id"),
        db.Index("idx_course_requirement_edges_requirement", "requirement_id"),
    )


class CourseOffering(db.Model):
    __tablename__ = "course_offerings"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    semester_id = db.Column(db.String(16), nullable=False)
    catalog_version_id = db.Column(db.Integer, db.ForeignKey("course_catalog_versions.id", ondelete="SET NULL"), nullable=True)
    offering_code = db.Column(db.String(32), nullable=False)
    title_snapshot = db.Column(db.String(255), nullable=False)
    credits_snapshot = db.Column(db.Integer, nullable=False)
    source = db.Column(db.String(80), nullable=False)
    import_hash = db.Column(db.String(128), nullable=True)
    status = db.Column(db.String(24), default="offered", nullable=False)
    imported_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    course = db.relationship("Course", backref=db.backref("offerings", lazy="dynamic", cascade="all, delete-orphan"))
    catalog_version = db.relationship("CourseCatalogVersion", backref=db.backref("offerings", lazy="dynamic"))

    __table_args__ = (
        db.UniqueConstraint("course_id", "semester_id", name="uq_course_offering_course_semester"),
        db.CheckConstraint(
            "status IN ('offered', 'tentative', 'cancelled', 'archived')",
            name="valid_course_offering_status",
        ),
        db.Index("idx_course_offerings_semester", "semester_id"),
        db.Index("idx_course_offerings_course", "course_id"),
    )


class CourseSection(db.Model):
    __tablename__ = "course_sections"

    id = db.Column(db.Integer, primary_key=True)
    offering_id = db.Column(db.Integer, db.ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False)
    source_section_id = db.Column(db.String(32), nullable=False)
    name = db.Column(db.String(256), nullable=False)
    section_type = db.Column(db.String(16), nullable=False)
    bundle = db.Column(db.Integer, nullable=False)
    layer = db.Column(db.Integer, default=0, nullable=False)
    quota = db.Column(db.Integer, nullable=False)
    enrol = db.Column(db.Integer, nullable=True)
    avail = db.Column(db.Integer, nullable=True)
    wait = db.Column(db.Integer, nullable=True)
    is_main = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    offering = db.relationship(
        "CourseOffering",
        backref=db.backref("sections", lazy="dynamic", cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.UniqueConstraint("offering_id", "source_section_id", name="uq_course_section_offering_source_section"),
        db.Index("idx_course_sections_offering", "offering_id"),
        db.Index("idx_course_sections_bundle_layer", "offering_id", "bundle", "layer"),
    )


class CourseMeeting(db.Model):
    __tablename__ = "course_meetings"

    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey("course_sections.id", ondelete="CASCADE"), nullable=False)
    day = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.Integer, nullable=False)
    end_time = db.Column(db.Integer, nullable=False)
    room = db.Column(db.String(255), nullable=False)
    instructor_text = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    section = db.relationship(
        "CourseSection",
        backref=db.backref("meetings", lazy="dynamic", cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.CheckConstraint("day >= 1 AND day <= 7", name="valid_course_meeting_day"),
        db.CheckConstraint("start_time >= 0 AND start_time <= 2359", name="valid_course_meeting_start_time"),
        db.CheckConstraint("end_time >= 0 AND end_time <= 2359", name="valid_course_meeting_end_time"),
        db.CheckConstraint("start_time < end_time", name="valid_course_meeting_time_order"),
        db.Index("idx_course_meetings_section", "section_id"),
    )


class UserCourseState(db.Model):
    __tablename__ = "user_course_states"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    status = db.Column(db.String(30), nullable=False)
    best_attempt_id = db.Column(db.Integer, db.ForeignKey("user_course_attempts.id", ondelete="SET NULL"), nullable=True)
    best_grade_points = db.Column(db.Numeric(3, 2), nullable=True)
    best_grade_letter = db.Column(db.String(12), nullable=True)
    source = db.Column(db.String(30), nullable=False, default="derived")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("course_states", lazy="dynamic", cascade="all, delete-orphan"))
    course = db.relationship("Course", backref=db.backref("user_states", lazy="dynamic", cascade="all, delete-orphan"))
    best_attempt = db.relationship("UserCourseAttempt", foreign_keys=[best_attempt_id], post_update=True)

    __table_args__ = (
        db.UniqueConstraint("user_id", "course_id", name="uq_user_course_state"),
        db.CheckConstraint(
            "status IN ('not_taken', 'interested', 'in_progress', 'completed')",
            name="valid_user_course_state_status",
        ),
        db.CheckConstraint(
            "source IN ('derived', 'manual', 'import')",
            name="valid_user_course_state_source",
        ),
        db.Index("idx_user_course_states_user", "user_id"),
        db.Index("idx_user_course_states_course", "course_id"),
    )


class UserCourseAttempt(db.Model):
    __tablename__ = "user_course_attempts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    offering_id = db.Column(db.Integer, db.ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False)
    status = db.Column(db.String(30), nullable=False)
    grade_letter = db.Column(db.String(12), nullable=True)
    grade_points = db.Column(db.Numeric(3, 2), nullable=True)
    term_label = db.Column(db.String(80), nullable=True)
    source = db.Column(db.String(30), nullable=False)
    raw_payload = db.Column(JSONB, default=dict, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("course_attempts", lazy="dynamic", cascade="all, delete-orphan"))
    course = db.relationship("Course", backref=db.backref("user_attempts", lazy="dynamic", cascade="all, delete-orphan"))
    offering = db.relationship("CourseOffering", backref=db.backref("user_attempts", lazy="dynamic", cascade="all, delete-orphan"))

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('in_progress', 'completed', 'failed', 'withdrawn')",
            name="valid_user_course_attempt_status",
        ),
        db.CheckConstraint(
            "source IN ('transcript_import', 'manual')",
            name="valid_user_course_attempt_source",
        ),
        db.Index("idx_user_course_attempts_user_course", "user_id", "course_id"),
        db.Index("idx_user_course_attempts_user_offering", "user_id", "offering_id"),
    )


class UserOfferingCart(db.Model):
    __tablename__ = "user_offering_carts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    offering_id = db.Column(db.Integer, db.ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False)
    enabled = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("offering_carts", lazy="dynamic", cascade="all, delete-orphan"))
    offering = db.relationship("CourseOffering", backref=db.backref("user_carts", lazy="dynamic", cascade="all, delete-orphan"))

    __table_args__ = (
        db.UniqueConstraint("user_id", "offering_id", name="uq_user_offering_cart"),
        db.Index("idx_user_offering_carts_user", "user_id"),
    )


class UserSectionSelection(db.Model):
    __tablename__ = "user_section_selections"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    offering_id = db.Column(db.Integer, db.ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False)
    section_id = db.Column(db.Integer, db.ForeignKey("course_sections.id", ondelete="CASCADE"), nullable=False)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    source = db.Column(db.String(30), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("section_selections", lazy="dynamic", cascade="all, delete-orphan"))
    offering = db.relationship("CourseOffering", backref=db.backref("section_selections", lazy="dynamic", cascade="all, delete-orphan"))
    section = db.relationship("CourseSection", backref=db.backref("user_selections", lazy="dynamic", cascade="all, delete-orphan"))

    __table_args__ = (
        db.UniqueConstraint("user_id", "offering_id", "section_id", name="uq_user_section_selection"),
        db.CheckConstraint(
            "source IN ('cart', 'final', 'import')",
            name="valid_user_section_selection_source",
        ),
        db.Index("idx_user_section_selections_user_offering", "user_id", "offering_id"),
    )


class CoursePostOfferingTarget(db.Model):
    __tablename__ = "course_post_offering_targets"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    course_offering_id = db.Column(db.Integer, db.ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False)
    section_id = db.Column(db.Integer, db.ForeignKey("course_sections.id", ondelete="SET NULL"), nullable=True)
    instructor_text = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    post = db.relationship("Post", backref=db.backref("course_offering_target", uselist=False, cascade="all, delete-orphan"))
    course_offering = db.relationship("CourseOffering", backref=db.backref("post_targets", lazy="dynamic", cascade="all, delete-orphan"))
    section = db.relationship("CourseSection", backref=db.backref("post_targets", lazy="dynamic"))

    __table_args__ = (
        db.UniqueConstraint("post_id", name="uq_course_post_offering_target_post"),
        db.Index("idx_course_post_offering_targets_offering", "course_offering_id"),
    )
