import pytest
from flask_jwt_extended import create_access_token

from app import create_app
from app.config import Config
from app.extensions import db
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.models.user_role import UserRole
from app.services.push_service import PushService
from app.utils.push_endpoints import is_valid_push_endpoint


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    CACHE_TYPE = "SimpleCache"
    AUTO_INIT_ON_STARTUP = False
    ENABLE_BACKGROUND_TASKS = False
    VAPID_PRIVATE_KEY = "test-private-key"
    VAPID_PUBLIC_KEY = "test-public-key"
    VAPID_EMAIL = "push-test@example.com"


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def create_user(username, role_name=UserRole.USER):
    role = UserRole.query.filter_by(name=role_name).first()
    if role is None:
        role = UserRole(name=role_name, description=f"{role_name} role")
        db.session.add(role)
        db.session.flush()

    user = User(
        username=username,
        email=f"{username}@connect.hkust-gz.edu.cn",
        role_id=role.id,
        email_verified=True,
        password_hash="test-password-hash",
    )
    db.session.add(user)
    db.session.commit()
    return user


def auth_headers(user_id):
    token = create_access_token(identity=str(user_id))
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fcm.googleapis.com/fcm/send/browser-token",
        "https://updates.push.services.mozilla.com/wpush/v2/browser-token",
        "https://web.push.apple.com/QP-browser-token",
        "https://fcm.googleapis.com:443/fcm/send/browser-token?key=value",
    ],
)
def test_current_web_push_provider_endpoints_are_accepted(endpoint):
    assert is_valid_push_endpoint(endpoint) is True


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/token",
        "https://localhost/push/token",
        "https://127.0.0.1/push/token",
        "https://10.121.15.222/push/token",
        "https://push.example.com/push/token",
        "https://fcm.googleapis.com.evil.example/push/token",
        "https://user@fcm.googleapis.com/fcm/send/token",
        "https://fcm.googleapis.com:8443/fcm/send/token",
        "https://fcm.googleapis.com/fcm/send/token#fragment",
    ],
)
def test_unsafe_or_unknown_push_endpoints_are_rejected(endpoint):
    assert is_valid_push_endpoint(endpoint) is False


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fcm.googleapis.com/fcm/send/browser-token",
        "https://updates.push.services.mozilla.com/wpush/v2/browser-token",
        "https://web.push.apple.com/QP-browser-token",
    ],
)
def test_subscribe_accepts_known_web_push_providers(app, client, endpoint):
    with app.app_context():
        user = create_user("push_subscriber")
        headers = auth_headers(user.id)

    response = client.post(
        "/push/subscribe",
        headers=headers,
        json={
            "endpoint": endpoint,
            "keys": {"p256dh": "valid-P256dh_key", "auth": "valid-auth_key"},
        },
    )

    assert response.status_code == 201
    with app.app_context():
        assert PushSubscription.query.filter_by(endpoint=endpoint, is_active=True).count() == 1


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://localhost/push/token",
        "https://192.168.1.10/push/token",
        "https://push.example.com/push/token",
    ],
)
def test_subscribe_rejects_unsafe_endpoint_without_storing_it(app, client, endpoint):
    with app.app_context():
        user = create_user("unsafe_push_subscriber")
        headers = auth_headers(user.id)

    response = client.post(
        "/push/subscribe",
        headers=headers,
        json={
            "endpoint": endpoint,
            "keys": {"p256dh": "valid-key", "auth": "valid-auth"},
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid push subscription"}
    with app.app_context():
        assert PushSubscription.query.count() == 0


def test_subscribe_bounds_key_and_user_agent_values(app, client):
    with app.app_context():
        user = create_user("bounded_push_subscriber")
        headers = auth_headers(user.id)

    rejected = client.post(
        "/push/subscribe",
        headers=headers,
        json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/rejected-token",
            "keys": {"p256dh": "a" * 513, "auth": "valid-auth"},
        },
    )
    assert rejected.status_code == 400

    accepted = client.post(
        "/push/subscribe",
        headers={**headers, "User-Agent": "browser/" + ("x" * 800)},
        json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/accepted-token",
            "keys": {"p256dh": "valid-key", "auth": "valid-auth"},
        },
    )
    assert accepted.status_code == 201
    with app.app_context():
        assert len(PushSubscription.query.one().user_agent) == 512


def test_send_revalidates_and_deactivates_restored_malicious_endpoint(
    app, monkeypatch
):
    webpush_calls = []

    def fail_if_called(**kwargs):
        webpush_calls.append(kwargs)
        raise AssertionError("webpush must not receive an unsafe endpoint")

    monkeypatch.setattr("app.services.push_service.webpush", fail_if_called)

    with app.app_context():
        user = create_user("restored_push_subscriber")
        subscription = PushSubscription(
            user_id=user.id,
            endpoint="https://127.0.0.1/internal/admin",
            p256dh_key="restored-key",
            auth_key="restored-auth",
            is_active=True,
        )
        db.session.add(subscription)
        db.session.commit()
        subscription_id = subscription.id

        result = PushService.send_notification_to_user(user.id, {"title": "test"})

        assert result["success"] is False
        assert result["successful_sends"] == 0
        assert result["results"] == [{
            "subscription_id": subscription_id,
            "success": False,
            "error": "Invalid push subscription",
        }]
        assert db.session.get(PushSubscription, subscription_id).is_active is False
    assert webpush_calls == []


def test_target_user_push_test_denies_non_admin_before_sending(app, client, monkeypatch):
    push_calls = []
    monkeypatch.setattr(
        PushService,
        "test_push_notification",
        staticmethod(lambda user_id: push_calls.append(user_id) or {"success": True}),
    )

    with app.app_context():
        user = create_user("ordinary_push_tester")
        headers = auth_headers(user.id)

    response = client.post("/push/test/999", headers=headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Admin access required"}
    assert push_calls == []


def test_target_user_push_test_allows_admin(app, client, monkeypatch):
    push_calls = []
    monkeypatch.setattr(
        PushService,
        "test_push_notification",
        staticmethod(lambda user_id: push_calls.append(user_id) or {"success": True}),
    )

    with app.app_context():
        admin = create_user("admin_push_tester", UserRole.ADMIN)
        headers = auth_headers(admin.id)

    response = client.post("/push/test/777", headers=headers)

    assert response.status_code == 200
    assert push_calls == [777]
