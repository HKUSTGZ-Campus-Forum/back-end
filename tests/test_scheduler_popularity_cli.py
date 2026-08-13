from types import SimpleNamespace
from unittest.mock import Mock
from datetime import datetime, timedelta, timezone

import pytest

import scripts.sample_scheduler_popularity as sampler_cli


def test_sampler_database_guard_rejects_non_postgresql(monkeypatch):
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="sqlite"),
        url=SimpleNamespace(database="prod_unikorn"),
    )
    with pytest.raises(RuntimeError, match="expected 'postgresql'"):
        sampler_cli.assert_expected_database(
            "prod_unikorn",
            database=SimpleNamespace(engine=engine),
        )


def test_sampler_database_guard_checks_configured_and_connected_name(monkeypatch):
    engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        url=SimpleNamespace(database="prod_unikorn"),
    )
    session = Mock()
    session.execute.return_value.scalar.return_value = "other_database"
    with pytest.raises(RuntimeError, match="connected database"):
        sampler_cli.assert_expected_database(
            "prod_unikorn",
            database=SimpleNamespace(engine=engine, session=session),
        )


def test_regular_cli_requires_explicit_slot_and_deadline():
    with pytest.raises(SystemExit):
        sampler_cli.parse_args(["--expected-database", "prod_unikorn"])


def test_regular_cli_preserves_exact_slot_and_deadline():
    slot = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    slot += timedelta(minutes=(5 - slot.minute % 5) % 5)
    if slot <= datetime.now(timezone.utc):
        slot += timedelta(minutes=5)
    deadline = slot + timedelta(seconds=120)
    args = sampler_cli.parse_args([
        "--expected-database",
        "prod_unikorn",
        "--scheduled-at",
        slot.isoformat(),
        "--commit-deadline",
        deadline.isoformat(),
    ])
    assert args.scheduled_at_value == slot
    assert args.commit_deadline_value == deadline


def test_terminal_cli_does_not_expose_a_tolerance_override():
    with pytest.raises(SystemExit):
        sampler_cli.parse_args([
            "--terminal",
            "--terminal-tolerance-seconds",
            "120",
            "--scheduled-at",
            "2026-09-30T15:59:00Z",
            "--commit-deadline",
            "2026-09-30T15:59:55Z",
        ])


def test_status_mode_is_read_only_and_needs_no_timestamps():
    args = sampler_cli.parse_args([
        "--status",
        "--expected-database",
        "prod_unikorn",
    ])
    assert args.status is True
    assert args.scheduled_at_value is None
    assert args.commit_deadline_value is None
