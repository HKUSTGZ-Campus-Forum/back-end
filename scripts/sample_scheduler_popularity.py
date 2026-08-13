"""Take or verify one idempotent scheduler-popularity history sample."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from sqlalchemy import text

# Set maintenance-mode configuration before importing the ``app`` package.
# This file intentionally lives outside that package: ``python -m app...``
# would execute app/__init__.py before the command module could set these.
os.environ["ENABLE_BACKGROUND_TASKS"] = "false"
os.environ["AUTO_INIT_ON_STARTUP"] = "false"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
if configured_env_file := os.environ.get("UNIKORN_POPULARITY_ENV_FILE"):
    # The cron launcher validates this path, its ownership, and its mode before
    # setting the variable. Loading it here keeps credentials out of both the
    # immutable release and the user crontab.
    load_dotenv(configured_env_file, override=False)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.extensions import db
from app.services.scheduler_popularity import (
    POPULARITY_HISTORY_END_AT,
    POPULARITY_HISTORY_SEMESTER,
    POPULARITY_HISTORY_START_AT,
    collect_terminal_popularity_history_sample,
    collect_popularity_history_sample,
    popularity_history_sampling_status,
    popularity_history_terminal_sample_exists,
)


def assert_expected_database(expected_database: str, *, database=None) -> None:
    """Fail closed when the sampler is pointed at a different database."""
    database = database or db
    if database.engine.dialect.name != "postgresql":
        raise RuntimeError(
            f"configured database dialect is {database.engine.dialect.name!r}, expected 'postgresql'"
        )
    configured_database = database.engine.url.database
    if configured_database != expected_database:
        raise RuntimeError(
            f"configured database is {configured_database!r}, expected {expected_database!r}"
        )
    connected_database = database.session.execute(text("SELECT current_database()")).scalar()
    if connected_database != expected_database:
        raise RuntimeError(
            f"connected database is {connected_database!r}, expected {expected_database!r}"
        )


def parse_utc_instant(value: str, *, argument: str) -> datetime:
    """Parse a timezone-explicit ISO instant and normalize it to UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{argument} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{argument} must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
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
        "--status",
        action="store_true",
        help="do not sample; report the anonymous campaign sampling status",
    )
    parser.add_argument(
        "--lock-wait-seconds",
        type=float,
        default=0,
        metavar="SECONDS",
    )
    parser.add_argument(
        "--scheduled-at",
        metavar="ISO_INSTANT",
        help="exact intended sample slot; required for sampling modes",
    )
    parser.add_argument(
        "--commit-deadline",
        metavar="ISO_INSTANT",
        help="hard transaction deadline; required for sampling modes",
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
    args = parser.parse_args(argv)
    selected_modes = sum((
        args.baseline,
        args.terminal,
        args.verify_terminal,
        args.status,
        args.verify_freshness_seconds is not None,
    ))
    if selected_modes > 1:
        parser.error("sampling and verification modes are mutually exclusive")
    if args.terminal and args.semester != POPULARITY_HISTORY_SEMESTER:
        parser.error(
            f"terminal sampling is only available for semester {POPULARITY_HISTORY_SEMESTER}"
        )
    if args.verify_freshness_seconds is not None and args.verify_freshness_seconds < 0:
        parser.error("--verify-freshness-seconds must be non-negative")
    if args.lock_wait_seconds < 0:
        parser.error("--lock-wait-seconds must be non-negative")

    verification_mode = (
        args.verify_terminal
        or args.status
        or args.verify_freshness_seconds is not None
    )
    if verification_mode and (args.scheduled_at or args.commit_deadline):
        parser.error("verification modes do not accept sampling timestamps")
    if not verification_mode and not (args.scheduled_at and args.commit_deadline):
        parser.error("sampling modes require --scheduled-at and --commit-deadline")
    scheduled_at = commit_deadline = None
    if args.scheduled_at and args.commit_deadline:
        try:
            scheduled_at = parse_utc_instant(args.scheduled_at, argument="--scheduled-at")
            commit_deadline = parse_utc_instant(
                args.commit_deadline,
                argument="--commit-deadline",
            )
        except ValueError as exc:
            parser.error(str(exc))
        if scheduled_at > commit_deadline:
            parser.error("--scheduled-at must not be after --commit-deadline")
        if datetime.now(timezone.utc) > commit_deadline:
            parser.error("sampling invocation missed its hard commit deadline")
        if args.terminal:
            expected_deadline = POPULARITY_HISTORY_END_AT + timedelta(seconds=55)
            if scheduled_at != POPULARITY_HISTORY_END_AT or commit_deadline != expected_deadline:
                parser.error("terminal timestamps must use the fixed cutoff and 55-second deadline")
        elif args.baseline:
            if scheduled_at < POPULARITY_HISTORY_START_AT:
                parser.error("baseline timestamp is before the tracking campaign")
            if scheduled_at > POPULARITY_HISTORY_END_AT:
                parser.error("baseline timestamp is after the tracking cutoff")
            if commit_deadline - scheduled_at > timedelta(seconds=120):
                parser.error("baseline deadline may be at most 120 seconds after its timestamp")
        else:
            if scheduled_at < POPULARITY_HISTORY_START_AT:
                parser.error("regular sample timestamp is before the tracking campaign")
            if scheduled_at.second or scheduled_at.microsecond or scheduled_at.minute % 5:
                parser.error("regular sample timestamp must be aligned to a five-minute slot")
            if scheduled_at > POPULARITY_HISTORY_END_AT:
                parser.error("regular sample timestamp is after the tracking cutoff")
            if commit_deadline - scheduled_at > timedelta(seconds=120):
                parser.error("regular sample deadline may be at most 120 seconds after its slot")

    args.scheduled_at_value = scheduled_at
    args.commit_deadline_value = commit_deadline
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app = create_app()
    with app.app_context():
        if args.expected_database:
            assert_expected_database(args.expected_database)
        if args.status:
            result = popularity_history_sampling_status(semester_id=args.semester)
        elif args.verify_freshness_seconds is not None:
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
            result = collect_terminal_popularity_history_sample(
                lock_wait_seconds=args.lock_wait_seconds,
            )
        else:
            result = collect_popularity_history_sample(
                semester_id=args.semester,
                sampled_at=args.scheduled_at_value,
                baseline=args.baseline,
                lock_wait_seconds=args.lock_wait_seconds,
                commit_deadline=args.commit_deadline_value,
            )
        if result.get("status") == "locked":
            print(json.dumps(result, sort_keys=True))
            return 75
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
