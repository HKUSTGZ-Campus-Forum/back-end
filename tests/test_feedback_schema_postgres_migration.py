import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.schema import CreateSchema, DropSchema


DATABASE_URL_ENV = "PRISTINE_POSTGRES_DATABASE_URL"
ROOT = Path(__file__).resolve().parents[1]
OLD_CORE_HEAD = "5202003d1ec0"
SCHEDULER_HEAD = "20260812_pop_history"
NEW_CORE_HEAD = "20260813_feedback_schema"
FEEDBACK_TABLES = {
    "feedbacks",
    "feedback_versions",
    "feedback_merge_requests",
    "feedback_comments",
    "feedback_merge_comments",
    "feedback_audit_events",
}


def _without_schema_query(database_url):
    url = sa.engine.make_url(database_url)
    query = dict(url.query)
    query.pop("schema", None)
    return url.set(query=query)


@pytest.fixture()
def postgres_schema():
    database_url = os.getenv(DATABASE_URL_ENV)
    if not database_url:
        pytest.skip(f"{DATABASE_URL_ENV} is required for PostgreSQL migration tests")

    base_url = _without_schema_query(database_url)
    if base_url.get_backend_name() != "postgresql":
        pytest.fail(f"{DATABASE_URL_ENV} must point to PostgreSQL")

    schema_name = f"feedback_migration_{uuid4().hex}"
    admin_engine = create_engine(base_url)
    with admin_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))

    try:
        yield base_url.render_as_string(hide_password=False), schema_name
    finally:
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema_name, cascade=True))
        admin_engine.dispose()


def _create_app(database_url, schema_name, monkeypatch):
    from app import create_app
    from app.config import Config

    class MigrationTestConfig(Config):
        SQLALCHEMY_DATABASE_URI = database_url
        SQLALCHEMY_ENGINE_OPTIONS = {
            "connect_args": {"options": f"-csearch_path={schema_name}"},
        }
        AUTO_INIT_ON_STARTUP = False
        ENABLE_BACKGROUND_TASKS = False
        CACHE_TYPE = "SimpleCache"

    monkeypatch.setenv("AUTO_INIT_ON_STARTUP", "false")
    monkeypatch.setenv("ENABLE_BACKGROUND_TASKS", "false")
    return create_app(MigrationTestConfig)


def _migration_config(app):
    from flask_migrate import Migrate

    from app.extensions import db

    return Migrate(app, db).get_config(directory=str(ROOT / "migrations"))


def _stamp_pre_migration_heads(app):
    from alembic import command

    command.stamp(
        _migration_config(app),
        [OLD_CORE_HEAD, SCHEDULER_HEAD],
        purge=True,
    )


def _upgrade_heads(app):
    from alembic import command

    command.upgrade(_migration_config(app), "heads")


def _heads(connection):
    return {
        row[0]
        for row in connection.execute(text("SELECT version_num FROM alembic_version"))
    }


def _create_prerequisite_tables(connection, include_link_url=False):
    connection.execute(text("CREATE TABLE users (id SERIAL PRIMARY KEY)"))
    connection.execute(text("CREATE TABLE posts (id SERIAL PRIMARY KEY)"))
    connection.execute(text("CREATE TABLE comments (id SERIAL PRIMARY KEY)"))
    connection.execute(text("CREATE TABLE reactions (id SERIAL PRIMARY KEY)"))
    link_column = "link_url VARCHAR(255)," if include_link_url else ""
    connection.execute(
        text(
            f"""
            CREATE TABLE notifications (
                id SERIAL PRIMARY KEY,
                recipient_id INTEGER NOT NULL REFERENCES users(id),
                sender_id INTEGER REFERENCES users(id),
                type VARCHAR(50) NOT NULL,
                title VARCHAR(255) NOT NULL,
                message TEXT NOT NULL,
                read BOOLEAN NOT NULL,
                post_id INTEGER REFERENCES posts(id),
                comment_id INTEGER REFERENCES comments(id),
                reaction_id INTEGER REFERENCES reactions(id),
                {link_column}
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_notifications_recipient_created "
            "ON notifications (recipient_id, created_at)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX idx_notifications_recipient_read "
            "ON notifications (recipient_id, read)"
        )
    )
    connection.execute(
        text("CREATE INDEX idx_notifications_type ON notifications (type)")
    )


def _assert_feedback_schema(engine):
    inspector = inspect(engine)
    assert FEEDBACK_TABLES.issubset(set(inspector.get_table_names()))
    assert {
        column["name"]
        for column in inspector.get_columns("notifications")
    } >= {"link_url"}
    link_url = next(
        column
        for column in inspector.get_columns("notifications")
        if column["name"] == "link_url"
    )
    assert isinstance(link_url["type"], sa.String)
    assert link_url["type"].length == 255
    assert link_url["nullable"] is True
    assert link_url["default"] is None

    assert {
        index["name"]
        for index in inspector.get_indexes("feedbacks")
    } == {
        "idx_feedbacks_status_updated",
        "ix_feedbacks_author_id",
        "ix_feedbacks_status",
    }
    assert {
        (constraint["name"], tuple(constraint["column_names"]))
        for constraint in inspector.get_unique_constraints("feedback_versions")
    } == {
        (
            "uq_feedback_versions_feedback_version_number",
            ("feedback_id", "version_number"),
        ),
    }


def test_pristine_postgres_graph_installs_feedback_schema(
    postgres_schema,
    monkeypatch,
):
    from app.extensions import db

    database_url, schema_name = postgres_schema
    app = _create_app(database_url, schema_name, monkeypatch)

    with app.app_context():
        _upgrade_heads(app)

        with db.engine.connect() as connection:
            assert _heads(connection) == {NEW_CORE_HEAD, SCHEDULER_HEAD}
        _assert_feedback_schema(db.engine)


def test_upgrade_creates_feedback_schema_from_current_pre_migration_heads(
    postgres_schema,
    monkeypatch,
):
    from app.extensions import db

    database_url, schema_name = postgres_schema
    app = _create_app(database_url, schema_name, monkeypatch)

    with app.app_context():
        with db.engine.begin() as connection:
            _create_prerequisite_tables(connection)
            connection.execute(text("INSERT INTO users (id) VALUES (1)"))
            connection.execute(
                text(
                    """
                    INSERT INTO notifications (
                        id, recipient_id, type, title, message, read,
                        created_at, updated_at
                    ) VALUES (
                        101, 1, 'legacy', 'retained', 'keep me', false,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )

        _stamp_pre_migration_heads(app)
        _upgrade_heads(app)

        with db.engine.connect() as connection:
            assert _heads(connection) == {NEW_CORE_HEAD, SCHEDULER_HEAD}
            retained = connection.execute(
                text("SELECT title, message, link_url FROM notifications WHERE id = 101")
            ).one()
            assert retained == ("retained", "keep me", None)
        _assert_feedback_schema(db.engine)

        # Normal deploy reruns are no-ops once the revision is stamped.
        _upgrade_heads(app)
        with db.engine.connect() as connection:
            assert _heads(connection) == {NEW_CORE_HEAD, SCHEDULER_HEAD}


def test_upgrade_adopts_create_all_equivalent_objects_without_data_loss(
    postgres_schema,
    monkeypatch,
):
    from app.extensions import db
    from app.models.feedback import Feedback
    from app.models.feedback_audit_event import FeedbackAuditEvent
    from app.models.feedback_comment import FeedbackComment
    from app.models.feedback_merge_comment import FeedbackMergeComment
    from app.models.feedback_merge_request import FeedbackMergeRequest
    from app.models.feedback_version import FeedbackVersion

    database_url, schema_name = postgres_schema
    app = _create_app(database_url, schema_name, monkeypatch)

    with app.app_context():
        with db.engine.begin() as connection:
            _create_prerequisite_tables(connection, include_link_url=True)

        # This is the historical startup path being adopted by the migration.
        db.metadata.create_all(
            bind=db.engine,
            tables=[
                Feedback.__table__,
                FeedbackVersion.__table__,
                FeedbackMergeRequest.__table__,
                FeedbackComment.__table__,
                FeedbackMergeComment.__table__,
                FeedbackAuditEvent.__table__,
            ],
            checkfirst=True,
        )
        with db.engine.begin() as connection:
            connection.execute(text("INSERT INTO users (id) VALUES (1), (2)"))
            connection.execute(
                text(
                    """
                    INSERT INTO feedbacks (
                        id, author_id, title, status, comments_ended,
                        created_at, updated_at
                    ) VALUES (
                        10, 1, 'existing feedback', 'published', false,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO feedback_versions (
                        id, feedback_id, version_number, markdown_content,
                        created_by_user_id, created_at
                    ) VALUES (20, 10, 1, 'existing body', 1, CURRENT_TIMESTAMP)
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO feedback_merge_requests (
                        id, feedback_id, author_id, base_version_id, title,
                        proposed_markdown_content, status, created_at, updated_at
                    ) VALUES (
                        30, 10, 2, 20, 'existing merge', 'proposed body',
                        'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            connection.execute(
                text("UPDATE feedbacks SET current_version_id = 20 WHERE id = 10")
            )
            connection.execute(
                text(
                    "UPDATE feedback_versions SET source_merge_request_id = 30 "
                    "WHERE id = 20"
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO feedback_comments (
                        id, feedback_id, user_id, content, visibility,
                        created_at, updated_at
                    ) VALUES (
                        40, 10, 2, 'existing comment', 'visible',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO feedback_merge_comments (
                        id, merge_request_id, user_id, content, visibility,
                        created_at, updated_at
                    ) VALUES (
                        50, 30, 1, 'existing review', 'visible',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO feedback_audit_events (
                        id, feedback_id, merge_request_id, actor_user_id,
                        event_type, event_payload, created_at
                    ) VALUES (
                        60, 10, 30, 1, 'existing', '{"kept": true}',
                        CURRENT_TIMESTAMP
                    )
                    """
                )
            )

        _stamp_pre_migration_heads(app)
        _upgrade_heads(app)

        with db.engine.connect() as connection:
            assert _heads(connection) == {NEW_CORE_HEAD, SCHEDULER_HEAD}
            assert connection.execute(
                text("SELECT title, current_version_id FROM feedbacks WHERE id = 10")
            ).one() == ("existing feedback", 20)
            assert connection.execute(
                text("SELECT markdown_content FROM feedback_versions WHERE id = 20")
            ).scalar_one() == "existing body"
            assert connection.execute(
                text("SELECT event_payload FROM feedback_audit_events WHERE id = 60")
            ).scalar_one() == {"kept": True}
        _assert_feedback_schema(db.engine)


@pytest.mark.parametrize("incompatible_state", ["partial", "wrong_link_type"])
def test_upgrade_fails_closed_before_mutating_incompatible_objects(
    postgres_schema,
    monkeypatch,
    incompatible_state,
):
    from app.extensions import db

    database_url, schema_name = postgres_schema
    app = _create_app(database_url, schema_name, monkeypatch)

    with app.app_context():
        with db.engine.begin() as connection:
            _create_prerequisite_tables(connection)
            if incompatible_state == "partial":
                connection.execute(
                    text("CREATE TABLE feedbacks (id SERIAL PRIMARY KEY)")
                )
            else:
                connection.execute(
                    text("ALTER TABLE notifications ADD COLUMN link_url TEXT")
                )
        _stamp_pre_migration_heads(app)

        with pytest.raises(RuntimeError, match="migration refused"):
            _upgrade_heads(app)

        inspector = inspect(db.engine)
        if incompatible_state == "partial":
            assert set(inspector.get_table_names()) & FEEDBACK_TABLES == {"feedbacks"}
            assert "link_url" not in {
                column["name"]
                for column in inspector.get_columns("notifications")
            }
        else:
            assert not (set(inspector.get_table_names()) & FEEDBACK_TABLES)
            link_column = next(
                column
                for column in inspector.get_columns("notifications")
                if column["name"] == "link_url"
            )
            assert isinstance(link_column["type"], sa.Text)
        with db.engine.connect() as connection:
            assert _heads(connection) == {OLD_CORE_HEAD, SCHEDULER_HEAD}
