import json
import os

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.scheduler_cart import SchedulerUserCourseCart
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_section import SchedulerSection
from app.models.user import User
from app.models.user_role import UserRole
from app.scripts.import_scheduler_offerings import (
    OfferingValidationError,
    apply_offerings,
    build_import_plan,
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


def test_load_offerings_file_rejects_invalid_lecture_day(tmp_path):
    data = payload()
    data["courses"][0]["sections"][0]["lectures"][0]["day"] = 8

    with pytest.raises(OfferingValidationError, match="expected 1-7"):
        load_offerings_file(write_payload(tmp_path, data))


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
        assert SchedulerSection.query.filter_by(semester_id="2540").count() == 1
        assert SchedulerLecture.query.filter_by(semester_id="2540").count() == 1
        assert SchedulerSection.query.filter_by(semester_id="2530", section_id="KEEP-L01").one()
        assert SchedulerUserCourseCart.query.filter_by(
            user_id=user.id,
            semester_id="2540",
            course_code="OLD1001",
        ).one()


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
