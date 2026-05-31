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
