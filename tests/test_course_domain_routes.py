from flask_jwt_extended import create_access_token
import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course
from app.models.course_domain import (
    CourseCatalogVersion,
    CourseMeeting,
    CourseOffering,
    CourseSection,
    UserOfferingCart,
    UserSectionSelection,
)
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
def test_app(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def test_client(test_app):
    return test_app.test_client()


def seed_new_domain_course(app):
    with app.app_context():
        course = Course(
            code="CDOM4400",
            normalized_code="CDOM4400",
            display_code="CDOM 4400",
            canonical_title="Clean Course Domain",
            name="Legacy Clean Course Domain",
            credits=3,
            subject="CDOM",
            catalog_number="4400",
            course_title_abbr="Clean Domain",
            description="Legacy description.",
            pre_requirement="Legacy prereq",
            is_active=True,
        )
        db.session.add(course)
        db.session.flush()

        version = CourseCatalogVersion(
            course_id=course.id,
            source="test",
            catalog_year="2025",
            title="Clean Course Domain",
            title_abbr="Domain",
            description="Versioned catalog description.",
            credits=4,
            pre_requirement_raw="CDOM 1100",
            co_requirement_raw="",
            exclusion_raw="CDOM 3300",
            pg_course=False,
            klms_course=True,
            effective_from_semester_id="2530",
        )
        db.session.add(version)
        db.session.flush()

        offering = CourseOffering(
            course_id=course.id,
            semester_id="2530",
            catalog_version_id=version.id,
            offering_code="CDOM4400",
            title_snapshot="Clean Course Domain",
            credits_snapshot=4,
            source="test",
            status="offered",
        )
        db.session.add(offering)
        db.session.flush()

        lecture = CourseSection(
            offering_id=offering.id,
            source_section_id="CDOM4400-L01",
            name="L01",
            section_type="L",
            bundle=1,
            layer=0,
            quota=60,
            enrol=40,
            avail=20,
            wait=0,
            is_main=True,
        )
        lab = CourseSection(
            offering_id=offering.id,
            source_section_id="CDOM4400-LA1",
            name="LA1",
            section_type="LA",
            bundle=1,
            layer=1,
            quota=30,
            enrol=20,
            avail=10,
            wait=0,
            is_main=False,
        )
        db.session.add_all([lecture, lab])
        db.session.flush()
        db.session.add_all([
            CourseMeeting(
                section_id=lecture.id,
                day=1,
                start_time=900,
                end_time=1030,
                room="Room 101",
                instructor_text="Dr. Domain",
            ),
            CourseMeeting(
                section_id=lab.id,
                day=3,
                start_time=1100,
                end_time=1250,
                room="Lab 1",
                instructor_text="TA Domain",
            ),
        ])
        db.session.commit()
        return course.id, offering.id


def auth_headers(app):
    with app.app_context():
        role = UserRole.query.filter_by(name=UserRole.USER).first()
        if role is None:
            role = UserRole(name=UserRole.USER, description="Regular user")
            db.session.add(role)
            db.session.flush()
        user = User(
            username="course_domain_route_user",
            email="course_domain_route_user@hkust-gz.edu.cn",
            email_verified=True,
            role_id=role.id,
        )
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        token = create_access_token(identity=str(user.id))
        return {"Authorization": f"Bearer {token}"}, user.id


def test_course_overview_reads_catalog_version_and_domain_offerings(test_client, test_app):
    seed_new_domain_course(test_app)

    resolve = test_client.get("/courses/resolve/CDOM 4400")
    assert resolve.status_code == 200
    assert resolve.get_json()["course_code"] == "CDOM4400"
    assert resolve.get_json()["display_code"] == "CDOM 4400"

    response = test_client.get("/courses/by-code/CDOM4400/overview")

    assert response.status_code == 200
    data = response.get_json()
    assert data["course"]["code"] == "CDOM4400"
    assert data["course"]["title"] == "Clean Course Domain"
    assert data["course"]["credits"] == 4
    assert data["course"]["description"] == "Versioned catalog description."
    assert data["course"]["pre_requirement"] == "CDOM 1100"
    assert data["course"]["exclusion"] == "CDOM 3300"
    assert data["course"]["klms_course"] is True
    assert data["offerings"][0]["scheduler_semester_id"] == "2530"
    assert data["offerings"][0]["section_count"] == 2
    assert data["offerings"][0]["instructors"] == ["Dr. Domain", "TA Domain"]


def test_scheduler_detail_reads_domain_sections_and_meetings(test_client, test_app):
    seed_new_domain_course(test_app)

    response = test_client.get("/scheduler/courses/CDOM4400?semester=2530")

    assert response.status_code == 200
    data = response.get_json()
    assert data["course_code"] == "CDOM4400"
    assert data["course_title"] == "Clean Course Domain"
    assert data["credit"] == 4
    assert [section["section_id"] for section in data["sections"]] == [
        "CDOM4400-L01",
        "CDOM4400-LA1",
    ]
    assert data["sections"][0]["lectures"][0]["instructor"] == "Dr. Domain"


def test_scheduler_cart_writes_offering_cart_and_section_selections(test_client, test_app):
    _, offering_id = seed_new_domain_course(test_app)
    headers, user_id = auth_headers(test_app)

    response = test_client.post(
        "/scheduler/cart/2530/add",
        json={"course_code": "CDOM4400"},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["course_code"] == "CDOM4400"
    assert data["enabled"] is False
    assert list(data["layers"]) == ["0", "1"]

    with test_app.app_context():
        assert UserOfferingCart.query.filter_by(user_id=user_id, offering_id=offering_id).count() == 1
        assert UserSectionSelection.query.filter_by(user_id=user_id, offering_id=offering_id).count() == 2

    toggle = test_client.put(
        "/scheduler/cart/2530/bundle/CDOM4400/1/1/toggle",
        json={"enabled": False},
        headers=headers,
    )

    assert toggle.status_code == 200
    assert toggle.get_json() == {"id": 1, "layer": 1, "enabled": False}
    with test_app.app_context():
        disabled = (
            UserSectionSelection.query
            .filter_by(user_id=user_id, offering_id=offering_id, enabled=False)
            .count()
        )
        assert disabled == 1
