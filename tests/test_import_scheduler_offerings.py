import json
import os

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.course_domain import CourseCatalogVersion, CourseMeeting, CourseOffering, CourseSection
from app.models.scheduler_cart import SchedulerUserCourseCart
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_section import SchedulerSection
from app.models.user import User
from app.models.user_role import UserRole
from app.scripts.import_scheduler_offerings import (
    DEPLOY_SCHEDULER_OFFERING_UPDATE_MODE,
    OfferingValidationError,
    apply_offerings,
    build_import_plan,
    bundled_scheduler_offering_updates,
    create_import_app,
    file_sha256,
    load_offerings_file,
    run_deploy_scheduler_offering_update,
)


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
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def write_payload(tmp_path, payload):
    path = tmp_path / "offerings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def payload():
    return {
        "semester_id": "2540",
        "courses": [
            {
                "course_code": "TEST1001",
                "course_title": "Test Course",
                "course_desc": "",
                "credit": 3,
                "subject": "TEST",
                "catalog_number": "1001",
                "pg_course": False,
                "sections": [
                    {
                        "semester_id": "2540",
                        "section_id": "TEST1001-L01",
                        "course_code": "TEST1001",
                        "section_type": "L",
                        "name": "L01",
                        "bundle": 1,
                        "layer": 0,
                        "quota": 50,
                        "is_main": True,
                        "lectures": [
                            {
                                "day": 1,
                                "start_time": "0900",
                                "end_time": "1050",
                                "room": "Room 101",
                                "instructor": "Dr. Test",
                            }
                        ],
                    }
                ],
            },
            {
                "course_code": "ZERO1001",
                "course_title": "Zero Section Course",
                "course_desc": "",
                "credit": 2,
                "subject": "ZERO",
                "catalog_number": "1001",
                "pg_course": False,
                "sections": [],
            },
        ],
    }


def test_load_offerings_file_validates_and_normalizes(tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    assert snapshot.semester_id == "2540"
    assert len(snapshot.courses) == 2
    assert snapshot.courses[0].sections[0].lectures[0].start_time == 900


def test_create_import_app_maps_schema_query_to_postgres_search_path():
    flask_app = create_import_app(
        "postgresql://user:pass@localhost/course_scheduler?schema=public"
    )

    assert flask_app.config["SQLALCHEMY_DATABASE_URI"] == (
        "postgresql://user:pass@localhost/course_scheduler"
    )
    assert flask_app.config["SQLALCHEMY_ENGINE_OPTIONS"]["connect_args"]["options"] == (
        "-csearch_path=public"
    )


def test_load_offerings_file_rejects_invalid_lecture_day(tmp_path):
    data = payload()
    data["courses"][0]["sections"][0]["lectures"][0]["day"] = 8

    with pytest.raises(OfferingValidationError, match="expected 1-7"):
        load_offerings_file(write_payload(tmp_path, data))


def test_bundled_scheduler_offering_updates_match_files():
    updates = bundled_scheduler_offering_updates()

    assert DEPLOY_SCHEDULER_OFFERING_UPDATE_MODE == "apply"
    assert [update.expected_semester_id for update in updates] == ["2510", "2530", "2540"]
    for update in updates:
        snapshot = load_offerings_file(update.file_path, update.expected_semester_id)
        assert snapshot.semester_id == update.expected_semester_id
        assert file_sha256(update.file_path) == update.expected_sha256


def test_build_plan_and_apply_replace_only_target_semester(app, tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, payload()))

    with app.app_context():
        old = Course(code="OLD1001", name="Old Course", credits=3)
        other_semester = Course(code="KEEP1001", name="Keep Course", credits=3)
        db.session.add_all([old, other_semester])
        db.session.flush()
        db.session.add_all([
            SchedulerSection(
                semester_id="2540",
                section_id="OLD-L01",
                course_id=old.id,
                name="L01",
                bundle=1,
                layer=0,
                quota=10,
                section_type="L",
                is_main=True,
            ),
            SchedulerSection(
                semester_id="2530",
                section_id="KEEP-L01",
                course_id=other_semester.id,
                name="L01",
                bundle=1,
                layer=0,
                quota=10,
                section_type="L",
                is_main=True,
            ),
        ])
        db.session.add(SchedulerLecture(
            semester_id="2540",
            section_id="OLD-L01",
            day=1,
            start_time=900,
            end_time=1000,
            room="Old Room",
            instructor="Old Instructor",
        ))
        old_offering = CourseOffering(
            course_id=old.id,
            semester_id="2540",
            offering_code="OLD1001",
            title_snapshot="Old Course",
            credits_snapshot=3,
            source="test",
            status="offered",
        )
        keep_offering = CourseOffering(
            course_id=other_semester.id,
            semester_id="2530",
            offering_code="KEEP1001",
            title_snapshot="Keep Course",
            credits_snapshot=3,
            source="test",
            status="offered",
        )
        db.session.add_all([old_offering, keep_offering])
        db.session.flush()
        old_domain_section = CourseSection(
            offering_id=old_offering.id,
            source_section_id="OLD-L01",
            name="L01",
            section_type="L",
            bundle=1,
            layer=0,
            quota=10,
            is_main=True,
        )
        keep_domain_section = CourseSection(
            offering_id=keep_offering.id,
            source_section_id="KEEP-L01",
            name="L01",
            section_type="L",
            bundle=1,
            layer=0,
            quota=10,
            is_main=True,
        )
        db.session.add_all([old_domain_section, keep_domain_section])
        db.session.flush()
        db.session.add_all([
            CourseMeeting(
                section_id=old_domain_section.id,
                day=1,
                start_time=900,
                end_time=1000,
                room="Old Room",
                instructor_text="Old Instructor",
            ),
            CourseMeeting(
                section_id=keep_domain_section.id,
                day=2,
                start_time=900,
                end_time=1000,
                room="Keep Room",
                instructor_text="Keep Instructor",
            ),
        ])
        role = UserRole.query.filter_by(name="user").first() or UserRole(name="user")
        db.session.add(role)
        db.session.flush()
        user = User(
            username="scheduler_import_user",
            email="scheduler_import@hkust-gz.edu.cn",
            email_verified=True,
            role_id=role.id,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(SchedulerUserCourseCart(
            user_id=user.id,
            semester_id="2540",
            course_code="OLD1001",
            enabled=True,
        ))
        db.session.commit()

        plan = build_import_plan(snapshot)
        assert plan.existing_sections_to_replace == 1
        assert plan.existing_lectures_to_replace == 1
        assert plan.course_rows_to_insert == 2
        assert plan.stale_cart_references == ["OLD1001"]

        apply_offerings(snapshot)

        assert Course.query.filter_by(code="TEST1001").one().name == "Test Course"
        assert Course.query.filter_by(code="ZERO1001").one().credits == 2
        test_course = Course.query.filter_by(code="TEST1001").one()
        assert test_course.normalized_code == "TEST1001"
        assert test_course.display_code == "TEST 1001"
        assert CourseCatalogVersion.query.filter_by(
            course_id=test_course.id,
            source="scheduler_offerings",
            source_version="2540",
        ).one().title == "Test Course"
        offering = CourseOffering.query.filter_by(
            course_id=test_course.id,
            semester_id="2540",
        ).one()
        assert offering.title_snapshot == "Test Course"
        assert CourseSection.query.filter_by(offering_id=offering.id).count() == 1
        assert CourseMeeting.query.join(CourseSection).filter(
            CourseSection.offering_id == offering.id
        ).one().instructor_text == "Dr. Test"
        zero_course = Course.query.filter_by(code="ZERO1001").one()
        assert CourseOffering.query.filter_by(course_id=zero_course.id, semester_id="2540").count() == 0
        assert old_offering.status == "archived"
        assert CourseSection.query.filter_by(offering_id=old_offering.id).count() == 0
        assert CourseSection.query.filter_by(offering_id=keep_offering.id).count() == 1
        assert SchedulerSection.query.filter_by(semester_id="2540").count() == 1
        assert SchedulerLecture.query.filter_by(semester_id="2540").count() == 1
        assert SchedulerSection.query.filter_by(semester_id="2530", section_id="KEEP-L01").one()
        assert SchedulerUserCourseCart.query.filter_by(
            user_id=user.id,
            semester_id="2540",
            course_code="OLD1001",
        ).one()


def test_apply_offerings_preserves_existing_course_rules_when_snapshot_rules_are_empty(app, tmp_path):
    snapshot = load_offerings_file(write_payload(tmp_path, {
        "semester_id": "2530",
        "courses": [
            {
                "course_code": "RULE1504",
                "course_title": "Honors General Physics II",
                "course_desc": "Offering description.",
                "credit": 3,
                "subject": "RULE",
                "catalog_number": "1504",
                "sections": [],
            }
        ],
    }))

    with app.app_context():
        db.session.add(Course(
            code="RULE1504",
            name="Honors General Physics II",
            credits=3,
            pre_requirement="(UFUG 1501 or UFUG 1503) AND (UFUG 1102 or UFUG 1105)",
            exclusion="UFUG 1502",
        ))
        db.session.commit()

        apply_offerings(snapshot)

        course = Course.query.filter_by(code="RULE1504").one()

    assert course.pre_requirement == "(UFUG 1501 or UFUG 1503) AND (UFUG 1102 or UFUG 1105)"
    assert course.co_requirement is None
    assert course.exclusion == "UFUG 1502"


def test_deploy_update_dry_run_does_not_write_database(app, tmp_path):
    path = write_payload(tmp_path, payload())
    digest = file_sha256(path)

    with app.app_context():
        result = run_deploy_scheduler_offering_update(
            mode="dry-run",
            file_path=path,
            expected_semester_id="2540",
            expected_sha256=digest,
        )

        assert result.status == "dry-run"
        assert result.plan.courses == 2
        assert SchedulerSection.query.filter_by(semester_id="2540").count() == 0
        assert SchedulerLecture.query.filter_by(semester_id="2540").count() == 0
        assert CourseOffering.query.filter_by(semester_id="2540").count() == 0


def test_deploy_update_apply_is_guarded_by_hash_and_runs_once(app, tmp_path):
    path = write_payload(tmp_path, payload())
    digest = file_sha256(path)

    with app.app_context():
        mismatch = run_deploy_scheduler_offering_update(
            mode="apply",
            file_path=path,
            expected_semester_id="2540",
            expected_sha256="0" * 64,
        )
        assert mismatch.status == "blocked"
        assert SchedulerSection.query.filter_by(semester_id="2540").count() == 0

        first = run_deploy_scheduler_offering_update(
            mode="apply",
            file_path=path,
            expected_semester_id="2540",
            expected_sha256=digest,
        )
        second = run_deploy_scheduler_offering_update(
            mode="apply",
            file_path=path,
            expected_semester_id="2540",
            expected_sha256=digest,
        )

        assert first.status == "applied"
        assert first.plan.sections == 1
        assert second.status == "skipped"
        assert SchedulerSection.query.filter_by(semester_id="2540").count() == 1
        assert SchedulerLecture.query.filter_by(semester_id="2540").count() == 1
        assert CourseOffering.query.filter_by(semester_id="2540").count() == 1
        assert CourseSection.query.join(CourseOffering).filter(
            CourseOffering.semester_id == "2540"
        ).count() == 1
