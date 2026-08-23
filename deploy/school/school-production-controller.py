#!/usr/bin/env python3
"""Poll and activate the reviewed UniKorn school-production release manifest."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request


BACKEND_URL = "https://github.com/HKUSTGZ-Campus-Forum/back-end.git"
FRONTEND_URL = "https://github.com/HKUSTGZ-Campus-Forum/front-end.git"
STATUS_URL = "https://api.github.com/repos/HKUSTGZ-Campus-Forum/back-end/commits/{sha}/status"
STATUS_CONTEXT = "school-production/validated"
CONTROL_BRANCH = "school-production"
MANIFEST_PATH = "deploy/school/school-production-release.json"
STATE_ROOT = Path("/var/lib/unikorn-school-deploy")
APP_ROOT = Path("/srv/unikorn")
TRUSTED_DEPLOY = Path("/usr/local/libexec/unikorn-school-deploy-release")
TRUSTED_VERIFY = Path("/usr/local/libexec/unikorn-school-verify-local")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SENSITIVE_PREFIXES = ("migrations/", "app/data/")
SISN_TIMERS = (
    "unikorn-sisn-push-production.timer",
    "unikorn-sisn-push.timer",
)
SISN_SERVICES = (
    "unikorn-sisn-push-production.service",
    "unikorn-sisn-push.service",
)


class ReleaseBlocked(RuntimeError):
    """The requested release violates a permanent safety invariant."""


class ReleaseWaiting(RuntimeError):
    """The requested release is valid but not ready to activate yet."""


def run(
    arguments: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C.UTF-8",
        }
    )
    return subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        check=check,
        text=True,
        capture_output=capture,
    )


def git(repository: Path, *arguments: str, capture: bool = True) -> str:
    result = run(["git", "-C", str(repository), *arguments], capture=capture)
    return result.stdout.strip() if capture else ""


def parse_manifest_text(payload: str) -> dict[str, object]:
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ReleaseBlocked(f"release manifest is not valid JSON: {error.msg}") from error
    if not isinstance(manifest, dict):
        raise ReleaseBlocked("release manifest must be a JSON object")
    expected = {"schema_version", "backend_sha", "frontend_sha", "database_change"}
    if set(manifest) != expected:
        raise ReleaseBlocked("release manifest fields are not exact")
    if manifest["schema_version"] != 1:
        raise ReleaseBlocked("unsupported release manifest schema")
    for field in ("backend_sha", "frontend_sha"):
        if not isinstance(manifest[field], str) or not SHA_PATTERN.fullmatch(manifest[field]):
            raise ReleaseBlocked(f"{field} must be a lowercase full commit SHA")
    database_change = manifest["database_change"]
    if not isinstance(database_change, dict) or set(database_change) != {
        "approved",
        "approval_reference",
    }:
        raise ReleaseBlocked("database_change fields are not exact")
    approved = database_change["approved"]
    reference = database_change["approval_reference"]
    if not isinstance(approved, bool):
        raise ReleaseBlocked("database_change.approved must be boolean")
    if approved:
        if not (
            isinstance(reference, str)
            and 3 <= len(reference) <= 200
            and reference == reference.strip()
            and all(character.isprintable() for character in reference)
        ):
            raise ReleaseBlocked("an approved database change needs a concise approval reference")
    elif reference is not None:
        raise ReleaseBlocked("an unapproved database change must have a null approval reference")
    return manifest


def is_sensitive_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in SENSITIVE_PREFIXES)


def ensure_bare_repository(path: Path, url: str) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise ReleaseBlocked(f"unsafe Git state path: {path}")
    else:
        path.mkdir(mode=0o700, parents=True)
        git(path, "init", "--bare")
    remotes = git(path, "remote").splitlines()
    if "origin" in remotes:
        git(path, "remote", "set-url", "origin", url)
    else:
        git(path, "remote", "add", "origin", url)
    if git(path, "remote", "get-url", "origin") != url:
        raise ReleaseBlocked(f"unexpected Git remote for {path.name}")


def fetch_repository(repository: Path, *refspecs: str) -> None:
    try:
        git(
            repository,
            "fetch",
            "--force",
            "--prune",
            "--no-tags",
            "origin",
            *refspecs,
            capture=False,
        )
    except subprocess.CalledProcessError as error:
        raise ReleaseWaiting(
            f"GitHub fetch for {repository.name} is temporarily unavailable"
        ) from error


def fetch_backend_repository(backend: Path) -> None:
    fetch_repository(
        backend,
        "+refs/heads/main:refs/remotes/origin/main",
        f"+refs/heads/{CONTROL_BRANCH}:refs/remotes/origin/{CONTROL_BRANCH}",
    )


def fetch_frontend_repository(frontend: Path) -> None:
    fetch_repository(
        frontend,
        "+refs/heads/main:refs/remotes/origin/main",
    )


def verify_control_branch(backend: Path, control_sha: str) -> None:
    parents = git(backend, "show", "-s", "--format=%P", control_sha).split()
    if len(parents) != 1:
        raise ReleaseBlocked("the control commit must have exactly one parent")
    merge_base = git(backend, "merge-base", control_sha, "refs/remotes/origin/main")
    changed = git(backend, "diff", "--name-only", merge_base, control_sha).splitlines()
    if not changed or set(changed) != {MANIFEST_PATH}:
        raise ReleaseBlocked("school-production may change only the release manifest")


def verify_ancestor(repository: Path, older: str, newer_ref: str, description: str) -> None:
    result = run(
        ["git", "-C", str(repository), "merge-base", "--is-ancestor", older, newer_ref],
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseBlocked(f"{description} is not reachable from main")


def requested_status(control_sha: str) -> str | None:
    request = urllib.request.Request(
        STATUS_URL.format(sha=control_sha),
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "unikorn-school-production-controller/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ReleaseWaiting(f"GitHub validation status is temporarily unavailable: {error}") from error
    statuses = payload.get("statuses") if isinstance(payload, dict) else None
    if not isinstance(statuses, list):
        raise ReleaseWaiting("GitHub validation status response is incomplete")
    for status in statuses:
        if isinstance(status, dict) and status.get("context") == STATUS_CONTEXT:
            state = status.get("state")
            return state if isinstance(state, str) else None
    return None


def current_release() -> dict[str, object] | None:
    path = APP_ROOT / "current" / "release.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        raise ReleaseBlocked("current release metadata is invalid") from error
    if not isinstance(payload, dict):
        raise ReleaseBlocked("current release metadata must be an object")
    for field in ("backend_sha", "frontend_sha"):
        if not isinstance(payload.get(field), str) or not SHA_PATTERN.fullmatch(payload[field]):
            raise ReleaseBlocked(f"current release has an invalid {field}")
    return payload


def changed_paths(repository: Path, old_sha: str, new_sha: str) -> list[str]:
    if old_sha == new_sha:
        return []
    return git(repository, "diff", "--name-only", old_sha, new_sha).splitlines()


def validate_transition(
    backend: Path,
    frontend: Path,
    manifest: dict[str, object],
    current: dict[str, object] | None,
) -> list[str]:
    backend_sha = str(manifest["backend_sha"])
    frontend_sha = str(manifest["frontend_sha"])
    verify_ancestor(backend, backend_sha, "refs/remotes/origin/main", "backend SHA")
    verify_ancestor(frontend, frontend_sha, "refs/remotes/origin/main", "frontend SHA")
    sensitive: list[str] = []
    if current is not None:
        current_backend = str(current["backend_sha"])
        current_frontend = str(current["frontend_sha"])
        verify_ancestor(backend, current_backend, backend_sha, "backend transition")
        verify_ancestor(frontend, current_frontend, frontend_sha, "frontend transition")
        sensitive = [
            path
            for path in changed_paths(backend, current_backend, backend_sha)
            if is_sensitive_path(path)
        ]
    database_change = manifest["database_change"]
    assert isinstance(database_change, dict)
    approved = database_change["approved"] is True
    if sensitive and not approved:
        raise ReleaseBlocked(
            "the release changes migrations or product data without recorded approval: "
            + ", ".join(sensitive[:8])
        )
    if approved and not sensitive:
        raise ReleaseBlocked("database-change approval is set but this transition has no sensitive files")
    return sensitive


def checkout_candidate(repository: Path, destination: Path, sha: str) -> None:
    run(["git", "clone", "--no-checkout", "--shared", str(repository), str(destination)])
    git(destination, "checkout", "--detach", sha, capture=False)
    if git(destination, "rev-parse", "HEAD") != sha:
        raise ReleaseBlocked(f"candidate checkout did not reach {sha}")
    if git(destination, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ReleaseBlocked(f"candidate checkout is dirty: {destination.name}")


def active_units(units: tuple[str, ...]) -> list[str]:
    return [
        unit
        for unit in units
        if run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0
    ]


def pause_sisn_timers() -> list[str]:
    active = active_units(SISN_TIMERS)
    if active:
        run(["systemctl", "stop", *active])
    deadline = time.monotonic() + 180
    while active_units(SISN_SERVICES):
        if time.monotonic() >= deadline:
            raise ReleaseWaiting("an SISN synchronization service did not quiesce")
        time.sleep(2)
    return active


def resume_sisn_timers(units: list[str]) -> None:
    if units:
        run(["systemctl", "start", *units], check=False)


def verify_activated_release(frontend_sha: str) -> None:
    run([str(TRUSTED_VERIFY), "--require-oidc"])
    checks = (
        ("https://unikorn.hkust-gz.edu.cn/health", "frontend"),
        ("https://unikorn.hkust-gz.edu.cn/api/healthz", "backend"),
        ("https://unikorn.hkust-gz.edu.cn/api/auth/oidc/status", "oidc"),
    )
    for url, kind in checks:
        response = run(
            ["curl", "--fail", "--silent", "--show-error", "--max-time", "15", url],
            capture=True,
        ).stdout
        try:
            payload = json.loads(response)
        except json.JSONDecodeError as error:
            raise ReleaseWaiting(f"public {kind} check returned invalid JSON") from error
        if kind == "frontend" and not (
            payload.get("status") == "ok" and payload.get("version") == frontend_sha
        ):
            raise ReleaseWaiting("public frontend health does not identify the requested SHA")
        if kind == "backend" and payload.get("status") != "ok":
            raise ReleaseWaiting("public backend health is not ok")
        if kind == "oidc" and not (
            payload.get("enabled") is True
            and payload.get("flow") == "authorization_code_pkce"
            and payload.get("provider") == "HKUST(GZ)"
        ):
            raise ReleaseWaiting("public OIDC status is not production-ready")


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def deploy_if_ready() -> None:
    if os.geteuid() != 0:
        raise ReleaseBlocked("the production controller must run as root")
    STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE_ROOT.is_symlink() or not STATE_ROOT.is_dir():
        raise ReleaseBlocked("production controller state root is unsafe")
    os.chmod(STATE_ROOT, 0o700)
    lock_path = STATE_ROOT / "controller.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ReleaseWaiting("another production controller run is active") from error

        backend = STATE_ROOT / "backend.git"
        ensure_bare_repository(backend, BACKEND_URL)
        fetch_backend_repository(backend)
        control_sha = git(backend, "rev-parse", f"refs/remotes/origin/{CONTROL_BRANCH}")
        success_path = STATE_ROOT / "last-success.json"
        if success_path.exists():
            success = json.loads(success_path.read_text(encoding="utf-8"))
            if success.get("control_sha") == control_sha:
                print(f"school production manifest {control_sha} is already active")
                return

        frontend = STATE_ROOT / "frontend.git"
        ensure_bare_repository(frontend, FRONTEND_URL)
        fetch_frontend_repository(frontend)
        verify_control_branch(backend, control_sha)
        manifest_text = git(backend, "show", f"{control_sha}:{MANIFEST_PATH}")
        manifest = parse_manifest_text(manifest_text)
        current = current_release()
        validate_transition(backend, frontend, manifest, current)
        status = requested_status(control_sha)
        if status != "success":
            raise ReleaseWaiting(
                f"GitHub validation {STATUS_CONTEXT} is {status or 'not published'}"
            )

        backend_sha = str(manifest["backend_sha"])
        frontend_sha = str(manifest["frontend_sha"])
        already_active = current is not None and (
            current["backend_sha"] == backend_sha and current["frontend_sha"] == frontend_sha
        )
        if not already_active:
            candidate = STATE_ROOT / "candidates" / control_sha
            if candidate.exists():
                if candidate.is_symlink() or STATE_ROOT not in candidate.parents:
                    raise ReleaseBlocked("unsafe candidate cleanup path")
                shutil.rmtree(candidate)
            candidate.mkdir(mode=0o700, parents=True)
            checkout_candidate(backend, candidate / "backend", backend_sha)
            checkout_candidate(frontend, candidate / "frontend", frontend_sha)
            active_timers = pause_sisn_timers()
            try:
                run(
                    [
                        str(TRUSTED_DEPLOY),
                        "--backend-source",
                        str(candidate / "backend"),
                        "--frontend-source",
                        str(candidate / "frontend"),
                        "--backend-sha",
                        backend_sha,
                        "--frontend-sha",
                        frontend_sha,
                        "--activate",
                    ]
                )
            finally:
                resume_sisn_timers(active_timers)
            shutil.rmtree(candidate)

        verify_activated_release(frontend_sha)
        atomic_json(
            success_path,
            {
                "activated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "backend_sha": backend_sha,
                "control_sha": control_sha,
                "frontend_sha": frontend_sha,
            },
        )
        print(
            "school production release verified: "
            f"control={control_sha} backend={backend_sha} frontend={frontend_sha}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-file", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.validate_file is not None:
            parse_manifest_text(arguments.validate_file.read_text(encoding="utf-8"))
            print(f"valid school production manifest: {arguments.validate_file}")
            return 0
        deploy_if_ready()
        return 0
    except ReleaseWaiting as error:
        print(f"school production deployment waiting: {error}")
        return 0
    except (ReleaseBlocked, subprocess.CalledProcessError, OSError, ValueError) as error:
        print(f"school production deployment blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
