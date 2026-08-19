import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.post import Post
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
def client(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for proxy_key in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"]:
        monkeypatch.delenv(proxy_key, raising=False)
    app = create_app(TestConfig)
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            role = UserRole.query.filter_by(name=UserRole.USER).first()
            if role is None:
                role = UserRole(name=UserRole.USER, description="user role")
                db.session.add(role)
                db.session.flush()
            user = User(
                username="preview-author",
                email="preview-author@example.com",
                role_id=role.id,
                email_verified=True,
            )
            user.set_password("password")
            db.session.add(user)
            db.session.flush()
            for index in range(4):
                db.session.add(Post(
                    user_id=user.id,
                    title=f"Honor result {index}",
                    content="Honor course discussion",
                ))
            db.session.commit()
        yield client


def test_global_search_limits_post_preview_to_two(client):
    response = client.get("/search/global", query_string={"q": "honor"})

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["results"]["posts"]) == 2
