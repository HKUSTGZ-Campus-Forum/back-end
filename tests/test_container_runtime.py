from pathlib import Path

from flask import Flask

import pytest

from app import _configure_proxy_fix, _validate_production_secrets, create_app


ROOT = Path(__file__).resolve().parents[1]


class RuntimeConfig:
    TESTING = True
    APP_ENV = 'development'
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


def test_healthz_is_dependency_independent():
    app = create_app(RuntimeConfig)
    response = app.test_client().get('/healthz')

    assert response.status_code == 200
    assert response.get_json() == {'status': 'ok'}
    assert response.headers['Cache-Control'] == 'no-store'


def test_readyz_does_not_expose_dependency_error(monkeypatch):
    app = create_app(RuntimeConfig)

    with app.app_context():
        monkeypatch.setattr(
            'app.routes.health._check_postgresql',
            lambda: (_ for _ in ()).throw(
                RuntimeError('credential-bearing database error')
            ),
        )
        monkeypatch.setattr('app.routes.health._check_redis', lambda: None)
        response = app.test_client().get('/readyz')

    assert response.status_code == 503
    assert 'credential-bearing' not in response.get_data(as_text=True)


def test_production_rejects_placeholder_secrets():
    app = Flask(__name__)
    app.config.update(
        APP_ENV='production',
        SECRET_KEY='your_default_secret_key',
        JWT_SECRET_KEY='x' * 64,
    )

    with pytest.raises(RuntimeError, match='SECRET_KEY'):
        _validate_production_secrets(app)


def test_proxy_fix_rejects_negative_hops():
    app = Flask(__name__)
    app.config['TRUSTED_PROXY_HOPS'] = -1

    with pytest.raises(ValueError, match='non-negative'):
        _configure_proxy_fix(app)


def test_container_default_base_image_is_digest_pinned():
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')

    assert (
        'ARG PYTHON_BASE_IMAGE=python:3.12-slim-bookworm@sha256:'
        in dockerfile
    )
    assert dockerfile.count('FROM ${PYTHON_BASE_IMAGE}') == 2
