from datetime import datetime, timedelta, timezone

import pytest
from flask_jwt_extended import create_access_token

from app import create_app
from app.extensions import cache, db
from app.models.user import User
from app.models.user_role import UserRole
from app.routes.auth import is_hkust_email
from app.services.email_service import EmailService
from app.services.institutional_email import is_institutional_email, normalize_email


class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    JWT_SECRET_KEY = "email-integrity-test-secret"
    CACHE_TYPE = "SimpleCache"
    AUTO_INIT_ON_STARTUP = False


class StubEmailService:
    def __init__(self):
        self.sent = []
        self.next_code = "654321"

    def generate_verification_code(self):
        return self.next_code

    def send_verification_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"success": True}


@pytest.fixture
def app(monkeypatch):
    app = create_app(TestConfig)
    email_service = StubEmailService()
    monkeypatch.setattr(
        EmailService,
        "from_app_config",
        staticmethod(lambda: email_service),
    )
    monkeypatch.setattr(
        "app.routes.auth.content_moderation.moderate_text",
        lambda **_kwargs: {"is_safe": True, "reason": None, "risk_level": "low"},
    )

    with app.app_context():
        db.create_all()
        db.session.add(UserRole(name=UserRole.USER))
        db.session.commit()
        cache.clear()

    app.email_service = email_service
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def create_user(
    app,
    username,
    email,
    *,
    verified=False,
    code="123456",
    created_at=None,
):
    with app.app_context():
        role = UserRole.query.filter_by(name=UserRole.USER).one()
        user = User(
            username=username,
            email=email,
            email_verified=verified,
            role_id=role.id,
            created_at=created_at or datetime.now(timezone.utc),
        )
        user.set_password("password")
        if not verified and code:
            user.set_email_verification_code(code)
        db.session.add(user)
        db.session.commit()
        return user.id


def authorization_header(app, user_id):
    with app.app_context():
        token = create_access_token(identity=str(user_id))
    return {"Authorization": f"Bearer {token}"}


def test_institutional_email_validation_is_normalized_and_backward_compatible():
    email = "  Student.Name@CONNECT.HKUST-GZ.EDU.CN "
    assert normalize_email(email) == "student.name@connect.hkust-gz.edu.cn"
    assert is_institutional_email(email)
    assert is_hkust_email(email)
    assert not is_institutional_email("student@connect.hkust-gz.edu.cn.example.com")


def test_register_normalizes_email_and_rejects_case_insensitive_duplicate(app, client):
    create_user(app, "legacy", "Legacy.Student@HKUST-GZ.EDU.CN")

    duplicate = client.post("/auth/register", json={
        "username": "duplicate",
        "password": "password",
        "email": "legacy.student@hkust-gz.edu.cn",
    })
    assert duplicate.status_code == 400
    assert duplicate.get_json()["msg"] == "Email already registered"

    created = client.post("/auth/register", json={
        "username": "newstudent",
        "password": "password",
        "email": " New.Student@CONNECT.HKUST-GZ.EDU.CN ",
    })
    assert created.status_code == 201

    with app.app_context():
        user = User.query.filter_by(username="newstudent").one()
        assert user.email == "new.student@connect.hkust-gz.edu.cn"


def test_update_email_resets_verification_and_clears_old_code(app, client):
    user_id = create_user(app, "verified", "old@hkust-gz.edu.cn", verified=True)
    with app.app_context():
        user = db.session.get(User, user_id)
        user.email_verification_code = "111111"
        user.email_verification_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        db.session.commit()

    response = client.put(
        f"/users/{user_id}",
        json={"email": " New.Address@CONNECT.HKUST-GZ.EDU.CN "},
        headers=authorization_header(app, user_id),
    )
    assert response.status_code == 200

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.email == "new.address@connect.hkust-gz.edu.cn"
        assert user.email_verified is False
        assert user.email_verification_code is None
        assert user.email_verification_expires_at is None


def test_case_only_update_preserves_verified_principal(app, client):
    user_id = create_user(
        app,
        "mixedcase",
        "Student@CONNECT.HKUST-GZ.EDU.CN",
        verified=True,
    )

    response = client.put(
        f"/users/{user_id}",
        json={"email": "student@connect.hkust-gz.edu.cn"},
        headers=authorization_header(app, user_id),
    )
    assert response.status_code == 200

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.email == "student@connect.hkust-gz.edu.cn"
        assert user.email_verified is True


def test_update_email_rejects_domain_and_case_insensitive_duplicate(app, client):
    user_id = create_user(app, "target", "target@hkust-gz.edu.cn", verified=True)
    create_user(app, "owner", "Taken@CONNECT.HKUST-GZ.EDU.CN", verified=True)
    headers = authorization_header(app, user_id)

    external = client.put(
        f"/users/{user_id}",
        json={"email": "target@example.com"},
        headers=headers,
    )
    duplicate = client.put(
        f"/users/{user_id}",
        json={"email": "taken@connect.hkust-gz.edu.cn"},
        headers=headers,
    )

    assert external.status_code == 400
    assert duplicate.status_code == 400
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.email == "target@hkust-gz.edu.cn"
        assert user.email_verified is True


def test_add_email_normalizes_and_rejects_case_insensitive_duplicate(app, client):
    user_id = create_user(app, "noemail", None, code=None)
    create_user(app, "owner", "Owner@HKUST-GZ.EDU.CN")
    headers = authorization_header(app, user_id)

    duplicate = client.post(
        f"/users/{user_id}/add-email",
        json={"email": "owner@hkust-gz.edu.cn"},
        headers=headers,
    )
    assert duplicate.status_code == 400

    added = client.post(
        f"/users/{user_id}/add-email",
        json={"email": " Student@CONNECT.HKUST-GZ.EDU.CN "},
        headers=headers,
    )
    assert added.status_code == 200
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.email == "student@connect.hkust-gz.edu.cn"
        assert user.email_verified is False
        assert user.email_verification_code == "654321"


def test_verify_email_rejects_noninstitutional_and_noncanonical_accounts(app, client):
    external_id = create_user(app, "external", "external@example.com")
    external = client.post("/auth/verify-email", json={
        "user_id": external_id,
        "verification_code": "123456",
    })
    assert external.status_code == 400

    oldest = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first_id = create_user(
        app,
        "first",
        "shared@hkust-gz.edu.cn",
        created_at=oldest,
    )
    second_id = create_user(
        app,
        "second",
        "SHARED@HKUST-GZ.EDU.CN",
        created_at=oldest + timedelta(seconds=1),
    )

    second = client.post("/auth/verify-email", json={
        "user_id": second_id,
        "verification_code": "123456",
    })
    first = client.post("/auth/verify-email", json={
        "user_id": first_id,
        "verification_code": "123456",
    })

    assert second.status_code == 409
    assert first.status_code == 200
    with app.app_context():
        assert db.session.get(User, first_id).email_verified is True
        assert db.session.get(User, second_id).email_verified is False


def test_verify_email_throttles_failed_codes_and_success_clears_attempts(app, client):
    successful_id = create_user(app, "eventualsuccess", "success@hkust-gz.edu.cn")

    failed = client.post("/auth/verify-email", json={
        "user_id": successful_id,
        "verification_code": "000000",
    })
    assert failed.status_code == 400
    with app.app_context():
        assert cache.get(f"email-verification:failures:{successful_id}") == 1

    success = client.post("/auth/verify-email", json={
        "user_id": successful_id,
        "verification_code": "123456",
    })
    assert success.status_code == 200
    with app.app_context():
        assert cache.get(f"email-verification:failures:{successful_id}") is None

    throttled_id = create_user(app, "throttled", "throttled@hkust-gz.edu.cn")
    for _ in range(5):
        response = client.post("/auth/verify-email", json={
            "user_id": throttled_id,
            "verification_code": "000000",
        })
        assert response.status_code == 400

    response = client.post("/auth/verify-email", json={
        "user_id": throttled_id,
        "verification_code": "123456",
    })
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "900"


def test_resend_verification_has_per_user_cooldown(app, client):
    user_id = create_user(app, "resend", "resend@hkust-gz.edu.cn")

    first = client.post("/auth/resend-verification", json={"user_id": user_id})
    second = client.post("/auth/resend-verification", json={"user_id": user_id})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "60"
    assert len(app.email_service.sent) == 1
