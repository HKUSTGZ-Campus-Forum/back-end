import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
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


def create_user_and_headers(app, username="route_user"):
    with app.app_context():
        role = UserRole.query.filter_by(name=UserRole.USER).first()
        if role is None:
            role = UserRole(name=UserRole.USER, description="user role")
            db.session.add(role)
            db.session.flush()
        user = User(username=username, email=f"{username}@connect.hkust-gz.edu.cn", role_id=role.id, email_verified=True)
        user.password_hash = "test-password-hash"
        db.session.add(user)
        db.session.commit()
        token = create_access_token(identity=str(user.id))
        return user.id, {"Authorization": f"Bearer {token}"}


def test_update_profile_and_get_summary(client, app):
    _user_id, headers = create_user_and_headers(app)

    response = client.put("/academic-map/profile", json={"cohort": "2025", "target_majors": ["AI", "DSBD"]}, headers=headers)
    assert response.status_code == 200
    assert response.get_json()["profile"]["target_majors"] == ["AI", "DSBD"]

    summary = client.get("/academic-map/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.get_json()["profile"]["cohort"] == "2025"


def test_parse_and_save_course_history_without_exposing_grade_publicly(client, app):
    _user_id, headers = create_user_and_headers(app, "import_user")
    pasted = "AIAA 2205 Introduction to AI 2024-25 Summer A+ 3.00"

    parse_response = client.post("/academic-map/import/parse", json={"text": pasted}, headers=headers)
    assert parse_response.status_code == 200
    parsed = parse_response.get_json()["rows"]
    assert parsed[0]["grade"] == "A+"

    save_response = client.post(
        "/academic-map/records/bulk",
        json={"keep_grades": True, "records": parsed},
        headers=headers,
    )
    assert save_response.status_code == 200
    records = save_response.get_json()["records"]
    assert records[0]["grade"] == "A+"

    delete_response = client.delete("/academic-map/grades", headers=headers)
    assert delete_response.status_code == 200
    assert delete_response.get_json()["cleared_count"] == 1
