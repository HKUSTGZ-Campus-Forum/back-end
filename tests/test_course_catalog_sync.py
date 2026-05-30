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
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    for proxy_key in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"]:
        monkeypatch.delenv(proxy_key, raising=False)
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_sync_course_catalog_upserts_course_rows(app):
    from app.services.course_catalog_sync import sync_course_catalog_from_payload

    payload = {
        "courses": [
            {
                "course_code": "AIAA1010",
                "course_title": "Academic Orientation for AI Students",
                "credit": "1",
                "course_desc": "Official description.",
            }
        ]
    }

    with app.app_context():
        result = sync_course_catalog_from_payload(payload)
        course = Course.query.filter_by(code="AIAA1010").one()

    assert result["upserted"] == 1
    assert course.name == "Academic Orientation for AI Students"
    assert course.credits == 1
    assert course.description == "Official description."


def test_sync_course_catalog_updates_existing_spaced_course_code(app):
    from app.services.course_catalog_sync import sync_course_catalog_from_payload

    payload = {
        "courses": [
            {
                "course_code": "AIAA1010",
                "course_title": "Academic Orientation for AI Students",
                "credit": "1",
                "course_desc": "Official description.",
            }
        ]
    }

    with app.app_context():
        db.session.add(Course(code="AIAA 1010", name="Old title", credits=0, is_active=True, is_deleted=False))
        db.session.commit()
        before_count = Course.query.count()

        result = sync_course_catalog_from_payload(payload)
        after_count = Course.query.count()
        spaced_course = Course.query.filter_by(code="AIAA 1010").one()
        catalog_course = Course.query.filter_by(code="AIAA1010").one()

    assert result["upserted"] == 1
    assert after_count == before_count
    assert spaced_course.name == "Academic Orientation for AI Students"
    assert spaced_course.credits == 1
    assert spaced_course.description == "Official description."
    assert catalog_course.description == "Official description."
