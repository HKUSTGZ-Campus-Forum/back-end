from flask import Flask
from sqlalchemy import inspect, text

import app as app_package
from app.extensions import db


SCHEDULER_COURSE_COLUMNS = {
    "subject",
    "catalog_number",
    "course_title_abbr",
    "pre_requirement",
    "co_requirement",
    "exclusion",
    "pg_course",
    "klms_course",
    "vector",
}

SCHEDULER_TABLES = {
    "scheduler_sections",
    "scheduler_lectures",
    "scheduler_map_components",
    "scheduler_map_lines",
    "scheduler_user_course_carts",
    "scheduler_user_bundle_carts",
}


def test_auto_init_scheduler_support_upgrades_legacy_schema_idempotently():
    flask_app = Flask(__name__)
    flask_app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(flask_app)

    with flask_app.app_context():
        with db.engine.begin() as conn:
            conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            conn.execute(text(
                """
                CREATE TABLE courses (
                    id INTEGER PRIMARY KEY,
                    code VARCHAR(20) NOT NULL UNIQUE,
                    name VARCHAR(255) NOT NULL,
                    credits INTEGER NOT NULL,
                    is_deleted BOOLEAN NOT NULL DEFAULT false
                )
                """
            ))

        app_package._auto_init_scheduler_support()
        app_package._auto_init_scheduler_support()

        inspector = inspect(db.engine)
        course_columns = {column["name"] for column in inspector.get_columns("courses")}

        assert SCHEDULER_COURSE_COLUMNS.issubset(course_columns)
        assert SCHEDULER_TABLES.issubset(set(inspector.get_table_names()))
