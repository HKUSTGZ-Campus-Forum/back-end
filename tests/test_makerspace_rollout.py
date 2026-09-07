"""Production-shape rehearsal on an explicitly disposable PostgreSQL database."""
import os
import pytest
from sqlalchemy import text, inspect


def test_school_revision_to_makerspace_preserves_existing_data(monkeypatch):
    url = os.environ.get('MAKERSPACE_ROLLOUT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('MAKERSPACE_ROLLOUT_TEST_DATABASE_URL is required')
    for key in ('ALL_PROXY', 'all_proxy', 'HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-key')
    from app import create_app
    from app.config import Config
    from app.extensions import db
    from flask_migrate import Migrate
    from alembic import command
    from tools.makerspace_preflight import inspect_target

    class Settings(Config):
        SQLALCHEMY_DATABASE_URI = url
        SQLALCHEMY_ENGINE_OPTIONS = {}
        AUTO_INIT_ON_STARTUP = False
        ENABLE_BACKGROUND_TASKS = False
        CACHE_TYPE = 'SimpleCache'

    app = create_app(Settings)
    with app.app_context():
        assert not inspect(db.engine).get_table_names(), 'Use an empty disposable database'
        migrate = Migrate(app, db)
        command.upgrade(migrate.get_config(), '20260903_recruitment_admin')
        with db.engine.begin() as connection:
            role = connection.execute(text("INSERT INTO user_roles (name, description) VALUES ('rollout-test', 'test') RETURNING id")).scalar_one()
            connection.execute(text("INSERT INTO users (id,username,password_hash,email,email_verified,phone_verified,role_id,is_deleted,created_at,updated_at) VALUES (1256,'MakerSpace test creator','disabled','fning477@connect.hkust-gz.edu.cn',true,false,:role,false,now(),now())"), {'role': role})
            before = {name: connection.execute(text(f'SELECT count(*) FROM {name}')).scalar_one() for name in ('users', 'posts', 'courses')}
            assert inspect_target(connection)['owner_matches'] == 1
        command.upgrade(migrate.get_config(), '20260907_teamup_makerspace')
        command.upgrade(migrate.get_config(), '20260907_teamup_makerspace')
        with db.engine.connect() as connection:
            assert before == {name: connection.execute(text(f'SELECT count(*) FROM {name}')).scalar_one() for name in before}
            assert connection.execute(text("SELECT owner_id, status, kind, external_path FROM maker_spaces WHERE slug='teamup'")).one() == (1256, 'published', 'external', '/teamup/')
            assert connection.execute(text('SELECT count(*) FROM maker_spaces')).scalar_one() == 1
            assert connection.execute(text('SELECT count(*) FROM maker_audit_events')).scalar_one() == 1
            assert connection.execute(text('SELECT count(*) FROM maker_deployments')).scalar_one() == 0
            assert connection.execute(text("SELECT to_regclass('public.agent_conversations')")).scalar_one() is None
