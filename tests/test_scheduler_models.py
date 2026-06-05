import os

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.course import Course


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


def test_course_has_scheduler_fields(app):
    with app.app_context():
        course = Course(
            code="TEST9999",
            name="Test Scheduler Course",
            credits=3,
            subject="TEST",
            catalog_number="9999",
            course_title_abbr="Test Sched Crse",
            pre_requirement="",
            co_requirement="",
            exclusion="",
            pg_course=False,
            klms_course=False,
        )
        db.session.add(course)
        db.session.commit()

        loaded = Course.query.filter_by(code="TEST9999").first()
        assert loaded.subject == "TEST"
        assert loaded.catalog_number == "9999"
        assert loaded.course_title_abbr == "Test Sched Crse"
        assert loaded.pg_course is False


from app.models.scheduler_section import SchedulerSection
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_map import SchedulerMapComponent, SchedulerMapLine
from app.models.scheduler_cart import SchedulerUserCourseCart, SchedulerUserBundleCart
from app.models.user import User
from app.models.user_role import UserRole


def test_section_and_lecture_models(app):
    with app.app_context():
        course = Course.query.filter_by(is_deleted=False).first()
        if not course:
            course = Course(code="TEST8888", name="Test Course", credits=3)
            db.session.add(course)
            db.session.flush()

        section = SchedulerSection(
            semester_id="2530",
            section_id="L01",
            course_id=course.id,
            name="L01",
            bundle=1,
            layer=0,
            quota=30,
            section_type="L",
            is_main=True,
        )
        db.session.add(section)
        db.session.flush()

        lecture = SchedulerLecture(
            semester_id="2530",
            section_id="L01",
            day=1,
            start_time=900,
            end_time=1030,
            room="Room 101",
            instructor="Dr. Smith",
        )
        db.session.add(lecture)
        db.session.commit()

        loaded_section = SchedulerSection.query.filter_by(semester_id="2530", section_id="L01").first()
        assert loaded_section.course_id == course.id
        assert loaded_section.lectures.count() == 1
        assert loaded_section.lectures.first().instructor == "Dr. Smith"


def test_map_models(app):
    with app.app_context():
        comp1 = SchedulerMapComponent(id="UCUG1001", node_type=True, x_coordinate=100, y_coordinate=200, category=0)
        comp2 = SchedulerMapComponent(id="UCUG1002", node_type=True, x_coordinate=300, y_coordinate=200, category=0)
        db.session.add_all([comp1, comp2])
        db.session.flush()

        line = SchedulerMapLine(start_id="UCUG1001", end_id="UCUG1002", line_type=True, x_coordinate=200, category=1)
        db.session.add(line)
        db.session.commit()

        loaded = SchedulerMapLine.query.first()
        assert loaded.start_id == "UCUG1001"
        assert loaded.end_component.id == "UCUG1002"


def test_cart_models(app):
    with app.app_context():
        # Use a course that exists
        course = Course.query.filter_by(is_deleted=False).first()
        if not course:
            course = Course(code="TEST7777", name="Test Course 2", credits=3)
            db.session.add(course)
            db.session.flush()

        # Ensure a user role exists
        role = UserRole.query.first()
        if not role:
            role = UserRole(name="user")
            db.session.add(role)
            db.session.flush()

        # Create a user
        user = User(username="testuser_scheduler", email="test_scheduler@hkust-gz.edu.cn", email_verified=True, role_id=role.id)
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()

        cart = SchedulerUserCourseCart(
            user_id=user.id,
            semester_id="2530",
            course_code=course.code,
            enabled=True,
        )
        db.session.add(cart)
        db.session.flush()

        bundle = SchedulerUserBundleCart(
            user_id=user.id,
            semester_id="2530",
            course_code=course.code,
            id=1,
            layer=0,
            enabled=True,
        )
        db.session.add(bundle)
        db.session.commit()

        loaded_cart = SchedulerUserCourseCart.query.filter_by(user_id=user.id).first()
        assert loaded_cart.course_code == course.code
        assert loaded_cart.bundles.count() == 1
