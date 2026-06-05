import os

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.scheduler_map import SchedulerMapComponent, SchedulerMapLine
from app.services.scheduler_map_seed import seed_scheduler_map_if_empty


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


def sample_seed():
    return {
        "components": [
            {
                "id": "AIAA2205",
                "node_type": None,
                "x_coordinate": 20,
                "y_coordinate": 20,
                "category": 0,
            },
            {
                "id": "(UFUG2601|UFUG2602)",
                "node_type": True,
                "x_coordinate": 20,
                "y_coordinate": 20,
                "category": 1,
            },
        ],
        "lines": [
            {
                "start_id": "(UFUG2601|UFUG2602)",
                "end_id": "AIAA2205",
                "line_type": None,
                "x_coordinate": 10,
                "category": 1,
            }
        ],
    }


def test_seed_scheduler_map_populates_empty_tables(app):
    with app.app_context():
        result = seed_scheduler_map_if_empty(sample_seed())

        assert result["status"] == "seeded"
        assert SchedulerMapComponent.query.count() == 2
        assert SchedulerMapLine.query.count() == 1


def test_seed_scheduler_map_does_not_overwrite_existing_tables(app):
    with app.app_context():
        db.session.add(SchedulerMapComponent(
            id="KEEP",
            node_type=None,
            x_coordinate=1,
            y_coordinate=1,
            category=0,
        ))
        db.session.commit()

        result = seed_scheduler_map_if_empty(sample_seed())

        assert result["status"] == "skipped"
        assert SchedulerMapComponent.query.count() == 1
        assert SchedulerMapComponent.query.filter_by(id="KEEP").one().x_coordinate == 1
        assert SchedulerMapLine.query.count() == 0
