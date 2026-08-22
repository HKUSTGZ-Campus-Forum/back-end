import os

import pytest
from sqlalchemy import create_engine, inspect, text


DATABASE_URL_ENV = "PRISTINE_POSTGRES_DATABASE_URL"
EXPECTED_HEADS = {"20260822_scheduler_plans"}


@pytest.fixture(scope="module")
def pristine_database_url():
    database_url = os.getenv(DATABASE_URL_ENV)
    if not database_url:
        pytest.skip(f"{DATABASE_URL_ENV} is required for PostgreSQL migration tests")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        public_relations = connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = 'public'
                  AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
                """
            )
        ).scalar_one()
    engine.dispose()

    assert public_relations == 0, "migration integration test requires a pristine database"
    return database_url


def test_pristine_postgres_upgrade_reaches_existing_heads(
    pristine_database_url,
    monkeypatch,
):
    from alembic import command
    from flask_migrate import Migrate

    from app import create_app
    from app.config import Config
    from app.extensions import db

    class MigrationTestConfig(Config):
        SQLALCHEMY_DATABASE_URI = pristine_database_url
        SQLALCHEMY_ENGINE_OPTIONS = {}
        AUTO_INIT_ON_STARTUP = False
        ENABLE_BACKGROUND_TASKS = False
        CACHE_TYPE = "SimpleCache"

    monkeypatch.setenv("AUTO_INIT_ON_STARTUP", "false")
    monkeypatch.setenv("ENABLE_BACKGROUND_TASKS", "false")

    app = create_app(MigrationTestConfig)
    with app.app_context():
        migrate_extension = Migrate(app, db)
        command.upgrade(
            migrate_extension.get_config(),
            "20260820_title_abbr_255",
        )
        with db.engine.begin() as connection:
            rollout_floor = connection.execute(
                text("SELECT CURRENT_TIMESTAMP")
            ).scalar_one()
            role_id = connection.execute(
                text(
                    "INSERT INTO user_roles (name, description) "
                    "VALUES ('migration_test_user', 'migration test') "
                    "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
                    "RETURNING id"
                )
            ).scalar_one()
            grandfathered_user_id = connection.execute(
                text(
                    "INSERT INTO users ("
                    "username, password_hash, email, email_verified, "
                    "phone_verified, role_id, is_deleted, created_at, updated_at"
                    ") VALUES ("
                    "'pre_onboarding_user', 'disabled', "
                    "'pre-onboarding@connect.hkust-gz.edu.cn', TRUE, FALSE, "
                    ":role_id, FALSE, "
                    "TIMESTAMPTZ '2020-01-01 00:00:00+00', "
                    "TIMESTAMPTZ '2020-02-01 00:00:00+00'"
                    ") RETURNING id"
                ),
                {"role_id": role_id},
            ).scalar_one()

        command.upgrade(migrate_extension.get_config(), "heads")

        with db.engine.connect() as connection:
            actual_heads = {
                row[0]
                for row in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            }
        assert actual_heads == EXPECTED_HEADS

        with db.engine.connect() as connection:
            completed_at = connection.execute(
                text(
                    "SELECT onboarding_completed_at FROM users WHERE id = :user_id"
                ),
                {"user_id": grandfathered_user_id},
            ).scalar_one()
        assert completed_at >= rollout_floor

        inspector = inspect(db.engine)
        contest_columns = {
            column["name"]
            for column in inspector.get_columns("contest_submissions")
        }
        assert "track" in contest_columns
        assert any(
            constraint.get("column_names") == ["user_id", "track"]
            for constraint in inspector.get_unique_constraints(
                "contest_submissions"
            )
        )

        gugu_columns = {
            column["name"]
            for column in inspector.get_columns("gugu_messages")
        }
        assert "reply_to_message_id" in gugu_columns
        assert any(
            foreign_key.get("constrained_columns") == ["reply_to_message_id"]
            and foreign_key.get("referred_table") == "gugu_messages"
            and foreign_key.get("referred_columns") == ["id"]
            for foreign_key in inspector.get_foreign_keys("gugu_messages")
        )

        assert inspector.has_table("curriculum_programs")
        assert inspector.has_table("scheduler_popularity_events")
        assert set(db.metadata.tables).issubset(set(inspector.get_table_names()))
        assert {
            column["name"]
            for column in inspector.get_columns("notifications")
        } >= {"link_url"}
        assert "onboarding_completed_at" in {
            column["name"]
            for column in inspector.get_columns("users")
        }

        # The second pass proves the normal deploy command is safe to rerun.
        command.upgrade(migrate_extension.get_config(), "heads")
        with db.engine.connect() as connection:
            rerun_heads = {
                row[0]
                for row in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            }
        assert rerun_heads == EXPECTED_HEADS
