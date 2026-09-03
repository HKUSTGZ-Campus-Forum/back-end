from types import SimpleNamespace

from flask_jwt_extended import create_access_token

from app import create_app
from app.config import Config
from app.extensions import cache, db
from app.models.user import User
from app.models.user_role import UserRole
from app.routes import recruitment as recruitment_routes
from app.services.recruitment_agent_service import (
    PROMPT_LIMIT,
    RecruitmentVirtualTarget,
    count_recruitment_prompt_characters,
    normalize_recruitment_prompt,
    run_recruitment_agent,
)
from app.services import recruitment_agent_service


class RecruitmentTestConfig(Config):
    TESTING = True
    SECRET_KEY = 'recruitment-test-secret'
    JWT_SECRET_KEY = 'recruitment-test-jwt-secret-0123456789'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    CACHE_TYPE = 'SimpleCache'
    AUTO_INIT_ON_STARTUP = False
    ENABLE_BACKGROUND_TASKS = False
    RECRUITMENT_CHALLENGE_ENABLED = True
    RECRUITMENT_AGENT_API_KEY = 'test-key'
    RECRUITMENT_AGENT_BASE_URL = 'https://agent.example.test/v1'
    RECRUITMENT_AGENT_MODEL = 'test-model'
    RECRUITMENT_AGENT_MAX_ROUNDS = 8
    RECRUITMENT_AGENT_MAX_TOOL_CALLS = 20
    RECRUITMENT_AGENT_TIMEOUT_SECONDS = 12
    RECRUITMENT_ATTEMPT_TTL_SECONDS = 3600
    RECRUITMENT_UNLIMITED_EMAILS = frozenset()


def _build_app():
    app = create_app(RecruitmentTestConfig)
    with app.app_context():
        db.create_all()
        cache.clear()
    return app


def _auth_headers(app, identity='42', email=None, email_verified=True):
    with app.app_context():
        role = UserRole(name=f'user-{identity}', description='Test role')
        user = User(
            id=int(identity),
            username=f'recruitment-user-{identity}',
            password_hash='not-used',
            email=email or f'recruitment-{identity}@connect.hkust-gz.edu.cn',
            email_verified=email_verified,
            role=role,
        )
        db.session.add_all([role, user])
        db.session.commit()
        token = create_access_token(identity=str(user.id))
    return {'Authorization': f'Bearer {token}'}


def test_prompt_normalization_matches_visible_character_limit():
    assert normalize_recruitment_prompt('  Ａ\u200b计划  ') == 'A计划'
    assert count_recruitment_prompt_characters('🙂计划') == 3
    assert count_recruitment_prompt_characters('a' * PROMPT_LIMIT) == PROMPT_LIMIT


def test_virtual_target_is_isolated_and_has_a_complete_scored_route():
    target = RecruitmentVirtualTarget(flag='NODE{test-flag}')

    rejected = target.open_path('https://unikorn.hkust-gz.edu.cn/api/users')
    assert rejected['status'] == 404

    target.open_path('/')
    target.open_path('/assets/app.js')
    target.open_path('/assets/app.js.map')
    exposed = target.call_api('/api/candidates/node-founder')
    submitted = target.submit_flag(exposed['data']['flag'])

    assert submitted['accepted'] is True
    assert target.success is True
    assert target.score == 100
    assert {event['code'] for event in target.events} >= {
        'surface_mapped',
        'bundle_found',
        'source_map_found',
        'record_exposed',
        'flag_accepted',
        'efficiency_bonus',
    }


def test_public_config_does_not_require_authentication():
    client = _build_app().test_client()
    response = client.get('/recruitment/config')

    assert response.status_code == 200
    assert response.get_json()['data'] == {
        'enabled': True,
        'prompt_limit': 100,
        'attempt_limit': 1,
        'max_tool_calls': 20,
        'max_rounds': 8,
    }
    assert response.headers['Cache-Control'] == 'no-store'


def test_status_and_run_require_authentication():
    client = _build_app().test_client()

    assert client.get('/recruitment/status').status_code == 401
    assert client.post('/recruitment/run', json={'prompt': 'test'}).status_code == 401


def test_run_validates_prompt_before_consuming_attempt():
    app = _build_app()
    client = app.test_client()
    headers = _auth_headers(app)

    assert client.post('/recruitment/run', headers=headers, json={'prompt': '  '}).status_code == 400
    assert client.post(
        '/recruitment/run',
        headers=headers,
        json={'prompt': 'a' * 101},
    ).status_code == 400
    status = client.get('/recruitment/status', headers=headers).get_json()
    assert status['data']['attempted'] is False


def test_unavailable_challenge_does_not_consume_attempt():
    app = _build_app()
    app.config['RECRUITMENT_AGENT_API_KEY'] = ''
    client = app.test_client()
    headers = _auth_headers(app)

    config = client.get('/recruitment/config').get_json()
    run = client.post('/recruitment/run', headers=headers, json={'prompt': '检查源码'})
    status = client.get('/recruitment/status', headers=headers).get_json()

    assert config['data']['enabled'] is False
    assert run.status_code == 503
    assert run.get_json()['error'] == 'challenge_unavailable'
    assert status['data']['attempted'] is False


def test_agent_uses_dedicated_provider_configuration(monkeypatch):
    app = _build_app()
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create),
            )

        @staticmethod
        def create(**kwargs):
            captured['request'] = kwargs
            message = SimpleNamespace(content='done', tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(recruitment_agent_service, 'OpenAI', FakeClient)

    with app.app_context():
        result = run_recruitment_agent('test strategy')

    assert captured['api_key'] == 'test-key'
    assert captured['base_url'] == 'https://agent.example.test/v1'
    assert captured['request']['model'] == 'test-model'
    assert result['model'] == 'test-model'


def test_only_one_successful_run_is_accepted_per_user(monkeypatch):
    app = _build_app()
    client = app.test_client()
    headers = _auth_headers(app)
    fake_result = {
        'state': 'complete',
        'success': True,
        'score': 100,
        'tool_calls': 5,
        'events': [{'code': 'flag_accepted', 'detail': 'ok', 'points': 35, 'score': 95}],
        'agent_message': 'done',
        'duration_ms': 10,
        'model': 'test-model',
    }
    calls = []

    def fake_run(prompt):
        calls.append(prompt)
        return fake_result

    monkeypatch.setattr(recruitment_routes, 'run_recruitment_agent', fake_run)

    first = client.post('/recruitment/run', headers=headers, json={'prompt': '  先检查源码  '})
    second = client.post('/recruitment/run', headers=headers, json={'prompt': '再来一次'})
    status = client.get('/recruitment/status', headers=headers)

    assert first.status_code == 200
    assert first.get_json()['data']['attempt']['score'] == 100
    assert second.status_code == 409
    assert second.get_json()['error'] == 'attempt_already_used'
    assert status.get_json()['data']['attempted'] is True
    assert status.get_json()['data']['unlimited_attempts'] is False
    assert calls == ['先检查源码']


def test_allowlisted_verified_email_can_repeat_challenge(monkeypatch):
    app = _build_app()
    app.config['RECRUITMENT_UNLIMITED_EMAILS'] = {
        'YXIA873@CONNECT.HKUST-GZ.EDU.CN',
    }
    client = app.test_client()
    headers = _auth_headers(
        app,
        email='yxia873@connect.hkust-gz.edu.cn',
    )
    calls = []

    def fake_run(prompt):
        calls.append(prompt)
        return {
            'state': 'complete',
            'success': True,
            'score': 100,
            'tool_calls': 5,
            'events': [],
            'agent_message': 'done',
            'duration_ms': 10,
            'model': 'test-model',
        }

    monkeypatch.setattr(recruitment_routes, 'run_recruitment_agent', fake_run)

    first = client.post('/recruitment/run', headers=headers, json={'prompt': '第一次'})
    second = client.post('/recruitment/run', headers=headers, json={'prompt': '第二次'})
    status = client.get('/recruitment/status', headers=headers).get_json()['data']

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()['data']['unlimited_attempts'] is True
    assert status['attempted'] is True
    assert status['unlimited_attempts'] is True
    assert status['attempt']['score'] == 100
    assert calls == ['第一次', '第二次']


def test_allowlisted_unverified_email_keeps_one_attempt_limit(monkeypatch):
    app = _build_app()
    app.config['RECRUITMENT_UNLIMITED_EMAILS'] = {
        'yxia873@connect.hkust-gz.edu.cn',
    }
    client = app.test_client()
    headers = _auth_headers(
        app,
        identity='43',
        email='yxia873@connect.hkust-gz.edu.cn',
        email_verified=False,
    )
    monkeypatch.setattr(
        recruitment_routes,
        'run_recruitment_agent',
        lambda _prompt: {
            'state': 'complete',
            'success': False,
            'score': 10,
            'tool_calls': 1,
            'events': [],
            'agent_message': 'done',
            'duration_ms': 10,
            'model': 'test-model',
        },
    )

    first = client.post('/recruitment/run', headers=headers, json={'prompt': '第一次'})
    second = client.post('/recruitment/run', headers=headers, json={'prompt': '第二次'})
    status = client.get('/recruitment/status', headers=headers).get_json()['data']

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.get_json()['error'] == 'attempt_already_used'
    assert status['unlimited_attempts'] is False
