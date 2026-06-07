import os

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
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


def _create_user():
    role = UserRole.query.filter_by(name=UserRole.USER).first()
    if role is None:
        role = UserRole(name=UserRole.USER, description="user role")
        db.session.add(role)
        db.session.flush()
    user = User(
        username="course_domain_user",
        email="course_domain_user@connect.hkust-gz.edu.cn",
        role_id=role.id,
        email_verified=True,
    )
    user.password_hash = "test-password-hash"
    db.session.add(user)
    db.session.flush()
    return user


def test_course_domain_models_store_versioned_offering_graph(app):
    from app.models.course_domain import (
        CourseCatalogRequirement,
        CourseCatalogVersion,
        CourseMeeting,
        CourseOffering,
        CourseRequirementEdge,
        CourseSection,
    )

    with app.app_context():
        course = Course(
            normalized_code="CDOM2205",
            display_code="CDOM 2205",
            code="CDOM2205",
            name="Introduction to Artificial Intelligence",
            credits=3,
        )
        db.session.add(course)
        db.session.flush()

        version = CourseCatalogVersion(
            course_id=course.id,
            source="course_catalog.json",
            title="Introduction to Artificial Intelligence",
            credits=3,
            pre_requirement_raw="UFUG 2601 OR UFUG 2602",
        )
        db.session.add(version)
        db.session.flush()

        requirement = CourseCatalogRequirement(
            catalog_version_id=version.id,
            relation_type="prerequisite",
            raw_text="UFUG 2601 OR UFUG 2602",
            normalized_text="UFUG2601 OR UFUG2602",
            requirement_kind="course",
            expression_json={
                "op": "OR",
                "items": [
                    {"course_code": "UFUG2601"},
                    {"course_code": "UFUG2602"},
                ],
            },
            source="course_prerequisites.json",
        )
        db.session.add(requirement)
        db.session.flush()

        prereq = Course(
            normalized_code="CDOM2601",
            display_code="CDOM 2601",
            code="CDOM2601",
            name="C++ Programming",
            credits=3,
        )
        db.session.add(prereq)
        db.session.flush()
        db.session.add(
            CourseRequirementEdge(
                requirement_id=requirement.id,
                from_course_id=prereq.id,
                to_course_id=course.id,
                relation_type="prerequisite",
            )
        )

        offering = CourseOffering(
            course_id=course.id,
            semester_id="2530",
            catalog_version_id=version.id,
            offering_code="CDOM2205",
            title_snapshot="Introduction to Artificial Intelligence",
            credits_snapshot=3,
            source="scheduler_offerings",
            status="offered",
        )
        db.session.add(offering)
        db.session.flush()

        section = CourseSection(
            offering_id=offering.id,
            source_section_id="6400",
            name="L01",
            section_type="L",
            bundle=1,
            layer=0,
            quota=50,
            enrol=36,
            avail=14,
            wait=0,
            is_main=True,
        )
        db.session.add(section)
        db.session.flush()
        db.session.add(
            CourseMeeting(
                section_id=section.id,
                day=1,
                start_time=900,
                end_time=1150,
                room="Rm 148, E1",
                instructor_text="LIU, Li",
            )
        )
        db.session.commit()

        loaded_course = Course.query.filter_by(normalized_code="CDOM2205").one()
        assert loaded_course.catalog_versions.count() == 1
        loaded_offering = CourseOffering.query.filter_by(
            course_id=course.id,
            semester_id="2530",
        ).one()
        assert loaded_offering.sections.count() == 1
        assert CourseMeeting.query.filter_by(section_id=section.id).one().instructor_text == "LIU, Li"


def test_user_course_state_and_attempt_store_best_gpa_inputs(app):
    from app.models.course_domain import (
        CourseOffering,
        UserCourseAttempt,
        UserCourseState,
    )

    with app.app_context():
        user = _create_user()
        course = Course(
            normalized_code="CDOM2205",
            display_code="CDOM 2205",
            code="CDOM2205",
            name="Introduction to Artificial Intelligence",
            credits=3,
        )
        db.session.add(course)
        db.session.flush()
        offering = CourseOffering(
            course_id=course.id,
            semester_id="2530",
            offering_code="CDOM2205",
            title_snapshot=course.name,
            credits_snapshot=3,
            source="test",
            status="offered",
        )
        db.session.add(offering)
        db.session.flush()
        attempt = UserCourseAttempt(
            user_id=user.id,
            course_id=course.id,
            offering_id=offering.id,
            status="completed",
            grade_letter="A-",
            grade_points=3.7,
            source="transcript_import",
        )
        db.session.add(attempt)
        db.session.flush()
        state = UserCourseState(
            user_id=user.id,
            course_id=course.id,
            status="completed",
            best_attempt_id=attempt.id,
            best_grade_points=3.7,
            best_grade_letter="A-",
            source="derived",
        )
        db.session.add(state)
        db.session.commit()

        assert float(UserCourseState.query.filter_by(
            user_id=user.id,
            course_id=course.id,
        ).one().best_grade_points) == 3.7
