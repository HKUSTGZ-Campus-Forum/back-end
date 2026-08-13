"""Fail-closed launcher for the bounded 2026 scheduler-popularity campaign.

The user crontab deliberately invokes an immutable release path.  This wrapper
checks that release, the production environment file, and the shared backend
mutation lock before starting the sampler.  Missed runs remain honest gaps; it
never catches up an earlier bucket with later database state.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Iterator, TextIO


CAMPAIGN_START_EPOCH = 1785513600  # 2026-08-01 00:00 Asia/Shanghai
REGULAR_FINAL_SLOT_EPOCH = 1790783700  # 2026-09-30 23:55 Asia/Shanghai
TERMINAL_SLOT_EPOCH = 1790783940  # 2026-09-30 23:59 Asia/Shanghai
REGULAR_LAUNCH_TOLERANCE_SECONDS = 120
# The terminal observation must still occur within the requested 23:59
# Asia/Shanghai wall-clock minute. A delayed launch after that minute is an
# honest missing terminal sample, never an October 1 observation relabelled as
# September 30.
TERMINAL_LAUNCH_TOLERANCE_SECONDS = 55
INTERVAL_SECONDS = 300
EXPECTED_DATABASE = "prod_unikorn"
EXPECTED_SEMESTER = "2610"
EXPECTED_ENV_FILE = Path("/data/prod_unikorn/back-end/.env")
EXPECTED_LOCK_FILE = Path(
    "/data/prod_unikorn/back-end/.git/unikorn-operations/backend-mutations.lock"
)
STATE_DIRECTORY_RELATIVE = Path(".local/state/unikorn/scheduler-popularity-2610")
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
_RELEASE_NAME_PATTERN = re.compile(r"([0-9a-f]{40})\.v[1-9][0-9]*")


class LauncherError(RuntimeError):
    """A fail-closed launcher validation error."""


def scheduled_slot(mode: str, now_epoch: int) -> int | None:
    """Return the permitted scheduled epoch, or ``None`` outside the campaign."""
    if mode == "regular":
        slot = now_epoch - now_epoch % INTERVAL_SECONDS
        if not CAMPAIGN_START_EPOCH <= slot <= REGULAR_FINAL_SLOT_EPOCH:
            return None
        if now_epoch - slot > REGULAR_LAUNCH_TOLERANCE_SECONDS:
            return None
        return slot
    if mode == "terminal":
        if TERMINAL_SLOT_EPOCH <= now_epoch <= (
            TERMINAL_SLOT_EPOCH + TERMINAL_LAUNCH_TOLERANCE_SECONDS
        ):
            return TERMINAL_SLOT_EPOCH
        return None
    if mode == "baseline":
        if CAMPAIGN_START_EPOCH <= now_epoch <= REGULAR_FINAL_SLOT_EPOCH:
            return now_epoch
        return None
    if mode == "status":
        if CAMPAIGN_START_EPOCH <= now_epoch:
            return now_epoch
        return None
    if mode in {"verify-freshness", "verify-terminal"}:
        return now_epoch
    raise LauncherError(f"unsupported launcher mode: {mode}")


def missed_expected_invocation(mode: str, now_epoch: int) -> bool:
    """Return whether a campaign invocation arrived too late to sample truthfully."""
    if mode == "regular":
        return (
            CAMPAIGN_START_EPOCH
            <= now_epoch
            < TERMINAL_SLOT_EPOCH
        )
    if mode == "terminal":
        return TERMINAL_SLOT_EPOCH <= now_epoch < TERMINAL_SLOT_EPOCH + INTERVAL_SECONDS
    return False


def _mode_bits(path: Path, *, follow_symlinks: bool = False) -> int:
    return stat.S_IMODE(path.stat(follow_symlinks=follow_symlinks).st_mode)


def _validate_owned_directory(
    path: Path,
    *,
    uid: int,
    exact_mode: int | None = None,
    immutable: bool = False,
) -> None:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise LauncherError(f"required directory is not a real directory: {path}")
    mode = stat.S_IMODE(metadata.st_mode)
    forbidden_write_bits = 0o222 if immutable else 0o022
    if metadata.st_uid != uid or mode & forbidden_write_bits:
        raise LauncherError(f"directory has unsafe owner or write mode: {path}")
    if exact_mode is not None and mode != exact_mode:
        raise LauncherError(f"directory must use mode {exact_mode:04o}: {path}")


def _validate_regular_file(
    path: Path,
    *,
    allowed_uids: set[int],
    require_executable: bool = False,
    immutable: bool = False,
) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise LauncherError(f"required path is not a real regular file: {path}")
    mode = stat.S_IMODE(metadata.st_mode)
    forbidden_write_bits = 0o222 if immutable else 0o022
    if metadata.st_uid not in allowed_uids or mode & forbidden_write_bits:
        raise LauncherError(f"file has unsafe owner or write mode: {path}")
    if require_executable and not mode & 0o100:
        raise LauncherError(f"file is not owner-executable: {path}")


def validate_release(expected_sha: str, script_file: str) -> tuple[Path, Path, Path]:
    """Validate the immutable release and return root, Python, and sampler paths."""
    if not _SHA_PATTERN.fullmatch(expected_sha):
        raise LauncherError("expected release SHA must be a full lowercase Git SHA")

    launcher = Path(script_file).resolve(strict=True)
    _validate_regular_file(
        launcher, allowed_uids={os.getuid()}, immutable=True
    )
    release_root = launcher.parent.parent
    match = _RELEASE_NAME_PATTERN.fullmatch(release_root.name)
    if match is None or match.group(1) != expected_sha:
        raise LauncherError("launcher is not inside the expected SHA-named release")
    _validate_owned_directory(release_root, uid=os.getuid(), immutable=True)
    _validate_owned_directory(
        release_root / "scripts", uid=os.getuid(), immutable=True
    )

    commit_file = release_root / ".unikorn-commit"
    sampler = release_root / "scripts" / "sample_scheduler_popularity.py"
    venv_link = release_root / "venv"
    python_link = release_root / "venv" / "bin" / "python"
    _validate_regular_file(
        commit_file, allowed_uids={os.getuid()}, immutable=True
    )
    _validate_regular_file(sampler, allowed_uids={os.getuid()}, immutable=True)
    if commit_file.read_text(encoding="ascii").strip() != expected_sha:
        raise LauncherError("release commit marker does not match expected SHA")
    if (
        not venv_link.is_symlink()
        or venv_link.readlink() != EXPECTED_ENV_FILE.parent / "venv"
    ):
        raise LauncherError("release venv must be the pinned production venv symlink")

    # A venv's python is normally a symlink.  Require every in-release link to
    # resolve to a root-owned executable instead of accepting an arbitrary
    # writable interpreter target.
    if not python_link.is_symlink():
        _validate_regular_file(
            python_link,
            allowed_uids={0, os.getuid()},
            require_executable=True,
        )
    resolved_python = python_link.resolve(strict=True)
    _validate_regular_file(
        resolved_python,
        allowed_uids={0, os.getuid()},
        require_executable=True,
    )
    if _mode_bits(resolved_python) & 0o022:
        raise LauncherError("resolved Python interpreter is group/world writable")
    return release_root, python_link, sampler


def validate_environment_file(path: Path) -> Path:
    requested = path.absolute()
    if requested != EXPECTED_ENV_FILE:
        raise LauncherError(f"environment file must be {EXPECTED_ENV_FILE}")
    _validate_regular_file(requested, allowed_uids={0})
    www_data_gid = grp.getgrnam("www-data").gr_gid
    metadata = requested.lstat()
    if metadata.st_gid != www_data_gid or _mode_bits(requested) != 0o640:
        raise LauncherError(
            "production environment file must be owned by root:www-data mode 0640"
        )
    if (
        os.getgid() not in {os.getegid(), *os.getgroups()}
        or www_data_gid not in {os.getegid(), *os.getgroups()}
        or not os.access(requested, os.R_OK)
    ):
        raise LauncherError("launcher account cannot read the production environment file")
    return requested


def validate_state_directory(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    expected = Path(pwd.getpwuid(os.getuid()).pw_dir) / STATE_DIRECTORY_RELATIVE
    if resolved != expected:
        raise LauncherError(f"state directory must be {expected}")
    _validate_owned_directory(resolved, uid=os.getuid(), exact_mode=0o700)
    return resolved


def validate_lock_file_path(path: Path) -> Path:
    requested = path.absolute()
    if requested != EXPECTED_LOCK_FILE:
        raise LauncherError(f"mutation lock must be {EXPECTED_LOCK_FILE}")
    return requested


def _open_log(state_dir: Path) -> TextIO:
    log_path = state_dir / "sampler.log"
    descriptor = os.open(
        log_path,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        os.close(descriptor)
        raise LauncherError("sampler log has unsafe metadata")
    return os.fdopen(descriptor, "a", encoding="utf-8", buffering=1)


def _write_status(state_dir: Path, payload: dict) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".latest.",
        dir=state_dir,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, state_dir / "latest.json")
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def mutation_lock(path: Path, *, wait_seconds: float = 0) -> Iterator[bool]:
    """Acquire the same validated lock used by production backend mutations."""
    if wait_seconds < 0:
        raise LauncherError("mutation lock wait must be non-negative")
    inherited_fd = os.environ.get("UNIKORN_BACKEND_MUTATION_LOCK_FD")
    inherited_dev_ino = os.environ.get("UNIKORN_BACKEND_MUTATION_LOCK_DEV_INO")
    if inherited_fd is not None or inherited_dev_ino is not None:
        if (
            inherited_fd is None
            or inherited_dev_ino is None
            or not inherited_fd.isdecimal()
            or int(inherited_fd) < 3
        ):
            raise LauncherError("inherited backend mutation lock metadata is malformed")
        descriptor = int(inherited_fd)
        fd_metadata = os.fstat(descriptor)
        path_metadata = path.stat(follow_symlinks=False)
        actual_dev_ino = f"{fd_metadata.st_dev}:{fd_metadata.st_ino}"
        if (
            not stat.S_ISREG(fd_metadata.st_mode)
            or (fd_metadata.st_dev, fd_metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
            or actual_dev_ino != inherited_dev_ino
            or fd_metadata.st_uid != os.getuid()
            or stat.S_IMODE(fd_metadata.st_mode) != 0o600
            or fd_metadata.st_nlink != 1
            or fd_metadata.st_size != 0
        ):
            raise LauncherError("inherited backend mutation lock is unsafe")
        try:
            # This is a no-op when the inherited open-file description already
            # owns the deployment lock, and acquires it if the caller merely
            # passed a validated but unlocked descriptor.
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LauncherError("inherited backend mutation lock is not owned") from exc
        yield True
        return

    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fd_metadata = os.fstat(descriptor)
        path_metadata = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(fd_metadata.st_mode)
            or (fd_metadata.st_dev, fd_metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
            or fd_metadata.st_uid != os.getuid()
            or stat.S_IMODE(fd_metadata.st_mode) != 0o600
            or fd_metadata.st_nlink != 1
            or fd_metadata.st_size != 0
        ):
            raise LauncherError("backend mutation lock has unsafe metadata or identity")
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        try:
            yield True
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def sampler_command(
    mode: str,
    *,
    python_path: Path,
    sampler_path: Path,
    scheduled_epoch: int,
) -> tuple[list[str], int]:
    command = [
        str(python_path),
        str(sampler_path),
        "--semester",
        EXPECTED_SEMESTER,
        "--expected-database",
        EXPECTED_DATABASE,
    ]
    scheduled_at = _utc_now_text(scheduled_epoch)
    deadline_epoch = scheduled_epoch + REGULAR_LAUNCH_TOLERANCE_SECONDS
    timeout_seconds = REGULAR_LAUNCH_TOLERANCE_SECONDS + 5
    if mode == "baseline":
        command.extend((
            "--baseline",
            "--scheduled-at",
            scheduled_at,
            "--commit-deadline",
            _utc_now_text(deadline_epoch),
            "--lock-wait-seconds",
            "0",
        ))
    elif mode == "regular":
        command.extend((
            "--scheduled-at",
            scheduled_at,
            "--commit-deadline",
            _utc_now_text(deadline_epoch),
            "--lock-wait-seconds",
            "0",
        ))
    elif mode == "terminal":
        deadline_epoch = TERMINAL_SLOT_EPOCH + TERMINAL_LAUNCH_TOLERANCE_SECONDS
        command.extend((
            "--terminal",
            "--scheduled-at",
            scheduled_at,
            "--commit-deadline",
            _utc_now_text(deadline_epoch),
            "--lock-wait-seconds",
            "0",
        ))
        timeout_seconds = 45
    elif mode == "status":
        command.append("--status")
        timeout_seconds = 45
    elif mode == "verify-freshness":
        command.extend(("--verify-freshness-seconds", "600"))
        timeout_seconds = 45
    elif mode == "verify-terminal":
        command.append("--verify-terminal")
        timeout_seconds = 45
    else:
        raise LauncherError(f"unsupported launcher mode: {mode}")
    return command, timeout_seconds


def _clean_environment(env_file: Path) -> dict[str, str]:
    account = pwd.getpwuid(os.getuid())
    return {
        "AUTO_INIT_ON_STARTUP": "false",
        "ENABLE_BACKGROUND_TASKS": "false",
        "HOME": account.pw_dir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LOGNAME": account.pw_name,
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PGCONNECT_TIMEOUT": "10",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "UNIKORN_POPULARITY_ENV_FILE": str(env_file),
        "USER": account.pw_name,
    }


def _utc_now_text(epoch: int | None = None) -> str:
    instant = datetime.fromtimestamp(
        epoch if epoch is not None else datetime.now(timezone.utc).timestamp(),
        tz=timezone.utc,
    )
    return instant.isoformat().replace("+00:00", "Z")


def _validated_sampler_result(
    mode: str,
    parsed: object,
    *,
    scheduled_epoch: int,
) -> dict:
    """Validate anonymous child output before treating an invocation as successful."""
    if not isinstance(parsed, dict) or parsed.get("semester_id") != EXPECTED_SEMESTER:
        raise LauncherError("sampler returned an invalid semester result")

    exact_keys = {
        "baseline": {
            "bucket_at", "course_facts", "observed_at", "section_facts",
            "semester_id", "status",
        },
        "regular": {
            "bucket_at", "course_facts", "observed_at", "section_facts",
            "semester_id", "status",
        },
        "terminal": {
            "bucket_at", "course_facts", "observed_at", "section_facts",
            "semester_id", "status",
        },
        "status": {
            "age_seconds", "checked_at", "latest_bucket_at", "latest_observed_at",
            "sampling_state", "semester_id", "terminal_present",
        },
        "verify-freshness": {
            "age_seconds", "checked_at", "latest_bucket_at", "latest_observed_at",
            "sampling_state", "semester_id", "terminal_present",
        },
        "verify-terminal": {"semester_id", "terminal_sample_exists"},
    }[mode]
    if set(parsed) != exact_keys:
        raise LauncherError("sampler returned an unexpected result schema")

    if mode in {"baseline", "regular", "terminal"}:
        allowed_statuses = {
            "baseline": {"completed"},
            "regular": {"completed", "already_completed"},
            "terminal": {"completed", "already_completed"},
        }[mode]
        if parsed.get("status") not in allowed_statuses:
            raise LauncherError("sampler returned an invalid sampling status")
        if parsed.get("bucket_at") != _utc_now_text(scheduled_epoch):
            raise LauncherError("sampler returned a bucket other than the propagated slot")
        for key in ("course_facts", "section_facts"):
            value = parsed.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise LauncherError("sampler returned an invalid anonymous fact count")
        if not isinstance(parsed.get("observed_at"), str):
            raise LauncherError("sampler did not return an observation timestamp")
    elif mode == "status":
        sampling_state = parsed.get("sampling_state")
        if sampling_state not in {
            "not_started",
            "fresh",
            "stale",
            "ended_complete",
            "ended_incomplete",
        }:
            raise LauncherError("sampler returned invalid campaign status")
        if not isinstance(parsed.get("terminal_present"), bool):
            raise LauncherError("sampler returned invalid terminal status")
        if not isinstance(parsed.get("checked_at"), str):
            raise LauncherError("sampler returned invalid status check timestamp")
        for key in ("latest_bucket_at", "latest_observed_at"):
            if parsed.get(key) is not None and not isinstance(parsed[key], str):
                raise LauncherError("sampler returned invalid latest-sample evidence")
        age_seconds = parsed.get("age_seconds")
        if age_seconds is not None and (
            isinstance(age_seconds, bool)
            or not isinstance(age_seconds, int)
            or age_seconds < 0
        ):
            raise LauncherError("sampler returned invalid sample age")
    elif mode == "verify-freshness":
        age_seconds = parsed.get("age_seconds")
        if (
            isinstance(age_seconds, bool)
            or not isinstance(age_seconds, int)
            or age_seconds < 0
            or age_seconds > 600
            or not isinstance(parsed.get("latest_bucket_at"), str)
            or not isinstance(parsed.get("latest_observed_at"), str)
            or not isinstance(parsed.get("checked_at"), str)
            or parsed.get("sampling_state") != "fresh"
            or not isinstance(parsed.get("terminal_present"), bool)
        ):
            raise LauncherError("sampler returned invalid freshness evidence")
    elif mode == "verify-terminal":
        if parsed.get("terminal_sample_exists") is not True:
            raise LauncherError("sampler returned invalid terminal evidence")
    else:
        raise LauncherError(f"unsupported launcher mode: {mode}")

    return parsed


def run(args: argparse.Namespace, *, now_epoch: int | None = None) -> int:
    actual_epoch = int(datetime.now(timezone.utc).timestamp()) if now_epoch is None else now_epoch
    state_dir = validate_state_directory(args.state_dir)
    started_at = _utc_now_text(actual_epoch)
    base_status = {
        "mode": args.mode,
        "release_sha": args.expected_sha,
        "scheduled_epoch": scheduled_slot(args.mode, actual_epoch),
        "started_at": started_at,
    }
    with _open_log(state_dir) as log:
        print(json.dumps({**base_status, "event": "started"}, sort_keys=True), file=log)
        if base_status["scheduled_epoch"] is None:
            missed = missed_expected_invocation(args.mode, actual_epoch)
            result = {
                **base_status,
                "exit_code": 75 if missed else 0,
                "finished_at": _utc_now_text(),
                "status": (
                    "gap_missed_launch_deadline"
                    if missed
                    else "skipped_outside_campaign_window"
                ),
            }
            _write_status(state_dir, result)
            print(json.dumps(result, sort_keys=True), file=log)
            print(json.dumps(result, sort_keys=True))
            return result["exit_code"]

        try:
            release_root, python_path, sampler_path = validate_release(
                args.expected_sha,
                __file__,
            )
            env_file = validate_environment_file(args.env_file)
            command, timeout_seconds = sampler_command(
                args.mode,
                python_path=python_path,
                sampler_path=sampler_path,
                scheduled_epoch=base_status["scheduled_epoch"],
            )
            lock_file = validate_lock_file_path(args.lock_file)
            lock_wait_seconds = 10 if args.mode == "terminal" else 0
            with mutation_lock(lock_file, wait_seconds=lock_wait_seconds) as acquired:
                if not acquired:
                    result = {
                        **base_status,
                        "exit_code": 75,
                        "finished_at": _utc_now_text(),
                        "status": "gap_backend_mutation_lock_busy",
                    }
                    _write_status(state_dir, result)
                    print(json.dumps(result, sort_keys=True), file=log)
                    print(json.dumps(result, sort_keys=True))
                    return 75
                completed = subprocess.run(
                    command,
                    cwd=release_root,
                    env=_clean_environment(env_file),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            # The sampler normally emits one anonymous JSON result. Never
            # persist arbitrary child stderr: driver exceptions can contain
            # connection details even when SQLAlchemy usually redacts them.
            child_output = completed.stdout.strip()
            sampler_result = None
            if completed.returncode == 0:
                try:
                    parsed = json.loads(child_output)
                    sampler_result = _validated_sampler_result(
                        args.mode,
                        parsed,
                        scheduled_epoch=base_status["scheduled_epoch"],
                    )
                except (json.JSONDecodeError, TypeError, LauncherError):
                    completed = subprocess.CompletedProcess(
                        completed.args,
                        65,
                        completed.stdout,
                        completed.stderr,
                    )
            result = {
                **base_status,
                "exit_code": completed.returncode,
                "finished_at": _utc_now_text(),
                "status": "completed" if completed.returncode == 0 else "sampler_failed_gap",
            }
            if sampler_result is not None:
                result["sampler_result"] = sampler_result
            _write_status(state_dir, result)
            print(json.dumps(result, sort_keys=True), file=log)
            print(json.dumps(result, sort_keys=True))
            return completed.returncode
        except subprocess.TimeoutExpired:
            result = {
                **base_status,
                "exit_code": 124,
                "finished_at": _utc_now_text(),
                "status": "sampler_timeout_gap",
            }
            _write_status(state_dir, result)
            print(json.dumps(result, sort_keys=True), file=log)
            print(json.dumps(result, sort_keys=True))
            return 124
        except Exception as exc:
            result = {
                **base_status,
                "error": str(exc),
                "exit_code": 78,
                "finished_at": _utc_now_text(),
                "status": "launcher_validation_failed",
            }
            _write_status(state_dir, result)
            print(json.dumps(result, sort_keys=True), file=log)
            print(json.dumps(result, sort_keys=True), file=sys.stderr)
            return 78


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        required=True,
        choices=(
            "baseline",
            "regular",
            "terminal",
            "status",
            "verify-freshness",
            "verify-terminal",
        ),
    )
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--lock-file", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
