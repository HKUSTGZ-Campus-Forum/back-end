from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.run_scheduler_popularity_cron as cron_launcher

from scripts.run_scheduler_popularity_cron import (
    CAMPAIGN_START_EPOCH,
    REGULAR_FINAL_SLOT_EPOCH,
    TERMINAL_SLOT_EPOCH,
    LauncherError,
    missed_expected_invocation,
    mutation_lock,
    sampler_command,
    scheduled_slot,
    validate_environment_file,
    validate_lock_file_path,
    validate_release,
)
from tools.render_scheduler_popularity_crontab import (
    BEGIN_MARKER,
    END_MARKER,
    ManagedBlockError,
    render,
)
from tools.render_scheduler_popularity_crontab import main as render_main


def test_campaign_epochs_are_exact_shanghai_instants():
    assert datetime.fromtimestamp(CAMPAIGN_START_EPOCH, timezone.utc).isoformat() == (
        "2026-07-31T16:00:00+00:00"
    )
    assert datetime.fromtimestamp(REGULAR_FINAL_SLOT_EPOCH, timezone.utc).isoformat() == (
        "2026-09-30T15:55:00+00:00"
    )
    assert datetime.fromtimestamp(TERMINAL_SLOT_EPOCH, timezone.utc).isoformat() == (
        "2026-09-30T15:59:00+00:00"
    )


def test_regular_window_accepts_only_current_slot_with_bounded_launch_delay():
    assert scheduled_slot("regular", CAMPAIGN_START_EPOCH) == CAMPAIGN_START_EPOCH
    assert scheduled_slot("regular", CAMPAIGN_START_EPOCH + 120) == CAMPAIGN_START_EPOCH
    assert scheduled_slot("regular", CAMPAIGN_START_EPOCH + 121) is None
    assert scheduled_slot("regular", REGULAR_FINAL_SLOT_EPOCH + 120) == REGULAR_FINAL_SLOT_EPOCH
    assert scheduled_slot("regular", REGULAR_FINAL_SLOT_EPOCH + 121) is None
    assert scheduled_slot("regular", CAMPAIGN_START_EPOCH - 1) is None
    # The recurring Aug/Sep cron line is harmless if still present next year.
    assert scheduled_slot("regular", CAMPAIGN_START_EPOCH + 365 * 86400) is None
    assert missed_expected_invocation("regular", CAMPAIGN_START_EPOCH + 121) is True
    assert missed_expected_invocation("regular", REGULAR_FINAL_SLOT_EPOCH + 121) is True
    assert missed_expected_invocation("regular", CAMPAIGN_START_EPOCH - 1) is False


def test_terminal_is_one_narrow_observed_attempt():
    assert scheduled_slot("terminal", TERMINAL_SLOT_EPOCH - 1) is None
    assert scheduled_slot("terminal", TERMINAL_SLOT_EPOCH) == TERMINAL_SLOT_EPOCH
    assert scheduled_slot("terminal", TERMINAL_SLOT_EPOCH + 55) == TERMINAL_SLOT_EPOCH
    assert scheduled_slot("terminal", TERMINAL_SLOT_EPOCH + 56) is None
    assert missed_expected_invocation("terminal", TERMINAL_SLOT_EPOCH + 56) is True
    assert missed_expected_invocation("terminal", TERMINAL_SLOT_EPOCH + 300) is False


def test_terminal_command_never_lets_the_sampler_backdate_state():
    command, timeout_seconds = sampler_command(
        "terminal",
        python_path=Path("/release/venv/bin/python"),
        sampler_path=Path("/release/scripts/sample_scheduler_popularity.py"),
        scheduled_epoch=TERMINAL_SLOT_EPOCH,
    )
    assert command[-2:] == ["--lock-wait-seconds", "0"]
    assert "--terminal" in command
    assert "--expected-database" in command
    assert command[command.index("--expected-database") + 1] == "prod_unikorn"
    assert command[command.index("--scheduled-at") + 1] == "2026-09-30T15:59:00Z"
    assert command[command.index("--commit-deadline") + 1] == "2026-09-30T15:59:55Z"
    assert timeout_seconds == 45


def test_terminal_launcher_uses_only_a_bounded_outer_lock_wait():
    launcher = Path(cron_launcher.__file__).read_text(encoding="utf-8")
    assert 'lock_wait_seconds = 10 if args.mode == "terminal" else 0' in launcher
    assert 'with mutation_lock(lock_file, wait_seconds=lock_wait_seconds)' in launcher


def test_status_command_is_read_only_and_database_pinned():
    command, timeout_seconds = sampler_command(
        "status",
        python_path=Path("/release/venv/bin/python"),
        sampler_path=Path("/release/scripts/sample_scheduler_popularity.py"),
        scheduled_epoch=CAMPAIGN_START_EPOCH,
    )
    assert "--status" in command
    assert "--baseline" not in command
    assert "--scheduled-at" not in command
    assert command[command.index("--expected-database") + 1] == "prod_unikorn"
    assert timeout_seconds == 45


def test_status_mode_cannot_authorize_a_pre_campaign_baseline():
    assert scheduled_slot("status", CAMPAIGN_START_EPOCH - 1) is None
    assert scheduled_slot("status", CAMPAIGN_START_EPOCH) == CAMPAIGN_START_EPOCH


def test_baseline_is_not_permitted_after_the_final_regular_slot():
    assert scheduled_slot("baseline", REGULAR_FINAL_SLOT_EPOCH) == REGULAR_FINAL_SLOT_EPOCH
    assert scheduled_slot("baseline", REGULAR_FINAL_SLOT_EPOCH + 1) is None


def test_regular_command_passes_exact_slot_and_transaction_deadline():
    command, timeout_seconds = sampler_command(
        "regular",
        python_path=Path("/release/venv/bin/python"),
        sampler_path=Path("/release/scripts/sample_scheduler_popularity.py"),
        scheduled_epoch=CAMPAIGN_START_EPOCH,
    )
    assert command[command.index("--scheduled-at") + 1] == "2026-07-31T16:00:00Z"
    assert command[command.index("--commit-deadline") + 1] == "2026-07-31T16:02:00Z"
    assert timeout_seconds == 125


def test_environment_file_is_pinned_to_production_path():
    with pytest.raises(LauncherError, match="environment file must be"):
        validate_environment_file(Path("/tmp/substituted.env"))


def test_environment_file_requires_exact_root_www_data_metadata(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=redacted\n", encoding="utf-8")
    os.chmod(env_file, 0o640)
    monkeypatch.setattr(cron_launcher, "EXPECTED_ENV_FILE", env_file)
    monkeypatch.setattr(
        cron_launcher.grp,
        "getgrnam",
        lambda _name: SimpleNamespace(gr_gid=os.getgid()),
    )
    # The fixture belongs to the test user, not root; exact metadata is mandatory.
    with pytest.raises(LauncherError, match="unsafe owner|root:www-data mode 0640"):
        validate_environment_file(env_file)


def test_release_requires_the_exact_pinned_production_venv_symlink(
    tmp_path, monkeypatch
):
    release = tmp_path / ("a" * 40 + ".v2")
    scripts_dir = release / "scripts"
    scripts_dir.mkdir(parents=True)
    launcher = scripts_dir / "run_scheduler_popularity_cron.py"
    sampler = scripts_dir / "sample_scheduler_popularity.py"
    launcher.write_text("# launcher\n", encoding="utf-8")
    sampler.write_text("# sampler\n", encoding="utf-8")
    (release / ".unikorn-commit").write_text("a" * 40 + "\n", encoding="ascii")
    (release / "venv" / "bin").mkdir(parents=True)
    fake_python = release / "venv" / "bin" / "python"
    fake_python.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_python.chmod(0o755)
    launcher.chmod(0o444)
    sampler.chmod(0o444)
    (release / ".unikorn-commit").chmod(0o444)
    scripts_dir.chmod(0o555)
    release.chmod(0o555)
    monkeypatch.setattr(cron_launcher, "__file__", str(launcher))
    try:
        with pytest.raises(LauncherError, match="pinned production venv symlink"):
            validate_release("a" * 40, str(launcher))
    finally:
        release.chmod(0o755)
        scripts_dir.chmod(0o755)
        launcher.chmod(0o644)
        sampler.chmod(0o644)
        (release / ".unikorn-commit").chmod(0o644)


def test_release_runtime_rejects_owner_writable_immutable_content(
    tmp_path, monkeypatch
):
    sha = "a" * 40
    app_dir = tmp_path / "app"
    venv_bin = app_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    python = venv_bin / "python"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o555)

    release = tmp_path / f"{sha}.v2"
    scripts_dir = release / "scripts"
    scripts_dir.mkdir(parents=True)
    launcher = scripts_dir / "run_scheduler_popularity_cron.py"
    sampler = scripts_dir / "sample_scheduler_popularity.py"
    commit = release / ".unikorn-commit"
    launcher.write_text("# launcher\n", encoding="utf-8")
    sampler.write_text("# sampler\n", encoding="utf-8")
    commit.write_text(f"{sha}\n", encoding="ascii")
    (release / "venv").symlink_to(app_dir / "venv")
    for path in (launcher, sampler, commit):
        path.chmod(0o444)
    scripts_dir.chmod(0o555)
    release.chmod(0o555)

    monkeypatch.setattr(cron_launcher, "EXPECTED_ENV_FILE", app_dir / ".env")
    try:
        assert validate_release(sha, str(launcher))[0] == release

        launcher.chmod(0o644)
        with pytest.raises(LauncherError, match="unsafe owner or write mode"):
            validate_release(sha, str(launcher))
    finally:
        release.chmod(0o755)
        scripts_dir.chmod(0o755)
        launcher.chmod(0o644)
        sampler.chmod(0o644)
        commit.chmod(0o644)


def test_mutation_lock_path_is_pinned_to_shared_production_lock():
    with pytest.raises(LauncherError, match="mutation lock must be"):
        validate_lock_file_path(Path("/tmp/dummy.lock"))


def test_mutation_lock_is_nonblocking_and_validates_metadata(tmp_path):
    lock_path = tmp_path / "backend-mutations.lock"
    lock_path.touch(mode=0o600)
    os.chmod(lock_path, 0o600)
    with lock_path.open("r+") as competing:
        fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with mutation_lock(lock_path) as acquired:
            assert acquired is False
        fcntl.flock(competing, fcntl.LOCK_UN)
    with mutation_lock(lock_path) as acquired:
        assert acquired is True


def test_mutation_lock_reuses_validated_inherited_deployment_descriptor(
    tmp_path, monkeypatch
):
    lock_path = tmp_path / "backend-mutations.lock"
    lock_path.touch(mode=0o600)
    os.chmod(lock_path, 0o600)
    with lock_path.open("r+") as deployment_lock:
        fcntl.flock(deployment_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = os.fstat(deployment_lock.fileno())
        monkeypatch.setenv(
            "UNIKORN_BACKEND_MUTATION_LOCK_FD", str(deployment_lock.fileno())
        )
        monkeypatch.setenv(
            "UNIKORN_BACKEND_MUTATION_LOCK_DEV_INO",
            f"{metadata.st_dev}:{metadata.st_ino}",
        )
        with mutation_lock(lock_path) as acquired:
            assert acquired is True
        fcntl.flock(deployment_lock, fcntl.LOCK_UN)


def test_launcher_persists_only_anonymous_allowlisted_sampler_output(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    os.chmod(state_dir, 0o700)
    lock_path = tmp_path / "lock"
    lock_path.touch(mode=0o600)
    os.chmod(lock_path, 0o600)
    release = tmp_path / "release"
    release.mkdir()
    fake_python = release / "python"
    fake_sampler = release / "sampler.py"
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=secret-value\n", encoding="utf-8")

    monkeypatch.setattr(cron_launcher, "validate_state_directory", lambda _path: state_dir)
    monkeypatch.setattr(
        cron_launcher,
        "validate_release",
        lambda _sha, _script: (release, fake_python, fake_sampler),
    )
    monkeypatch.setattr(cron_launcher, "validate_environment_file", lambda _path: env_file)
    monkeypatch.setattr(cron_launcher, "validate_lock_file_path", lambda _path: lock_path)
    monkeypatch.setattr(
        cron_launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: cron_launcher.subprocess.CompletedProcess(
            [],
            0,
            '{"status":"completed","semester_id":"2610",'
            '"bucket_at":"2026-07-31T16:00:00Z",'
            '"observed_at":"2026-07-31T16:00:01Z",'
            '"course_facts":2,"section_facts":3}',
        ),
    )
    args = SimpleNamespace(
        mode="regular",
        expected_sha="a" * 40,
        env_file=env_file,
        lock_file=lock_path,
        state_dir=state_dir,
    )
    assert cron_launcher.run(args, now_epoch=CAMPAIGN_START_EPOCH) == 0
    persisted = (state_dir / "sampler.log").read_text(encoding="utf-8")
    assert "secret-value" not in persisted
    assert "user_id" not in persisted
    assert "bucket_at" in persisted


def test_launcher_rejects_success_payload_with_secret_or_unknown_fields(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    os.chmod(state_dir, 0o700)
    lock_path = tmp_path / "lock"
    lock_path.touch(mode=0o600)
    os.chmod(lock_path, 0o600)
    release = tmp_path / "release"
    release.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=redacted\n", encoding="utf-8")
    monkeypatch.setattr(cron_launcher, "validate_state_directory", lambda _path: state_dir)
    monkeypatch.setattr(
        cron_launcher,
        "validate_release",
        lambda _sha, _script: (release, release / "python", release / "sampler.py"),
    )
    monkeypatch.setattr(cron_launcher, "validate_environment_file", lambda _path: env_file)
    monkeypatch.setattr(cron_launcher, "validate_lock_file_path", lambda _path: lock_path)
    monkeypatch.setattr(
        cron_launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: cron_launcher.subprocess.CompletedProcess(
            [],
            0,
            '{"status":"completed","semester_id":"2610",'
            '"bucket_at":"2026-07-31T16:00:00Z",'
            '"observed_at":"2026-07-31T16:00:01Z",'
            '"course_facts":2,"section_facts":3,"database_url":"secret"}',
        ),
    )
    args = SimpleNamespace(
        mode="regular",
        expected_sha="a" * 40,
        env_file=env_file,
        lock_file=lock_path,
        state_dir=state_dir,
    )
    assert cron_launcher.run(args, now_epoch=CAMPAIGN_START_EPOCH) == 65
    persisted = (state_dir / "sampler.log").read_text(encoding="utf-8")
    assert "secret" not in persisted


def test_launcher_returns_nonzero_for_delayed_in_campaign_invocation(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    os.chmod(state_dir, 0o700)
    monkeypatch.setattr(cron_launcher, "validate_state_directory", lambda _path: state_dir)
    args = SimpleNamespace(
        mode="regular",
        expected_sha="a" * 40,
        env_file=Path("/unused"),
        lock_file=Path("/unused"),
        state_dir=state_dir,
    )
    assert cron_launcher.run(args, now_epoch=CAMPAIGN_START_EPOCH + 121) == 75
    latest = (state_dir / "latest.json").read_text(encoding="utf-8")
    assert "gap_missed_launch_deadline" in latest


def test_launcher_rejects_zero_exit_with_wrong_success_payload(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    os.chmod(state_dir, 0o700)
    lock_path = tmp_path / "lock"
    lock_path.touch(mode=0o600)
    os.chmod(lock_path, 0o600)
    release = tmp_path / "release"
    release.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=redacted\n", encoding="utf-8")
    monkeypatch.setattr(cron_launcher, "validate_state_directory", lambda _path: state_dir)
    monkeypatch.setattr(
        cron_launcher,
        "validate_release",
        lambda _sha, _script: (release, release / "python", release / "sampler.py"),
    )
    monkeypatch.setattr(cron_launcher, "validate_environment_file", lambda _path: env_file)
    monkeypatch.setattr(cron_launcher, "validate_lock_file_path", lambda _path: lock_path)
    monkeypatch.setattr(
        cron_launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: cron_launcher.subprocess.CompletedProcess(
            [], 0, '{"status":"completed","semester_id":"9999"}'
        ),
    )
    args = SimpleNamespace(
        mode="regular",
        expected_sha="a" * 40,
        env_file=env_file,
        lock_file=lock_path,
        state_dir=state_dir,
    )
    assert cron_launcher.run(args, now_epoch=CAMPAIGN_START_EPOCH) == 65
    assert "sampler_failed_gap" in (state_dir / "latest.json").read_text()


def test_launcher_returns_zero_when_recurring_cron_is_outside_campaign(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    os.chmod(state_dir, 0o700)
    monkeypatch.setattr(cron_launcher, "validate_state_directory", lambda _path: state_dir)
    args = SimpleNamespace(
        mode="regular",
        expected_sha="a" * 40,
        env_file=Path("/unused"),
        lock_file=Path("/unused"),
        state_dir=state_dir,
    )
    assert cron_launcher.run(args, now_epoch=CAMPAIGN_START_EPOCH - 1) == 0
    latest = (state_dir / "latest.json").read_text(encoding="utf-8")
    assert "skipped_outside_campaign_window" in latest


def _block(command: str = "/immutable/runner --mode regular") -> str:
    return f"{BEGIN_MARKER}\n{command}\n{END_MARKER}\n"


def test_managed_crontab_append_preserves_existing_bytes():
    existing = "MAILTO=owner@example.edu\n17 3 * * * /existing/job\n"
    assert render(existing, _block()) == existing + _block()


@pytest.mark.parametrize(
    "directive",
    ("CRON_TZ=UTC\n", "  CRON_TZ = America/New_York\n"),
)
def test_managed_crontab_rejects_external_active_cron_timezone(directive):
    with pytest.raises(ManagedBlockError, match="CRON_TZ"):
        render(directive + "17 3 * * * /existing/job\n", _block())


def test_managed_crontab_allows_commented_cron_timezone():
    existing = "# CRON_TZ=UTC\n17 3 * * * /existing/job\n"
    assert render(existing, _block()) == existing + _block()


def test_managed_crontab_replacement_preserves_everything_outside_markers():
    existing = (
        "MAILTO=owner@example.edu\n"
        + _block("/old/release")
        + "17 3 * * * /existing/job\n"
    )
    updated = render(existing, _block("/new/release"))
    assert updated == (
        "MAILTO=owner@example.edu\n"
        + _block("/new/release")
        + "17 3 * * * /existing/job\n"
    )


def test_managed_crontab_removal_preserves_everything_outside_markers():
    existing = "before\n" + _block() + "after\n"
    assert render(existing, None) == "before\nafter\n"


@pytest.mark.parametrize(
    "existing",
    (
        f"{BEGIN_MARKER}\ncommand\n",
        f"{END_MARKER}\n",
        f"{END_MARKER}\n{BEGIN_MARKER}\n",
        f"{BEGIN_MARKER}\n{BEGIN_MARKER}\n{END_MARKER}\n",
        f"prefix {BEGIN_MARKER}\n{END_MARKER}\n",
    ),
)
def test_managed_crontab_rejects_ambiguous_or_malformed_markers(existing):
    with pytest.raises(ManagedBlockError):
        render(existing, _block())


def test_render_cli_preserves_non_utf8_bytes_outside_managed_block(tmp_path):
    existing = tmp_path / "before"
    replacement = tmp_path / "block"
    output = tmp_path / "after"
    existing.write_bytes(b"MAILTO=owner@example.edu\n# opaque:\xff\n")
    replacement.write_text(_block("/new/release"), encoding="utf-8")
    assert render_main([
        "--existing", str(existing),
        "--replacement", str(replacement),
        "--output", str(output),
    ]) == 0
    assert output.read_bytes() == existing.read_bytes() + replacement.read_bytes()
