import json
import os

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.academic_map import UserCourseRecord
from app.models.course import Course
from app.models.course_domain import (
    CourseCatalogRequirement,
    CourseCatalogVersion,
    CourseMeeting,
    CourseOffering,
    CoursePostOfferingTarget,
    CourseRequirementEdge,
    CourseSection,
    UserCourseAttempt,
    UserCourseState,
    UserOfferingCart,
    UserSectionSelection,
)
from app.models.post import Post
from app.models.scheduler_cart import SchedulerUserBundleCart, SchedulerUserCourseCart
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_section import SchedulerSection
from app.models.tag import Tag, TagType
from app.models.user import User
from app.models.user_role import UserRole


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CACHE_TYPE = "SimpleCache"
    ENABLE_BACKGROUND_TASKS = False
    JWT_SECRET_KEY = "test-secret"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "test-key"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", os.getenv("DASHSCOPE_API_KEY", "test-key"))
    for proxy_key in [
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
    ]:
        monkeypatch.delenv(proxy_key, raising=False)
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _user():
    role = UserRole.query.filter_by(name=UserRole.USER).first()
    if role is None:
        role = UserRole(name=UserRole.USER, description="user role")
        db.session.add(role)
        db.session.flush()
    user = User(
        username="migration_user",
        email="migration_user@connect.hkust-gz.edu.cn",
        role_id=role.id,
        email_verified=True,
    )
    user.password_hash = "test-password-hash"
    db.session.add(user)
    db.session.flush()
    return user


def _tag(name, type_name):
    tag_type = TagType.query.filter_by(name=type_name).first()
    if tag_type is None:
        tag_type = TagType(name=type_name)
        db.session.add(tag_type)
        db.session.flush()
    tag = Tag(name=name, tag_type_id=tag_type.id)
    db.session.add(tag)
    db.session.flush()
    return tag


def test_canonicalize_courses_reports_duplicates_and_applies(app):
    from app.services.course_domain_migration import canonicalize_courses

    with app.app_context():
        spaced = Course(code="CDOM 1001", name="Legacy", credits=3)
        compact = Course(
            code="CDOM1001",
            name="Canonical",
            credits=3,
            subject="CDOM",
            catalog_number="1001",
            course_title_abbr="Canon",
        )
        db.session.add_all([spaced, compact])
        db.session.flush()
        db.session.add(SchedulerSection(
            semester_id="2530",
            section_id="CDOM1001-L01",
            course_id=compact.id,
            name="L01",
            bundle=1,
            layer=0,
            quota=30,
            section_type="L",
            is_main=True,
        ))
        db.session.commit()

        dry_run = canonicalize_courses(apply=False)
        assert any(item["normalized_code"] == "CDOM1001" for item in dry_run.anomalies)

        summary = canonicalize_courses(apply=True)
        db.session.commit()

        assert summary.updated >= 1
        assert Course.query.filter_by(code="CDOM1001").one().normalized_code == "CDOM1001"


def test_catalog_and_requirement_migration_create_versions_edges(app, tmp_path):
    from app.services.course_domain_migration import (
        canonicalize_courses,
        migrate_catalog_versions,
        migrate_requirements,
    )

    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({
        "courses": [
            {
                "course_code": "CDOM2001",
                "course_title": "Target Course",
                "credit": "3",
                "course_desc": "Target desc",
                "subject": "CDOM",
                "catalog_number": "2001",
                "pre_requirement": "CDOM 1001",
                "co_requirement": "CDOM 1002",
                "exclusion": "CDOM 1003",
            },
            {"course_code": "CDOM1001", "course_title": "Prereq", "credit": "3"},
            {"course_code": "CDOM1002", "course_title": "Coreq", "credit": "3"},
            {"course_code": "CDOM1003", "course_title": "Excluded", "credit": "3"},
        ]
    }), encoding="utf-8")
    prereq_path = tmp_path / "prereq.json"
    prereq_path.write_text(json.dumps({
        "courses": [
            {
                "course_code": "CDOM2001",
                "raw_pre_requirement": "CDOM 1001",
                "normalized_pre_requirement": "CDOM1001",
                "requirement_kind": "course",
                "prerequisite_expression": {"course_code": "CDOM1001"},
            }
        ]
    }), encoding="utf-8")

    with app.app_context():
        db.session.add_all([
            Course(code="CDOM2001", name="Target Course", credits=3),
            Course(code="CDOM1001", name="Prereq", credits=3),
            Course(code="CDOM1002", name="Coreq", credits=3),
            Course(code="CDOM1003", name="Excluded", credits=3),
        ])
        db.session.commit()
        canonicalize_courses(apply=True)
        migrate_catalog_versions(catalog_path=catalog_path, apply=True)
        summary = migrate_requirements(prerequisite_path=prereq_path, apply=True)
        db.session.commit()

        target = Course.query.filter_by(normalized_code="CDOM2001").one()
        version = CourseCatalogVersion.query.filter_by(course_id=target.id).one()
        assert version.pre_requirement_raw == "CDOM 1001"
        assert CourseCatalogRequirement.query.filter_by(catalog_version_id=version.id).count() == 3
        assert CourseRequirementEdge.query.filter_by(to_course_id=target.id).count() == 3
        assert summary.created == 3


def test_offering_user_cart_and_review_migrations(app, tmp_path):
    from app.services.course_domain_migration import (
        canonicalize_courses,
        migrate_offerings,
        migrate_review_targets,
        migrate_scheduler_carts,
        migrate_user_academic_state,
    )

    anomaly_path = tmp_path / "review-anomalies.json"

    with app.app_context():
        user = _user()
        course = Course(code="CDOM3300", name="Migrated Course", credits=3)
        db.session.add(course)
        db.session.flush()
        section = SchedulerSection(
            semester_id="2530",
            section_id="CDOM3300-L01",
            course_id=course.id,
            name="L01",
            bundle=1,
            layer=0,
            quota=40,
            section_type="L",
            is_main=True,
        )
        lecture = SchedulerLecture(
            semester_id="2530",
            section_id="CDOM3300-L01",
            day=1,
            start_time=900,
            end_time=1150,
            room="Room 1",
            instructor="Prof. Domain",
        )
        db.session.add_all([section, lecture])
        db.session.add(UserCourseRecord(
            user_id=user.id,
            course_id=course.id,
            course_code="CDOM3300",
            course_title="Migrated Course",
            term_code="2530",
            term_label="2025-26 Spring",
            units=3,
            status=UserCourseRecord.STATUS_COMPLETED,
            grade="A-",
            keep_grade=True,
            import_source=UserCourseRecord.SOURCE_PASTE,
        ))
        db.session.add(SchedulerUserCourseCart(
            user_id=user.id,
            semester_id="2530",
            course_code="CDOM3300",
            enabled=True,
        ))
        db.session.add(SchedulerUserBundleCart(
            user_id=user.id,
            semester_id="2530",
            course_code="CDOM3300",
            id=1,
            layer=0,
            enabled=True,
        ))

        course_tag = _tag("CDOM3300", TagType.COURSE)
        offering_tag = _tag("25-26Spring", TagType.USER)
        review_tag = _tag("course-review", TagType.SYSTEM)
        unresolved_course_tag = _tag("ONLYCOURSE", TagType.COURSE)
        review = Post(user_id=user.id, title="Review", content="Good")
        review.tags = [course_tag, offering_tag, review_tag]
        unresolved = Post(user_id=user.id, title="Unresolved", content="Missing offering")
        unresolved.tags = [unresolved_course_tag, review_tag]
        db.session.add_all([review, unresolved])
        db.session.commit()

        canonicalize_courses(apply=True)
        migrate_offerings(apply=True)
        migrate_user_academic_state(apply=True)
        migrate_scheduler_carts(apply=True)
        review_summary = migrate_review_targets(anomaly_path=anomaly_path, apply=True)
        db.session.commit()

        offering = CourseOffering.query.join(Course).filter(
            Course.normalized_code == "CDOM3300",
            CourseOffering.semester_id == "2530",
        ).one()
        assert CourseSection.query.filter_by(offering_id=offering.id).count() == 1
        assert CourseMeeting.query.join(CourseSection).filter(CourseSection.offering_id == offering.id).count() == 1
        assert UserCourseAttempt.query.filter_by(user_id=user.id, offering_id=offering.id).one().grade_letter == "A-"
        assert float(UserCourseState.query.filter_by(user_id=user.id, course_id=offering.course_id).one().best_grade_points) == 3.7
        assert UserOfferingCart.query.filter_by(user_id=user.id, offering_id=offering.id).one().enabled is True
        assert UserSectionSelection.query.filter_by(user_id=user.id, offering_id=offering.id).count() == 1
        assert CoursePostOfferingTarget.query.filter_by(post_id=review.id, course_offering_id=offering.id).one()
        assert review_summary.anomalies
        anomaly_data = json.loads(anomaly_path.read_text(encoding="utf-8"))
        assert anomaly_data["items"][0]["record_id"] == unresolved.id


def test_migrate_offerings_can_limit_to_legacy_semesters(app):
    from app.services.course_domain_migration import migrate_offerings

    with app.app_context():
        target_course = Course(code="CDOM2430", name="Target Legacy Course", credits=3)
        ignored_course = Course(code="CDOM2530", name="Ignored Legacy Course", credits=3)
        db.session.add_all([target_course, ignored_course])
        db.session.flush()
        db.session.add_all([
            SchedulerSection(
                semester_id="2430",
                section_id="CDOM2430-L01",
                course_id=target_course.id,
                name="L01",
                bundle=1,
                layer=0,
                quota=40,
                section_type="L",
                is_main=True,
            ),
            SchedulerLecture(
                semester_id="2430",
                section_id="CDOM2430-L01",
                day=2,
                start_time=900,
                end_time=1020,
                room="Room 2430",
                instructor="Prof. Spring",
            ),
            SchedulerSection(
                semester_id="2530",
                section_id="CDOM2530-L01",
                course_id=ignored_course.id,
                name="L01",
                bundle=1,
                layer=0,
                quota=40,
                section_type="L",
                is_main=True,
            ),
        ])
        db.session.commit()

        dry_run = migrate_offerings(apply=False, semester_ids=["2430"])
        assert dry_run.scanned == 1
        assert CourseOffering.query.count() == 0

        summary = migrate_offerings(apply=True, semester_ids=["2430"])
        db.session.commit()

        assert summary.scanned == 1
        offering = CourseOffering.query.filter_by(course_id=target_course.id, semester_id="2430").one()
        assert CourseSection.query.filter_by(offering_id=offering.id).count() == 1
        assert CourseMeeting.query.join(CourseSection).filter(CourseSection.offering_id == offering.id).count() == 1
        assert CourseOffering.query.filter_by(course_id=ignored_course.id, semester_id="2530").count() == 0


def test_packaged_legacy_scheduler_backfill_imports_tsv(app, tmp_path, monkeypatch):
    from app.scripts import backfill_legacy_scheduler_offerings

    sections_path = tmp_path / "sections.tsv"
    lectures_path = tmp_path / "lectures.tsv"
    sections_path.write_text(
        "\t".join([
            "course_code",
            "name",
            "bundle",
            "quota",
            "enrol",
            "avail",
            "wait",
            "section_id",
            "semester_id",
            "section_type",
            "is_main",
            "layer",
        ])
        + "\n"
        + "CDOM2440\tL01\t1\t40\t12\t28\t0\t6045\t2440\tL\tt\t0\n",
        encoding="utf-8",
    )
    lectures_path.write_text(
        "\t".join(["section_id", "id", "day", "start_time", "end_time", "room", "instructor", "semester_id"])
        + "\n"
        + "6045\t1\t2\t1500\t1750\tRoom 2440\tProf. Summer\t2440\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(backfill_legacy_scheduler_offerings, "LEGACY_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        backfill_legacy_scheduler_offerings,
        "PACKAGED_SEMESTER_FILES",
        {"2440": ("sections.tsv", "lectures.tsv")},
    )

    with app.app_context():
        course = Course(code="CDOM2440", name="Packaged Legacy Course", credits=3)
        db.session.add(course)
        db.session.commit()

        dry_run = backfill_legacy_scheduler_offerings.run_backfill(semesters=["2440"], apply=False)
        assert dry_run.scanned == 1
        assert CourseOffering.query.count() == 0

        summary = backfill_legacy_scheduler_offerings.run_backfill(semesters=["2440"], apply=True)
        db.session.commit()

        assert summary.scanned == 1
        offering = CourseOffering.query.filter_by(course_id=course.id, semester_id="2440").one()
        section = CourseSection.query.filter_by(offering_id=offering.id).one()
        assert section.enrol == 12
        assert section.avail == 28
        assert CourseMeeting.query.filter_by(section_id=section.id).one().room == "Room 2440"


def test_full_migration_dry_run_uses_transactional_staging_then_rolls_back(app, tmp_path, monkeypatch):
    from app.scripts import migrate_course_domain
    from app.services.course_domain_migration import MigrationSummary

    apply_flags = []

    def record_apply_flag(**kwargs):
        apply_flags.append(kwargs["apply"])
        return MigrationSummary(created=1)

    def stage_course(**kwargs):
        apply_flags.append(kwargs["apply"])
        db.session.add(Course(code="DRYR1000", name="Dry Run Staged", credits=3))
        return MigrationSummary(created=1)

    def write_review_anomalies(**kwargs):
        apply_flags.append(kwargs["apply"])
        kwargs["anomaly_path"].write_text(json.dumps({"items": []}), encoding="utf-8")
        return MigrationSummary(created=1)

    monkeypatch.setattr(migrate_course_domain, "canonicalize_courses", stage_course)
    monkeypatch.setattr(migrate_course_domain, "migrate_catalog_versions", record_apply_flag)
    monkeypatch.setattr(migrate_course_domain, "migrate_requirements", record_apply_flag)
    monkeypatch.setattr(migrate_course_domain, "migrate_offerings", record_apply_flag)
    monkeypatch.setattr(migrate_course_domain, "migrate_user_academic_state", record_apply_flag)
    monkeypatch.setattr(migrate_course_domain, "migrate_scheduler_carts", record_apply_flag)
    monkeypatch.setattr(migrate_course_domain, "migrate_review_targets", write_review_anomalies)

    with app.app_context():
        report = migrate_course_domain.run_course_domain_migration(
            apply=False,
            anomaly_file=tmp_path / "anomalies.json",
        )

        assert report.canonical_courses.created == 1
        assert apply_flags == [True, True, True, True, True, True, True]
        assert Course.query.filter_by(code="DRYR1000").count() == 0
