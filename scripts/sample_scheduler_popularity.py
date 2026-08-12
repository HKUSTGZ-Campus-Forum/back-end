"""Take or verify one idempotent scheduler-popularity history sample."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from sqlalchemy import text

# Set maintenance-mode configuration before importing the ``app`` package.
# This file intentionally lives outside that package: ``python -m app...``
# would execute app/__init__.py before the command module could set these.
os.environ["ENABLE_BACKGROUND_TASKS"] = "false"
os.environ["AUTO_INIT_ON_STARTUP"] = "false"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.extensions import db
from app.services.scheduler_popularity import (
    POPULARITY_HISTORY_SEMESTER,
    collect_terminal_popularity_history_sample,
    collect_popularity_history_sample,
    popularity_history_sampling_status,
    popularity_history_terminal_sample_exists,
)


def assert_expected_database(expected_database: str) -> None:
    """Fail closed when the sampler is pointed at a different database."""
    configured_database = db.engine.url.database
    if configured_database != expected_database:
        raise RuntimeError(
            f"configured database is {configured_database!r}, expected {expected_database!r}"
        )
    if db.engine.dialect.name == "postgresql":
        connected_database = db.session.execute(text("SELECT current_database()")).scalar()
        if connected_database != expected_database:
            raise RuntimeError(
                f"connected database is {connected_database!r}, expected {expected_database!r}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semester", default=POPULARITY_HISTORY_SEMESTER)
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="record the exact deployment start, but only if tracking has not started",
    )
    parser.add_argument(
        "--terminal",
        action="store_true",
        help="capture the fixed cutoff bucket inside the permitted execution window",
    )
    parser.add_argument(
        "--verify-terminal",
        action="store_true",
        help="do not sample; fail unless the exact cutoff bucket exists",
    )
    parser.add_argument(
        "--terminal-tolerance-seconds",
        type=int,
        default=120,
        metavar="SECONDS",
    )
    parser.add_argument(
        "--lock-wait-seconds",
        type=float,
        default=0,
        metavar="SECONDS",
    )
    parser.add_argument(
        "--expected-database",
        metavar="NAME",
        help="fail before sampling unless both configured and connected database names match",
    )
    parser.add_argument(
        "--verify-freshness-seconds",
        type=int,
        metavar="SECONDS",
        help="do not sample; fail unless the latest completed run is this recent",
    )
    args = parser.parse_args()
    selected_modes = sum((
        args.baseline,
        args.terminal,
        args.verify_terminal,
        args.verify_freshness_seconds is not None,
    ))
    if selected_modes > 1:
        parser.error("sampling and verification modes are mutually exclusive")
    if args.verify_freshness_seconds is not None and args.verify_freshness_seconds < 0:
        parser.error("--verify-freshness-seconds must be non-negative")
    if args.terminal_tolerance_seconds < 0:
        parser.error("--terminal-tolerance-seconds must be non-negative")
    if args.lock_wait_seconds < 0:
        parser.error("--lock-wait-seconds must be non-negative")

    app = create_app()
    with app.app_context():
        if args.expected_database:
            assert_expected_database(args.expected_database)
        if args.verify_freshness_seconds is not None:
            result = popularity_history_sampling_status(semester_id=args.semester)
            if result["age_seconds"] is None or result["age_seconds"] > args.verify_freshness_seconds:
                print(json.dumps(result, sort_keys=True))
                return 1
        elif args.verify_terminal:
            result = {
                "semester_id": args.semester,
                "terminal_sample_exists": popularity_history_terminal_sample_exists(),
            }
            if args.semester != POPULARITY_HISTORY_SEMESTER or not result["terminal_sample_exists"]:
                print(json.dumps(result, sort_keys=True))
                return 1
        elif args.terminal:
            if args.semester != POPULARITY_HISTORY_SEMESTER:
                parser.error("terminal sampling is only available for semester 2610")
            result = collect_terminal_popularity_history_sample(
                tolerance_seconds=args.terminal_tolerance_seconds,
                lock_wait_seconds=args.lock_wait_seconds,
            )
        else:
            result = collect_popularity_history_sample(
                semester_id=args.semester,
                baseline=args.baseline,
                lock_wait_seconds=args.lock_wait_seconds,
            )
        if result.get("status") == "locked":
            print(json.dumps(result, sort_keys=True))
            return 75
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
