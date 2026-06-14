import os

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.course_domain import CourseOffering, UserCourseAttempt


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


def test_normalize_and_display_course_code():
    from app.services.course_domain import (
        catalog_number_for_code,
        display_course_code,
        normalize_course_code,
        subject_for_code,
    )

    assert normalize_course_code("UFUG 2104") == "UFUG2104"
    assert normalize_course_code("ufug2104") == "UFUG2104"
    assert display_course_code("UFUG2104") == "UFUG 2104"
    assert display_course_code("UCUG1052A") == "UCUG 1052A"
    assert subject_for_code("UFUG2104") == "UFUG"
    assert catalog_number_for_code("UFUG2104") == "2104"
    assert subject_for_code("UCUG1052A") == "UCUG"
    assert catalog_number_for_code("UCUG1052A") == "1052A"


def test_grade_points_for_letter():
    from app.services.course_domain import grade_points_for_letter

    assert grade_points_for_letter("A") == 4.0
    assert grade_points_for_letter("A-") == 3.7
    assert grade_points_for_letter("F") == 0.0
    assert grade_points_for_letter("P") is None


def test_expression_missing_courses_preserves_or_logic():
    from app.services.course_domain import expression_missing_courses

    expression = {
        "op": "AND",
        "items": [
            {"course_code": "UFUG2104"},
            {
                "op": "OR",
                "items": [
                    {"course_code": "UFUG2601"},
                    {"course_code": "UFUG2602"},
                ],
            },
        ],
    }
    assert expression_missing_courses(expression, {"UFUG2104", "UFUG2602"}) == []
    assert expression_missing_courses(expression, {"UFUG2104"}) == ["UFUG2601"]


def test_database_helpers_find_course_offering_and_best_attempt(app):
    from app.services.course_domain import (
        best_completed_attempt,
        find_course_by_code,
        find_offering,
    )

    with app.app_context():
        course = Course(
            code="CDOM3300",
            normalized_code="CDOM3300",
            display_code="CDOM 3300",
            name="Course Domain",
            credits=3,
        )
        db.session.add(course)
        db.session.flush()
        offering = CourseOffering(
            course_id=course.id,
            semester_id="2530",
            offering_code="CDOM3300",
            title_snapshot=course.name,
            credits_snapshot=3,
            source="test",
            status="offered",
        )
        db.session.add(offering)
        db.session.flush()
        failed = UserCourseAttempt(
            user_id=1,
            course_id=course.id,
            offering_id=offering.id,
            status="failed",
            grade_letter="F",
            grade_points=0,
            source="manual",
        )
        passed = UserCourseAttempt(
            user_id=1,
            course_id=course.id,
            offering_id=offering.id,
            status="completed",
            grade_letter="A-",
            grade_points=3.7,
            source="manual",
        )
        db.session.add_all([failed, passed])
        db.session.commit()

        assert find_course_by_code("CDOM 3300").id == course.id
        assert find_offering(course, "2530").id == offering.id
        assert best_completed_attempt([failed, passed]).id == passed.id


def test_withdrawn_attempts_derive_not_taken_state(app):
    from app.services.course_domain import derive_user_course_state

    with app.app_context():
        course = Course(
            code="CDOM4400",
            normalized_code="CDOM4400",
            display_code="CDOM 4400",
            name="Withdrawn Course",
            credits=3,
        )
        db.session.add(course)
        db.session.flush()
        offering = CourseOffering(
            course_id=course.id,
            semester_id="2530",
            offering_code="CDOM4400",
            title_snapshot=course.name,
            credits_snapshot=3,
            source="test",
            status="offered",
        )
        db.session.add(offering)
        db.session.flush()
        db.session.add(UserCourseAttempt(
            user_id=1,
            course_id=course.id,
            offering_id=offering.id,
            status="withdrawn",
            grade_letter="W",
            source="manual",
        ))
        db.session.commit()

        state = derive_user_course_state(1, course.id)

    assert state["status"] == "not_taken"
    assert state["best_attempt_id"] is None
    assert state["best_grade_letter"] is None
