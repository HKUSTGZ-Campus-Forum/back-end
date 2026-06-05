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
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        Course.query.delete()
        db.session.add_all([
            Course(code="DSAA3072", name="Data Science", credits=3, subject="DSAA"),
            Course(code="AIAA3053", name="Artificial Intelligence", credits=3),
            Course(code="AMAT 1010", name="Mathematics", credits=3),
        ])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_course_filters_return_four_letter_course_types(client):
    response = client.get("/courses/filters?lang=zh")

    assert response.status_code == 200
    assert response.get_json()["course_types"] == [
        {"code": "AIAA", "name": "AIAA"},
        {"code": "AMAT", "name": "AMAT"},
        {"code": "DSAA", "name": "DSAA"},
    ]
