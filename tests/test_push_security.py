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


def add_subscription(user_id, suffix):
    sub = PushSubscription(user_id=user_id, endpoint=f"https://web.push.apple.com/{suffix}",
                           p256dh_key="valid-key", auth_key="valid-auth", is_active=True)
    db.session.add(sub)
    db.session.commit()
    return sub


def test_device_test_cannot_target_another_users_subscription(app, client, monkeypatch):
    calls = []
    monkeypatch.setattr(PushService, "test_push_notification", staticmethod(lambda *a, **kw: calls.append(kw)))
    with app.app_context():
        owner = create_user("device_owner")
        other = create_user("device_other")
        endpoint = add_subscription(owner.id, "owned").endpoint
        headers = auth_headers(other.id)
    result = client.post('/push/test', headers=headers, json={"endpoint": endpoint})
    assert result.status_code == 404
    assert calls == []


def test_device_test_targets_only_one_owned_device(app, client, monkeypatch):
    calls = []
    class Accepted:
        status_code = 201
    monkeypatch.setattr("app.services.push_service.webpush", lambda **kw: calls.append(kw) or Accepted())
    with app.app_context():
        user = create_user("two_devices")
        endpoint = add_subscription(user.id, "iphone").endpoint
        add_subscription(user.id, "desktop")
        headers = auth_headers(user.id)
    result = client.post('/push/test', headers=headers, json={"endpoint": endpoint, "locale": "en"})
    assert result.status_code == 200
    assert len(calls) == 1
    assert calls[0]['subscription_info']['endpoint'] == endpoint
    assert calls[0]['timeout'] == 8
    import json
    payload = json.loads(calls[0]['data'])
    assert payload['title'] == 'UniKorn test notification'
    assert payload['data']['url'] == '/en/notifications'


@pytest.mark.parametrize('status', [404, 410])
def test_expired_subscription_is_deactivated_even_for_falsey_http_error(app, monkeypatch, status):
    import requests
    from pywebpush import WebPushException
    response = requests.Response()
    response.status_code = status
    def expired(**kwargs):
        raise WebPushException('expired', response=response)
    monkeypatch.setattr('app.services.push_service.webpush', expired)
    with app.app_context():
        user = create_user('expired_device')
        sub = add_subscription(user.id, 'expired')
        result = PushService.send_notification_to_user(user.id, {'title': 'test'})
        assert result['success'] is False
        assert sub.is_active is False
        assert result['results'][0]['status_code'] == status


def test_registering_device_for_new_account_stops_previous_account_delivery(app, client):
    with app.app_context():
        old_user = create_user('old_account')
        new_user = create_user('new_account')
        sub = add_subscription(old_user.id, 'shared-device')
        endpoint, old_id = sub.endpoint, sub.id
        headers = auth_headers(new_user.id)
    for _ in range(2):
        response = client.post('/push/subscribe', headers=headers, json={
            'endpoint': endpoint, 'keys': {'p256dh': 'valid-key', 'auth': 'valid-auth'},
        })
        assert response.status_code == 201
    with app.app_context():
        assert db.session.get(PushSubscription, old_id).is_active is False
        assert PushSubscription.query.filter_by(endpoint=endpoint, is_active=True).count() == 1
        assert PushSubscription.query.filter_by(endpoint=endpoint).count() == 2


def test_marking_read_never_sends_empty_or_silent_push(app, client, monkeypatch):
    from app.models.notification import Notification
    calls = []
    monkeypatch.setattr(PushService, 'send_notification_to_user', staticmethod(lambda *a, **kw: calls.append(a)))
    with app.app_context():
        user = create_user('reading_device')
        notification = Notification(recipient_id=user.id, type='post_comment', title='A reply', message='Hello')
        db.session.add(notification)
        db.session.commit()
        notification_id = notification.id
        headers = auth_headers(user.id)
    assert client.put(f'/notifications/{notification_id}/read', headers=headers).status_code == 200
    assert client.put('/notifications/mark-all-read', headers=headers).status_code == 200
    assert calls == []


def test_missing_private_key_reports_unavailable(app, client):
    app.config['VAPID_PRIVATE_KEY'] = None
    assert client.get('/push/vapid-public-key').status_code == 500


def test_unsubscribe_is_idempotent_and_does_not_affect_other_devices(app, client):
    with app.app_context():
        user = create_user('disable_device')
        first = add_subscription(user.id, 'one')
        endpoint, first_id = first.endpoint, first.id
        second_id = add_subscription(user.id, 'two').id
        headers = auth_headers(user.id)
    for _ in range(2):
        assert client.post('/push/unsubscribe', headers=headers, json={'endpoint': endpoint}).status_code == 200
    with app.app_context():
        assert db.session.get(PushSubscription, first_id).is_active is False
        assert db.session.get(PushSubscription, second_id).is_active is True
