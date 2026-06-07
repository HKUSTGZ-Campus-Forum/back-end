import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.course_domain import CourseMeeting, CourseOffering, CourseSection, UserCourseState
from app.models.scheduler_section import SchedulerSection
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
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for proxy_key in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"]:
        monkeypatch.delenv(proxy_key, raising=False)
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def seed_course(app):
    with app.app_context():
        course = Course(
            code="TEST2205",
            name="Introduction to AI",
            credits=3,
            subject="TEST",
            catalog_number="2205",
            course_title_abbr="Intro AI",
            description="AI foundations.",
            pre_requirement="AIAA 1010",
            co_requirement="",
            exclusion="",
            is_active=True,
        )
        db.session.add(course)
        db.session.flush()
        offering = CourseOffering(
            course_id=course.id,
            semester_id="2530",
            offering_code=course.code,
            title_snapshot=course.name,
            credits_snapshot=course.credits,
            source="test",
            status="offered",
        )
        db.session.add(offering)
        db.session.flush()
        section = CourseSection(
            offering_id=offering.id,
            source_section_id="TEST2205-L01",
            name="L01",
            bundle=1,
            layer=0,
            quota=60,
            section_type="L",
            is_main=True,
        )
        db.session.add(section)
        db.session.flush()
        lecture = CourseMeeting(
            section_id=section.id,
            day=1,
            start_time=900,
            end_time=1030,
            room="Room 101",
            instructor_text="Dr. AI",
        )
        db.session.add_all([section, lecture])
        db.session.commit()
        return course.id


def create_user_and_headers(app, course_id):
    with app.app_context():
        role = UserRole.query.filter_by(name=UserRole.USER).first()
        if role is None:
            role = UserRole(name=UserRole.USER, description="user role")
            db.session.add(role)
            db.session.flush()
        user = User(
            username="overview_user",
            email="overview_user@connect.hkust-gz.edu.cn",
            role_id=role.id,
            email_verified=True,
        )
        user.password_hash = "test-password-hash"
        db.session.add(user)
        db.session.flush()
        db.session.add(UserCourseState(
            user_id=user.id,
            course_id=course_id,
            status="interested",
            source="manual",
        ))
        db.session.commit()
        token = create_access_token(identity=str(user.id))
        return {"Authorization": f"Bearer {token}"}


def test_get_course_overview_by_compact_code(client, app):
    seed_course(app)

    response = client.get("/courses/by-code/test-2205/overview")

    assert response.status_code == 200
    data = response.get_json()
    assert data["course"]["code"] == "TEST2205"
    assert data["course"]["display_code"] == "TEST 2205"
    assert data["course"]["title"] == "Introduction to AI"
    assert data["course"]["credits"] == 3
    assert data["course"]["pre_requirement"] == "AIAA 1010"
    assert data["academic_record"] is None
    assert data["offerings"][0]["offering_tag"] == "25-26Spring"
    assert data["offerings"][0]["scheduler_semester_id"] == "2530"
    assert data["offerings"][0]["section_count"] == 1
    assert data["offerings"][0]["instructors"] == ["Dr. AI"]


def test_course_overview_does_not_expose_legacy_scheduler_only_offerings(client, app):
    with app.app_context():
        course = Course(
            code="OLD1001",
            name="Legacy Only",
            credits=3,
            is_active=True,
        )
        db.session.add(course)
        db.session.flush()
        course.create_semester_tag("2025spring")
        db.session.add(SchedulerSection(
            semester_id="2530",
            section_id="OLD1001-L01",
            course_id=course.id,
            name="L01",
            bundle=1,
            layer=0,
            quota=60,
            section_type="L",
            is_main=True,
        ))
        db.session.commit()

    response = client.get("/courses/by-code/OLD1001/overview")

    assert response.status_code == 200
    assert response.get_json()["offerings"] == []


def test_course_overview_prefers_scheduler_course_when_compact_codes_duplicate(client, app):
    with app.app_context():
        legacy_course = Course(
            code="DUPL 2205",
            name="Legacy Duplicate",
            credits=3,
            description="Legacy row from forum tags.",
            is_active=True,
        )
        db.session.add(legacy_course)
        db.session.flush()
        legacy_course.create_semester_tag("2025spring")

        scheduler_course = Course(
            code="DUPL2205",
            name="Scheduler Duplicate",
            credits=3,
            subject="DUPL",
            catalog_number="2205",
            description="Scheduler row with sections.",
            pre_requirement="DUPL 1001",
            is_active=True,
        )
        db.session.add(scheduler_course)
        db.session.flush()
        offering = CourseOffering(
            course_id=scheduler_course.id,
            semester_id="2530",
            offering_code=scheduler_course.code,
            title_snapshot=scheduler_course.name,
            credits_snapshot=scheduler_course.credits,
            source="test",
            status="offered",
        )
        db.session.add(offering)
        db.session.flush()
        lecture = CourseSection(
            offering_id=offering.id,
            source_section_id="DUPL2205-L01",
            name="L01",
            bundle=1,
            layer=0,
            quota=60,
            section_type="L",
            is_main=True,
        )
        tutorial = CourseSection(
            offering_id=offering.id,
            source_section_id="DUPL2205-T01",
            name="T01",
            bundle=1,
            layer=1,
            quota=30,
            section_type="T",
            is_main=False,
        )
        db.session.add_all([lecture, tutorial])
        db.session.flush()
        db.session.add(CourseMeeting(
            section_id=lecture.id,
            day=1,
            start_time=900,
            end_time=1030,
            room="Room 101",
            instructor_text="Dr. Scheduler",
        ))
        scheduler_course_id = scheduler_course.id
        db.session.commit()

    response = client.get("/courses/by-code/DUPL2205/overview")

    assert response.status_code == 200
    data = response.get_json()
    assert data["course"]["id"] == scheduler_course_id
    assert data["course"]["code"] == "DUPL2205"
    assert data["course"]["title"] == "Scheduler Duplicate"
    assert data["course"]["pre_requirement"] == "DUPL 1001"
    assert data["offerings"][0]["offering_tag"] == "25-26Spring"
    assert data["offerings"][0]["scheduler_semester_id"] == "2530"
    assert data["offerings"][0]["section_count"] == 2
    assert data["offerings"][0]["instructors"] == ["Dr. Scheduler"]


def test_get_course_overview_includes_authenticated_academic_record(client, app):
    course_id = seed_course(app)
    headers = create_user_and_headers(app, course_id)

    response = client.get("/courses/by-code/TEST2205/overview", headers=headers)

    assert response.status_code == 200
    data = response.get_json()
    assert data["academic_record"]["course_code"] == "TEST2205"
    assert data["academic_record"]["status"] == "interested"


def test_resolve_numeric_course_identifier(client, app):
    course_id = seed_course(app)

    response = client.get(f"/courses/resolve/{course_id}")

    assert response.status_code == 200
    data = response.get_json()
    assert data["course_id"] == course_id
    assert data["course_code"] == "TEST2205"
    assert data["overview_path"] == "/courses/TEST2205"


def test_course_overview_not_found(client, app):
    response = client.get("/courses/by-code/NOPE9999/overview")

    assert response.status_code == 404
