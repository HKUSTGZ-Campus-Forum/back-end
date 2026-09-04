from types import SimpleNamespace

from flask_jwt_extended import create_access_token

from app import create_app
from app.config import Config
from app.extensions import cache, db
from app.models.user import User
from app.models.user_role import UserRole
from app.models.recruitment_attempt import RecruitmentAttempt
from app.routes import recruitment as recruitment_routes
from app.services.recruitment_agent_service import (
    PROMPT_LIMIT,
    RecruitmentStrategyPolicy,
    RecruitmentVirtualTarget,
    count_recruitment_prompt_characters,
    normalize_recruitment_prompt,
    recruitment_strategy_policy,
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
    RECRUITMENT_ADMIN_EMAILS = frozenset()


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


def test_prompt_normalization_matches_weighted_character_limit():
    assert normalize_recruitment_prompt('  Ａ\u200b计划  ') == 'A计划'
    # 'A' is non-Chinese (0.3); 计 and 划 are each Chinese (1.0) => 2.3.
    assert count_recruitment_prompt_characters('A计划') == 2.3
    # 100 Chinese characters weigh exactly the full 100-unit budget.
    assert count_recruitment_prompt_characters('汉' * PROMPT_LIMIT) == PROMPT_LIMIT
    # 100 ASCII (non-Chinese) characters weigh only 30 units.
    assert count_recruitment_prompt_characters('a' * PROMPT_LIMIT) == 30.0
    # Emoji and full-width marks are non-Chinese (0.3 each).
    assert count_recruitment_prompt_characters('🙂计划') == 2.3


def test_virtual_target_is_isolated_and_has_a_complete_scored_route():
    target = RecruitmentVirtualTarget(
        flag='NODE{test-flag}',
        policy=RecruitmentStrategyPolicy(True, True, True, True, True),
    )

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


def test_clipboard_shortcut_cannot_authorize_an_autonomous_solution():
    policy = recruitment_strategy_policy('Ctrl C+V')
    target = RecruitmentVirtualTarget(flag='NODE{test-flag}', policy=policy)

    result = target.execute('open_path', {'path': '/'})

    assert policy == RecruitmentStrategyPolicy()
    assert result['error'] == 'strategy_not_authorized'
    assert target.score == 0
    assert target.events[0]['code'] == 'strategy_blocked'

    app = _build_app()
    with app.app_context():
        attempt = run_recruitment_agent('Ctrl C+V')
    assert attempt['score'] == 0
    assert attempt['tool_calls'] == 0
    assert attempt['events'][0]['code'] == 'strategy_too_vague'


def test_plain_language_full_strategy_authorizes_each_scored_stage():
    policy = recruitment_strategy_policy(
        '先查看网页和加载的文件，寻找隐藏源码线索，按发现的身份读取数据，拿到通行证后提交'
    )

    assert policy == RecruitmentStrategyPolicy(True, True, True, True, True)


def test_public_config_does_not_require_authentication():
    client = _build_app().test_client()
    response = client.get('/recruitment/config')

    assert response.status_code == 200
    assert response.get_json()['data'] == {
        'enabled': True,
        'prompt_limit': 100,
        'cjk_unit_weight': 1.0,
        'non_cjk_unit_weight': 0.3,
        'attempt_limit': None,
        'repeatable': True,
        'max_tool_calls': 20,
        'max_rounds': 8,
    }
    assert response.headers['Cache-Control'] == 'no-store'


def test_status_and_run_require_authentication():
    client = _build_app().test_client()

    assert client.get('/recruitment/status').status_code == 401
    assert client.get('/recruitment/leaderboard').status_code == 401
    assert client.post('/recruitment/run', json={'prompt': 'test'}).status_code == 401


def test_run_validates_prompt_before_consuming_attempt():
    app = _build_app()
    client = app.test_client()
    headers = _auth_headers(app)

    assert client.post('/recruitment/run', headers=headers, json={'prompt': '  '}).status_code == 400
    # 101 Chinese characters exceed the 100-unit weighted budget.
    assert client.post(
        '/recruitment/run',
        headers=headers,
        json={'prompt': '汉' * 101},
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
        result = run_recruitment_agent(
            'Open the page, inspect its loaded file and hidden source, read the record, then submit the flag.'
        )

    assert captured['api_key'] == 'test-key'
    assert captured['base_url'] == 'https://agent.example.test/v1'
    assert captured['request']['model'] == 'test-model'
    assert result['model'] == 'test-model'


def test_every_active_account_can_repeat_challenge(monkeypatch):
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
    assert second.status_code == 200
    assert status.get_json()['data']['attempted'] is True
    assert status.get_json()['data']['unlimited_attempts'] is True
    assert calls == ['先检查源码', '再来一次']
    with app.app_context():
        records = RecruitmentAttempt.query.order_by(RecruitmentAttempt.id).all()
        assert len(records) == 2
        assert records[0].prompt == '先检查源码'
        assert records[0].score == 100
        assert records[0].feedback[0]['code'] == 'flag_accepted'
        assert records[1].prompt == '再来一次'


def test_only_allowlisted_verified_account_can_view_recruitment_admin(monkeypatch):
    app = _build_app()
    app.config['RECRUITMENT_ADMIN_EMAILS'] = {
        'YXIA873@CONNECT.HKUST-GZ.EDU.CN',
    }
    client = app.test_client()
    owner_headers = _auth_headers(
        app,
        identity='50',
        email='yxia873@connect.hkust-gz.edu.cn',
    )
    other_headers = _auth_headers(
        app,
        identity='51',
        email='other@connect.hkust-gz.edu.cn',
    )
    fake_results = iter([
        {
            'state': 'complete',
            'success': True,
            'score': 90,
            'tool_calls': 6,
            'events': [{'code': 'record_exposed', 'detail': 'found', 'points': 20, 'score': 55}],
            'agent_message': 'owner feedback',
            'duration_ms': 1200,
            'model': 'test-model',
        },
        {
            'state': 'complete',
            'success': False,
            'score': 25,
            'tool_calls': 2,
            'events': [{'code': 'bundle_found', 'detail': 'bundle', 'points': 15, 'score': 25}],
            'agent_message': 'other feedback',
            'duration_ms': 800,
            'model': 'test-model',
        },
    ])
    monkeypatch.setattr(
        recruitment_routes,
        'run_recruitment_agent',
        lambda _prompt: next(fake_results),
    )

    assert client.post(
        '/recruitment/run',
        headers=owner_headers,
        json={'prompt': '主人提示'},
    ).status_code == 200
    assert client.post(
        '/recruitment/run',
        headers=other_headers,
        json={'prompt': '同学提示'},
    ).status_code == 200

    owner_status = client.get(
        '/recruitment/status', headers=owner_headers
    ).get_json()['data']
    other_status = client.get(
        '/recruitment/status', headers=other_headers
    ).get_json()['data']
    denied = client.get(
        '/recruitment/admin/overview', headers=other_headers
    )
    overview = client.get(
        '/recruitment/admin/overview', headers=owner_headers
    )

    assert owner_status['can_view_admin'] is True
    assert other_status['can_view_admin'] is False
    assert denied.status_code == 403
    assert denied.get_json()['error'] == 'admin_required'
    assert overview.status_code == 200
    data = overview.get_json()['data']
    assert data['summary'] == {
        'attempts': 2,
        'participants': 2,
        'completed': 2,
        'perfect_scores': 0,
        'average_score': 57.5,
    }
    assert [entry['email'] for entry in data['leaderboard']] == [
        'yxia873@connect.hkust-gz.edu.cn',
        'other@connect.hkust-gz.edu.cn',
    ]
    assert data['attempts'][0]['prompt'] == '同学提示'
    assert data['attempts'][0]['feedback'][0]['code'] == 'bundle_found'
    assert data['attempts'][1]['agent_message'] == 'owner feedback'


def test_public_leaderboard_uses_best_score_then_first_achievement(monkeypatch):
    app = _build_app()
    app.config['RECRUITMENT_ADMIN_EMAILS'] = {
        'yxia873@connect.hkust-gz.edu.cn',
    }
    client = app.test_client()
    first_headers = _auth_headers(
        app,
        identity='60',
        email='yxia873@connect.hkust-gz.edu.cn',
    )
    second_headers = _auth_headers(
        app,
        identity='61',
        email='second@connect.hkust-gz.edu.cn',
    )
    scores = iter([90, 50, 90, 80])

    def fake_run(_prompt):
        score = next(scores)
        return {
            'state': 'complete',
            'success': score == 100,
            'score': score,
            'tool_calls': 5,
            'events': [],
            'agent_message': '',
            'duration_ms': 10,
            'model': 'test-model',
        }

    monkeypatch.setattr(recruitment_routes, 'run_recruitment_agent', fake_run)
    for headers, prompt in (
        (first_headers, '先达到九十分'),
        (second_headers, '先试一次'),
        (second_headers, '后来达到九十分'),
        (first_headers, '较低分不覆盖'),
    ):
        assert client.post(
            '/recruitment/run', headers=headers, json={'prompt': prompt}
        ).status_code == 200

    response = client.get(
        '/recruitment/leaderboard', headers=second_headers
    ).get_json()['data']
    assert response['participants'] == 2
    assert [entry['username'] for entry in response['entries']] == [
        'recruitment-user-60',
        'recruitment-user-61',
    ]
    assert [entry['score'] for entry in response['entries']] == [90, 90]
    assert [entry['rank'] for entry in response['entries']] == [1, 2]
    assert response['entries'][0]['is_current_user'] is False
    assert response['entries'][1]['is_current_user'] is True
    assert set(response['entries'][0]) == {
        'rank', 'username', 'score', 'achieved_at', 'is_current_user',
    }

    admin = client.get(
        '/recruitment/admin/overview', headers=first_headers
    ).get_json()['data']
    assert admin['summary']['attempts'] == 4
    assert [entry['score'] for entry in admin['leaderboard']] == [90, 90]


def test_unverified_active_account_can_still_repeat_challenge(monkeypatch):
    app = _build_app()
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
    assert second.status_code == 200
    assert status['unlimited_attempts'] is True
