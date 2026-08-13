import signal

import pytest
from flask import Flask, request

from app import create_app, _validate_production_secrets
from app.config import get_env_bool, get_env_nonnegative_int
from app.routes import health


class RuntimeTestConfig:
    TESTING = True
    SECRET_KEY = 'runtime-test-secret'
    JWT_SECRET_KEY = 'runtime-test-jwt-secret'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    CACHE_TYPE = 'SimpleCache'
    REDIS_URL = 'redis://unused.example:6379/0'
    AUTO_INIT_ON_STARTUP = False
    ENABLE_BACKGROUND_TASKS = False
    TRUSTED_PROXY_HOPS = 0
    TRUSTED_PROXY_FOR_HOPS = 0
    TRUSTED_PROXY_PROTO_HOPS = 0


@pytest.fixture
def app():
    return create_app(RuntimeTestConfig)


def test_boolean_environment_parser_accepts_common_values(monkeypatch):
    for raw_value in ('1', 'true', 'TRUE', 'yes', 'on'):
        monkeypatch.setenv('RUNTIME_BOOL', raw_value)
        assert get_env_bool('RUNTIME_BOOL') is True

    for raw_value in ('0', 'false', 'FALSE', 'no', 'off'):
        monkeypatch.setenv('RUNTIME_BOOL', raw_value)
        assert get_env_bool('RUNTIME_BOOL') is False


def test_environment_parsers_reject_invalid_values(monkeypatch):
    monkeypatch.setenv('RUNTIME_BOOL', 'truthy')
    with pytest.raises(ValueError, match='RUNTIME_BOOL'):
        get_env_bool('RUNTIME_BOOL')

    monkeypatch.setenv('TRUSTED_PROXY_HOPS', '-1')
    with pytest.raises(ValueError, match='TRUSTED_PROXY_HOPS'):
        get_env_nonnegative_int('TRUSTED_PROXY_HOPS', 1)


def test_create_app_does_not_mutate_data_when_auto_init_is_disabled(monkeypatch):
    mutators = (
        '_auto_init_feedback_support',
        '_auto_init_admin_support',
        '_auto_init_academic_map_support',
        '_auto_init_scheduler_support',
        '_auto_init_course_domain_support',
        '_auto_sync_course_catalog',
        '_auto_sync_academic_curriculum',
        '_auto_seed_scheduler_map',
        '_auto_migrate_gugu_reply_columns',
        '_auto_init_contest',
        '_ensure_mount_admin_role',
        '_seed_dev_feedback',
        '_register_deferred_course_offerings_adjustments',
        '_register_deferred_scheduler_offering_imports',
    )
    calls = []
    import app as app_package

    for name in mutators:
        monkeypatch.setattr(
            app_package,
            name,
            lambda *_args, _name=name, **_kwargs: calls.append(_name),
        )

    create_app(RuntimeTestConfig)

    assert calls == []


def test_healthz_is_dependency_independent(app, monkeypatch):
    def fail_if_called():
        raise AssertionError('liveness must not check external dependencies')

    monkeypatch.setattr(health, '_check_postgresql', fail_if_called)
    monkeypatch.setattr(health, '_check_redis', fail_if_called)

    response = app.test_client().get('/healthz')

    assert response.status_code == 200
    assert response.get_json() == {'status': 'ok'}
    assert response.headers['Cache-Control'] == 'no-store'


def test_readyz_reports_database_and_redis_success(app, monkeypatch):
    monkeypatch.setattr(health, '_check_postgresql', lambda: None)
    monkeypatch.setattr(health, '_check_redis', lambda: None)

    response = app.test_client().get('/readyz')

    assert response.status_code == 200
    assert response.get_json() == {
        'status': 'ready',
        'checks': {
            'postgresql': {'status': 'ok'},
            'redis': {'status': 'ok'},
        },
    }


@pytest.mark.parametrize('failed_dependency', ['postgresql', 'redis'])
def test_readyz_returns_503_without_exposing_dependency_errors(
    app,
    monkeypatch,
    failed_dependency,
):
    def fail():
        raise RuntimeError('credential-bearing internal connection detail')

    monkeypatch.setattr(
        health,
        '_check_postgresql',
        fail if failed_dependency == 'postgresql' else lambda: None,
    )
    monkeypatch.setattr(
        health,
        '_check_redis',
        fail if failed_dependency == 'redis' else lambda: None,
    )

    response = app.test_client().get('/readyz')
    payload = response.get_json()

    assert response.status_code == 503
    assert payload['status'] == 'unavailable'
    assert payload['checks'][failed_dependency] == {'status': 'unavailable'}
    assert 'credential-bearing' not in response.get_data(as_text=True)


def test_production_rejects_default_or_low_entropy_secrets():
    app = Flask(__name__)
    app.config.update(
        APP_ENV='production',
        SECRET_KEY='your_default_secret_key',
        JWT_SECRET_KEY='x' * 64,
    )

    with pytest.raises(RuntimeError, match='SECRET_KEY, JWT_SECRET_KEY'):
        _validate_production_secrets(app)


@pytest.mark.parametrize(
    ('secret_key', 'jwt_secret_key'),
    [
        (
            'REPLACE_WITH_RANDOM_FLASK_SECRET',
            'strong-jwt-secret-for-runtime-test-0123456789',
        ),
        (
            'strong-flask-secret-for-runtime-test-0123456789',
            'Change-Me-With-A-Random-JWT-Secret-0123456789',
        ),
    ],
)
def test_production_rejects_long_placeholder_secrets(secret_key, jwt_secret_key):
    app = Flask(__name__)
    app.config.update(
        APP_ENV='production',
        SECRET_KEY=secret_key,
        JWT_SECRET_KEY=jwt_secret_key,
    )

    with pytest.raises(RuntimeError):
        _validate_production_secrets(app)


def test_non_production_keeps_legacy_secret_compatibility():
    app = Flask(__name__)
    app.config.update(
        APP_ENV='development',
        SECRET_KEY='your_default_secret_key',
        JWT_SECRET_KEY='your_jwt_secret_key',
    )

    _validate_production_secrets(app)


def test_proxy_fix_trusts_only_client_ip_and_scheme_headers():
    class ProxyConfig(RuntimeTestConfig):
        TRUSTED_PROXY_FOR_HOPS = 1
        TRUSTED_PROXY_PROTO_HOPS = 1

    app = create_app(ProxyConfig)

    @app.get('/proxy-inspection')
    def proxy_inspection():
        return {
            'remote_addr': request.remote_addr,
            'scheme': request.scheme,
            'host': request.host,
        }

    response = app.test_client().get(
        '/proxy-inspection',
        base_url='http://internal.example',
        headers={
            'X-Forwarded-For': '203.0.113.10',
            'X-Forwarded-Proto': 'https',
            'X-Forwarded-Host': 'attacker.example',
            'X-Forwarded-Port': '444',
            'X-Forwarded-Prefix': '/spoofed',
        },
    )

    assert response.get_json() == {
        'remote_addr': '203.0.113.10',
        'scheme': 'https',
        'host': 'internal.example',
    }


def test_proxy_fix_configures_forwarded_headers_independently():
    class ProxyConfig(RuntimeTestConfig):
        TRUSTED_PROXY_FOR_HOPS = 2
        TRUSTED_PROXY_PROTO_HOPS = 1

    app = create_app(ProxyConfig)

    @app.get('/proxy-hop-inspection')
    def proxy_hop_inspection():
        return {
            'remote_addr': request.remote_addr,
            'scheme': request.scheme,
        }

    response = app.test_client().get(
        '/proxy-hop-inspection',
        base_url='http://internal.example',
        headers={
            'X-Forwarded-For': '203.0.113.10, 10.0.0.20',
            'X-Forwarded-Proto': 'https',
        },
    )

    assert response.get_json() == {
        'remote_addr': '203.0.113.10',
        'scheme': 'https',
    }


def test_background_worker_requires_background_tasks(monkeypatch):
    from app import background_worker

    class DisabledApp:
        config = {'ENABLE_BACKGROUND_TASKS': False}

    monkeypatch.setattr(background_worker, 'create_app', lambda: DisabledApp())

    with pytest.raises(RuntimeError, match='ENABLE_BACKGROUND_TASKS=true'):
        background_worker.run_worker()


def test_background_worker_waits_for_signal_and_stops_scheduler(monkeypatch):
    from app import background_worker

    class EnabledApp:
        config = {'ENABLE_BACKGROUND_TASKS': True}

    class FakeScheduler:
        running = True
        shutdown_wait = None

        @staticmethod
        def get_jobs():
            return ['job']

        def shutdown(self, wait):
            self.shutdown_wait = wait
            self.running = False

    class ImmediatelyStoppedEvent:
        @staticmethod
        def wait(timeout):
            assert timeout == 30
            return True

        @staticmethod
        def set():
            pass

    registered_signals = []
    scheduler = FakeScheduler()
    monkeypatch.setattr(background_worker, 'create_app', lambda: EnabledApp())
    monkeypatch.setattr(background_worker, 'unified_scheduler', scheduler)
    monkeypatch.setattr(background_worker.threading, 'Event', ImmediatelyStoppedEvent)
    monkeypatch.setattr(
        background_worker.signal,
        'signal',
        lambda sig, handler: registered_signals.append((sig, handler)),
    )

    background_worker.run_worker()

    assert [item[0] for item in registered_signals] == [signal.SIGTERM, signal.SIGINT]
    assert scheduler.shutdown_wait is False
