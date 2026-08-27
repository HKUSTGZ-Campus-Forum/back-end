import pytest
from flask_jwt_extended import create_access_token

from app import create_app
from app.extensions import db
from app.models.user import User
from app.models.user_role import UserRole


class TestConfig:
    TESTING = True
    SECRET_KEY = "meetcampus-test-secret"
    JWT_SECRET_KEY = "meetcampus-test-jwt-secret-with-at-least-32-bytes"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    CACHE_TYPE = "SimpleCache"
    AUTO_INIT_ON_STARTUP = False
    ENABLE_BACKGROUND_TASKS = False
    CAMPUS_SSO_ENABLED = False
    MEETCAMPUS_BETA_EMAILS = ("wtao565@connect.hkust-gz.edu.cn",)


@pytest.fixture
def app():
    application = create_app(TestConfig)
    with application.app_context():
        db.create_all()
        db.session.add(UserRole(name=UserRole.USER))
        db.session.commit()
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def create_user(app, *, email, verified=True, deleted=False):
    with app.app_context():
        role = UserRole.query.filter_by(name=UserRole.USER).one()
        user = User(
            username=f"meet-{User.query.count() + 1}",
            email=email,
            email_verified=verified,
            role_id=role.id,
            is_deleted=deleted,
        )
        user.set_password("unused-sso-placeholder")
        db.session.add(user)
        db.session.commit()
        return user.id


def auth_headers(app, user_id):
    with app.app_context():
        token = create_access_token(identity=str(user_id))
    return {"Authorization": f"Bearer {token}"}


def test_bootstrap_requires_authentication(client):
    response = client.get("/meetcampus/bootstrap")
    assert response.status_code == 401


def test_bootstrap_denies_non_invited_and_unverified_accounts(app, client):
    other_id = create_user(app, email="other@connect.hkust-gz.edu.cn")
    unverified_id = create_user(
        app,
        email="wtao565@connect.hkust-gz.edu.cn",
        verified=False,
    )

    for user_id in (other_id, unverified_id):
        response = client.get(
            "/meetcampus/bootstrap",
            headers=auth_headers(app, user_id),
        )
        assert response.status_code == 403
        assert response.get_json()["code"] == "meetcampus_beta_required"
        assert response.headers["Cache-Control"] == "no-store"


def test_bootstrap_allows_only_normalized_invited_email(app, client):
    invited_id = create_user(
        app,
        email="  WTAO565@CONNECT.HKUST-GZ.EDU.CN  ",
    )

    response = client.get(
        "/meetcampus/bootstrap",
        headers=auth_headers(app, invited_id),
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["feature"] == {
        "id": "meetcampus",
        "stage": "private_beta",
        "mode": "guided_sandbox",
        "sessionStorage": "browser_local",
        "liveAgents": False,
        "realPeople": False,
        "autonomousAgentDecisions": False,
    }
    assert {scenario["id"] for scenario in payload["scenarios"]} == {
        "study",
        "dining",
        "activity",
    }
    assert len(payload["locations"]) == 5
    assert response.headers["Cache-Control"] == "private, no-store"
