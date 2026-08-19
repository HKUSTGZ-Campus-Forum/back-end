from urllib.parse import parse_qs, urlparse

import pytest
from flask import redirect

from app import create_app
from app.extensions import db
from app.models.oidc_identity import OidcIdentity, OidcLoginTicket
from app.models.user import User
from app.models.user_role import UserRole


class OidcTestConfig:
    TESTING = True
    SECRET_KEY = "oidc-test-session-secret"
    JWT_SECRET_KEY = "oidc-test-jwt-secret-with-at-least-32-bytes"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    CACHE_TYPE = "SimpleCache"
    AUTO_INIT_ON_STARTUP = False
    ENABLE_BACKGROUND_TASKS = False
    FRONTEND_BASE_URL = "https://unikorn.hkust-gz.edu.cn"
    CAMPUS_SSO_ENABLED = True
    CAMPUS_SSO_CLIENT_ID = "unikorn-test-client"
    CAMPUS_SSO_CLIENT_SECRET = "test-secret"
    CAMPUS_SSO_ISSUER = "https://devsso.hkust-gz.edu.cn"
    CAMPUS_SSO_METADATA_URL = (
        "https://devsso.hkust-gz.edu.cn/.well-known/openid-configuration"
    )
    CAMPUS_SSO_END_SESSION_ENDPOINT = (
        "https://devsso.hkust-gz.edu.cn/connect/endsession"
    )
    CAMPUS_SSO_REDIRECT_URI = (
        "https://unikorn.hkust-gz.edu.cn/api/auth/oidc/callback"
    )
    CAMPUS_SSO_POST_LOGOUT_REDIRECT_URI = (
        "https://unikorn.hkust-gz.edu.cn/"
    )
    CAMPUS_SSO_SCOPES = "openid profile"
    CAMPUS_SSO_LOGIN_TICKET_TTL_SECONDS = 120
    CAMPUS_SSO_ID_TOKEN_COOKIE_NAME = "test_oidc_id_token"
    CAMPUS_SSO_COOKIE_PATH = "/auth"
    CAMPUS_SSO_COOKIE_SECURE = False
    SESSION_COOKIE_SECURE = False


class FakeUserInfoResponse:
    def __init__(self, claims):
        self.claims = claims

    def raise_for_status(self):
        return None

    def json(self):
        return self.claims


class FakeOidcClient:
    def __init__(self, claims=None, id_token_claims=None):
        self.claims = claims or {}
        self.id_token_claims = id_token_claims or dict(self.claims)
        self.authorize_kwargs = None

    def authorize_redirect(self, redirect_uri, **kwargs):
        self.authorize_kwargs = {"redirect_uri": redirect_uri, **kwargs}
        return redirect("https://devsso.hkust-gz.edu.cn/connect/authorize")

    def authorize_access_token(self):
        return {
            "access_token": "school-access-token",
            "id_token": "school-id-token",
            "expires_in": 3600,
            "userinfo": self.id_token_claims,
        }

    def post(self, endpoint, token):
        assert endpoint == "userinfo"
        assert token["access_token"] == "school-access-token"
        return FakeUserInfoResponse(self.claims)


@pytest.fixture
def app():
    application = create_app(OidcTestConfig)
    with application.app_context():
        db.create_all()
        db.session.add(UserRole(name=UserRole.USER))
        db.session.commit()
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def school_claims(**overrides):
    claims = {
        "sub": "school-user-001",
        "name": "wtao565",
        "display_name": "Test Student",
        "type": "student",
        "email": "wtao565@connect.hkust-gz.edu.cn",
        "department": "DSTE",
        "emp_id": "20990001",
    }
    claims.update(overrides)
    return claims


def install_fake_client(monkeypatch, claims=None, id_token_claims=None):
    fake = FakeOidcClient(claims or school_claims(), id_token_claims)
    monkeypatch.setattr(
        "app.routes.oidc.get_campus_oidc_client",
        lambda: fake,
    )
    return fake


def callback_and_get_code(client):
    with client.session_transaction() as session:
        session["campus_oidc_return_to"] = "/courses/planner"
        session["campus_oidc_locale"] = "zh"

    response = client.get("/auth/oidc/callback?code=provider-code&state=test")
    assert response.status_code == 303
    query = parse_qs(urlparse(response.location).query)
    return response, query["oidc_code"][0]


def test_status_is_disabled_without_client_credentials():
    class DisabledConfig(OidcTestConfig):
        CAMPUS_SSO_ENABLED = False
        CAMPUS_SSO_CLIENT_ID = ""
        CAMPUS_SSO_CLIENT_SECRET = ""

    application = create_app(DisabledConfig)
    response = application.test_client().get("/auth/oidc/status")

    assert response.status_code == 200
    assert response.get_json()["enabled"] is False
    assert response.headers["Cache-Control"] == "no-store"


def test_login_uses_fixed_callback_pkce_client_and_safe_return_path(
    client,
    monkeypatch,
):
    fake = install_fake_client(monkeypatch)

    response = client.get(
        "/auth/oidc/login?locale=en&return_to=https://evil.example/steal"
    )

    assert response.status_code == 302
    assert fake.authorize_kwargs == {
        "redirect_uri": OidcTestConfig.CAMPUS_SSO_REDIRECT_URI,
        "response_type": "code",
        "response_mode": "query",
    }
    with client.session_transaction() as session:
        assert session["campus_oidc_return_to"] == "/en"
        assert session["campus_oidc_locale"] == "en"


def test_callback_provisions_account_and_ticket_is_single_use(
    app,
    client,
    monkeypatch,
):
    install_fake_client(monkeypatch)
    callback_response, code = callback_and_get_code(client)

    assert "test_oidc_id_token=school-id-token" in callback_response.headers.get(
        "Set-Cookie", ""
    )

    exchange = client.post("/auth/oidc/exchange", json={"code": code})
    assert exchange.status_code == 200
    payload = exchange.get_json()
    assert payload["access_token"]
    assert payload["refresh_token"]
    assert payload["return_to"] == "/courses/planner"
    assert payload["user"]["email"] == "wtao565@connect.hkust-gz.edu.cn"

    repeated = client.post("/auth/oidc/exchange", json={"code": code})
    assert repeated.status_code == 400
    assert repeated.get_json()["code"] == "invalid_login_ticket"

    with app.app_context():
        user = User.query.one()
        assert user.email_verified is True
        identity = OidcIdentity.query.one()
        assert identity.user_id == user.id
        assert identity.subject == "school-user-001"
        assert identity.department == "DSTE"
        assert OidcLoginTicket.query.one().consumed_at is not None


def test_verified_existing_account_is_linked_instead_of_duplicated(
    app,
    client,
    monkeypatch,
):
    with app.app_context():
        role = UserRole.query.filter_by(name=UserRole.USER).one()
        existing = User(
            username="existing_user",
            email="wtao565@connect.hkust-gz.edu.cn",
            email_verified=True,
            role_id=role.id,
        )
        existing.set_password("existing-password")
        db.session.add(existing)
        db.session.commit()
        existing_id = existing.id

    install_fake_client(monkeypatch)
    _, code = callback_and_get_code(client)
    exchange = client.post("/auth/oidc/exchange", json={"code": code})

    assert exchange.status_code == 200
    assert exchange.get_json()["user"]["id"] == existing_id
    with app.app_context():
        assert User.query.count() == 1
        assert OidcIdentity.query.one().user_id == existing_id


def test_unverified_existing_email_is_not_silently_linked(
    app,
    client,
    monkeypatch,
):
    with app.app_context():
        role = UserRole.query.filter_by(name=UserRole.USER).one()
        existing = User(
            username="unverified_user",
            email="wtao565@connect.hkust-gz.edu.cn",
            email_verified=False,
            role_id=role.id,
        )
        existing.set_password("existing-password")
        db.session.add(existing)
        db.session.commit()

    install_fake_client(monkeypatch)
    with client.session_transaction() as session:
        session["campus_oidc_locale"] = "zh"
    response = client.get("/auth/oidc/callback?code=provider-code&state=test")

    assert response.status_code == 303
    assert parse_qs(urlparse(response.location).query)["oidc_error"] == [
        "account_conflict"
    ]
    with app.app_context():
        assert OidcIdentity.query.count() == 0
        assert OidcLoginTicket.query.count() == 0


def test_callback_rejects_mismatched_id_token_and_userinfo_subjects(
    app,
    client,
    monkeypatch,
):
    install_fake_client(
        monkeypatch,
        claims=school_claims(sub="userinfo-user"),
        id_token_claims=school_claims(sub="id-token-user"),
    )
    response = client.get("/auth/oidc/callback?code=provider-code&state=test")

    assert response.status_code == 303
    assert parse_qs(urlparse(response.location).query)["oidc_error"] == [
        "invalid_response"
    ]
    with app.app_context():
        assert User.query.count() == 0


def test_local_logout_returns_provider_logout_url(
    client,
    monkeypatch,
):
    install_fake_client(monkeypatch)
    _, code = callback_and_get_code(client)
    exchange = client.post("/auth/oidc/exchange", json={"code": code})
    access_token = exchange.get_json()["access_token"]

    response = client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    logout_url = response.get_json()["oidc_logout_url"]
    query = parse_qs(urlparse(logout_url).query)
    assert query["id_token_hint"] == ["school-id-token"]
    assert query["post_logout_redirect_uri"] == [
        OidcTestConfig.CAMPUS_SSO_POST_LOGOUT_REDIRECT_URI
    ]
