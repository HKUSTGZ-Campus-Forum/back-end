"""Rehearse the scoped school revision, without unrelated development heads."""
from sqlalchemy import inspect, text
from tests.test_pristine_postgres_migrations import pristine_database_url


def test_school_social_to_sync_preserves_existing_tables(pristine_database_url, monkeypatch):
    from alembic import command
    from flask_migrate import Migrate
    from app import create_app
    from app.config import Config
    from app.extensions import db
    class MigrationConfig(Config):
        SQLALCHEMY_DATABASE_URI = pristine_database_url
        SQLALCHEMY_ENGINE_OPTIONS = {}
        AUTO_INIT_ON_STARTUP = False
        ENABLE_BACKGROUND_TASKS = False
        CACHE_TYPE = 'SimpleCache'
    monkeypatch.setenv('AUTO_INIT_ON_STARTUP', 'false')
    monkeypatch.setenv('ENABLE_BACKGROUND_TASKS', 'false')
    app = create_app(MigrationConfig)
    with app.app_context():
        migration = Migrate(app, db).get_config()
        command.upgrade(migration, '20260907_maker_social')
        original = set(inspect(db.engine).get_table_names())
        def counts():
            with db.engine.connect() as conn:
                return {name: conn.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one()
                        for name in original if name != 'alembic_version'}
        before = counts()
        added = {'maker_identities', 'maker_sync_grants', 'maker_sync_receipts', 'maker_sync_audits'}
        for repeat in range(2):
            command.upgrade(migration, '20260907_maker_sync')
            assert set(inspect(db.engine).get_table_names()) - original == added
            assert counts() == before
            with db.engine.connect() as conn:
                assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20260907_maker_sync'
                assert all(conn.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one() == 0 for name in added)
            if not repeat:
                # Safe only because all four new tables are still empty.
                command.downgrade(migration, '20260907_maker_social')
                assert set(inspect(db.engine).get_table_names()) == original
