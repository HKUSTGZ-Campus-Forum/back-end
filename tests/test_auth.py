# tests/test_auth.py
import pytest
from app import create_app, db
from app.models.user_role import UserRole


class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    JWT_SECRET_KEY = 'test-secret'
    CACHE_TYPE = 'SimpleCache'
    AUTO_INIT_ON_STARTUP = False


@pytest.fixture
def client():
    app = create_app(TestConfig)

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            db.session.add(UserRole(name=UserRole.USER))
            db.session.commit()
        yield client


@pytest.mark.parametrize(
    "path",
    [
        "/auth/login",
        "/auth/register",
        "/auth/forgot-password",
        "/auth/reset-password",
        "/auth/change-password",
        "/users",
    ],
)
def test_password_authentication_endpoints_are_gone(client, path):
    response = client.post(path, json={})

    assert response.status_code == 410
    assert response.get_json() == {
        "code": "sso_only",
        "msg": "Password authentication is no longer available. Use HKUST(GZ) SSO.",
    }
    assert response.headers["Cache-Control"] == "no-store"
