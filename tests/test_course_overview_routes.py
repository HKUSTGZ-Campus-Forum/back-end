import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.academic_map import UserCourseRecord
from app.models.course import Course
from app.models.scheduler_lecture import SchedulerLecture
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
        course.create_semester_tag("2025spring")
        section = SchedulerSection(
            semester_id="2530",
            section_id="TEST2205-L01",
            course_id=course.id,
            name="L01",
            bundle=1,
            layer=0,
            quota=60,
            section_type="L",
            is_main=True,
        )
        lecture = SchedulerLecture(
            semester_id="2530",
            section_id="TEST2205-L01",
            day=1,
            start_time=900,
            end_time=1030,
            room="Room 101",
            instructor="Dr. AI",
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
        db.session.add(UserCourseRecord(
            user_id=user.id,
            course_id=course_id,
            course_code="TEST2205",
            course_title="Introduction to AI",
            status=UserCourseRecord.STATUS_INTERESTED,
            units=3,
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
