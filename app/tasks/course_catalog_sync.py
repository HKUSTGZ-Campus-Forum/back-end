from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.services.official_course_catalog_sync import (
    fetch_official_course_catalog,
    sync_official_course_catalog_records,
)


def init_course_catalog_sync(app, scheduler) -> None:
    if not app.config.get("COURSE_CATALOG_SYNC_ENABLED", False):
        app.logger.info("Official course catalog synchronization is disabled.")
        return
    scheduler.add_job(
        id="official_course_catalog_sync",
        func=_course_catalog_sync_job,
        args=[app],
        trigger="interval",
        hours=app.config["COURSE_CATALOG_SYNC_INTERVAL_HOURS"],
        max_instances=1,
        coalesce=True,
        misfire_grace_time=900,
        replace_existing=True,
    )
    app.logger.info(
        "Official course catalog synchronization scheduled every %s hours.",
        app.config["COURSE_CATALOG_SYNC_INTERVAL_HOURS"],
    )


def _course_catalog_sync_job(app) -> None:
    with app.app_context():
        try:
            records, _pagination = fetch_official_course_catalog(
                url=current_app.config["COURSE_CATALOG_SYNC_URL"],
                term=current_app.config["COURSE_CATALOG_SYNC_TERM"],
                career=current_app.config["COURSE_CATALOG_SYNC_CAREER"],
                timeout_seconds=current_app.config["COURSE_CATALOG_SYNC_TIMEOUT_SECONDS"],
            )
            result = sync_official_course_catalog_records(
                records,
                term=current_app.config["COURSE_CATALOG_SYNC_TERM"],
                apply=True,
                min_courses=current_app.config["COURSE_CATALOG_SYNC_MIN_COURSES"],
                max_courses=current_app.config["COURSE_CATALOG_SYNC_MAX_COURSES"],
            )
            current_app.logger.info("Official course catalog sync result: %s", result)
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Official course catalog synchronization failed")
