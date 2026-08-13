#!/usr/bin/env python3
"""Audit and recoverably quarantine one known deployment migration set.

This helper is intentionally stdlib-only so a reviewed copy can be streamed to
the target host without importing or executing any migration module.
"""

from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any


APP_DIR = Path("/data/dev_unikorn/back-end")
QUARANTINE_ROOT = Path("/data/dev_unikorn/quarantine/legacy-migrations")
LOCK_PATH = Path("/data/dev_unikorn/backend-mutations-dev.lock")
EXPECTED_BRANCH = "main"
EXPECTED_DATABASE = "dev_unikorn"
APPLY_CONFIRMATION = "QUARANTINE_DEV_LEGACY_MIGRATIONS"
TARGET_NAME = "dev"
MAX_FILE_BYTES = 1_048_576
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID_RE = re.compile(r"[1-9][0-9]{0,19}\Z")
RUN_DIRECTORY_RE = re.compile(r"run-([1-9][0-9]{0,19})\Z")
MANIFEST_MAX_BYTES = 1_048_576

DEV_ALLOWLIST = (
    "migrations/versions/0e18af78068e_.py",
    "migrations/versions/1effc88ae61e_.py",
    "migrations/versions/6734a89a7bb7_.py",
    "migrations/versions/67c45f677a8a_.py",
    "migrations/versions/6fd25dd56cc7_.py",
    "migrations/versions/70dd1b7c30df_.py",
    "migrations/versions/73e858c1a76c_.py",
    "migrations/versions/8accb2f129c8_.py",
    "migrations/versions/c93a7d7db52a_initial20250821.py",
    "migrations/versions/ca9460bf287f_.py",
    "migrations/versions/d79de51fc5f3_.py",
    "migrations/versions/da5f7cad7d38_.py",
)
PRODUCTION_ALLOWLIST = (
    "migrations/versions/000000000000_create_oauth_tables.py",
)
TARGETS = {
    "dev": {
        "app_dir": Path("/data/dev_unikorn/back-end"),
        "quarantine_root": Path("/data/dev_unikorn/quarantine/legacy-migrations"),
        "lock_path": Path("/data/dev_unikorn/backend-mutations-dev.lock"),
        "branch": "main",
        "database": "dev_unikorn",
        "confirmation": "QUARANTINE_DEV_LEGACY_MIGRATIONS",
        "allowlist": DEV_ALLOWLIST,
    },
    "production": {
        "app_dir": Path("/data/prod_unikorn/back-end"),
        "quarantine_root": Path(
            "/data/prod_unikorn/back-end/.git/unikorn-operations/"
            "quarantine/legacy-migrations"
        ),
        "lock_path": Path(
            "/data/prod_unikorn/back-end/.git/unikorn-operations/"
            "backend-mutations.lock"
        ),
        "branch": "production",
        "database": "prod_unikorn",
        "confirmation": "QUARANTINE_PRODUCTION_LEGACY_OAUTH_MIGRATION",
        "allowlist": PRODUCTION_ALLOWLIST,
    },
}
ALLOWLIST = DEV_ALLOWLIST
ALLOWLIST_SET = frozenset(ALLOWLIST)
FAILURE_INJECTOR = None


def configure_target(target: str) -> None:
    """Select one reviewed target before opening any host path or database."""

    global APP_DIR, QUARANTINE_ROOT, LOCK_PATH
    global EXPECTED_BRANCH, EXPECTED_DATABASE, APPLY_CONFIRMATION, TARGET_NAME
    global ALLOWLIST, ALLOWLIST_SET

    try:
        config = TARGETS[target]
    except KeyError as error:
        raise ReconciliationBlocked("unknown reconciliation target") from error
    TARGET_NAME = target
    APP_DIR = config["app_dir"]
    QUARANTINE_ROOT = config["quarantine_root"]
    LOCK_PATH = config["lock_path"]
    EXPECTED_BRANCH = config["branch"]
    EXPECTED_DATABASE = config["database"]
    APPLY_CONFIRMATION = config["confirmation"]
    ALLOWLIST = config["allowlist"]
    ALLOWLIST_SET = frozenset(ALLOWLIST)


class ReconciliationBlocked(RuntimeError):
    """The checkout does not exactly match the reviewed recovery boundary."""


class ManualRecoveryRequired(ReconciliationBlocked):
    """A durable transaction was retained and needs operator inspection."""


def _failure_point(name: str, context: dict[str, Any] | None = None) -> None:
    if FAILURE_INJECTOR is not None:
        FAILURE_INJECTOR(name, context or {})


def _fsync_directory(descriptor: int) -> None:
    os.fsync(descriptor)


def _open_directory(path: Path) -> int:
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)


def _validate_fixed_parent_descriptor(descriptor: int, label: str) -> tuple[int, int]:
    details = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid not in {0, os.geteuid()}
        or details.st_mode & 0o022
    ):
        raise ReconciliationBlocked(
            "unsafe parent directory: "
            f"{label} (owner_uid={details.st_uid}, effective_uid={os.geteuid()}, "
            f"group_gid={details.st_gid}, effective_gid={os.getegid()}, "
            f"mode={stat.S_IMODE(details.st_mode):04o})"
        )
    return details.st_dev, details.st_ino


def _open_lock_parent() -> int:
    if TARGET_NAME == "dev":
        if LOCK_PATH.parent != APP_DIR.parent:
            raise ReconciliationBlocked(
                "dev lock path is outside the fixed private data directory"
            )
        parent_fd = _open_directory(APP_DIR.parent)
        try:
            _validate_fixed_parent_descriptor(parent_fd, str(APP_DIR.parent))
        except Exception:
            os.close(parent_fd)
            raise
        return parent_fd

    expected_operations_root = APP_DIR / ".git" / "unikorn-operations"
    if LOCK_PATH.parent != expected_operations_root:
        raise ReconciliationBlocked("production lock path is outside the fixed Git ops root")
    if _git(APP_DIR, "rev-parse", "--absolute-git-dir") != str(APP_DIR / ".git"):
        raise ReconciliationBlocked("production checkout does not use the fixed real Git directory")
    if (
        _git(APP_DIR, "rev-parse", "--path-format=absolute", "--git-common-dir")
        != str(APP_DIR / ".git")
    ):
        raise ReconciliationBlocked("production checkout uses an alternate Git common directory")
    app_fd = _open_directory(APP_DIR)
    try:
        git_fd = os.open(
            ".git",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=app_fd,
        )
    except OSError as error:
        os.close(app_fd)
        raise ReconciliationBlocked("cannot safely open the production Git directory") from error
    os.close(app_fd)
    git_details = os.fstat(git_fd)
    if (
        not stat.S_ISDIR(git_details.st_mode)
        or git_details.st_uid != os.geteuid()
        or git_details.st_mode & 0o022
    ):
        os.close(git_fd)
        raise ReconciliationBlocked("production Git directory has unsafe metadata")
    source_parent_fd, _source_parent_identity = _open_source_parent(APP_DIR)
    source_parent_details = os.fstat(source_parent_fd)
    os.close(source_parent_fd)
    if source_parent_details.st_dev != git_details.st_dev:
        os.close(git_fd)
        raise ReconciliationBlocked(
            "production Git directory and migrations must share a filesystem"
        )
    try:
        operations_fd = _ensure_owned_directory(git_fd, "unikorn-operations", 0o700)
    except Exception:
        os.close(git_fd)
        raise
    os.close(git_fd)
    operations_details = os.fstat(operations_fd)
    if (
        operations_details.st_dev != git_details.st_dev
        or stat.S_IMODE(operations_details.st_mode) != 0o700
    ):
        os.close(operations_fd)
        raise ReconciliationBlocked(
            "production Git ops root must share the Git filesystem and have mode 0700"
        )
    return operations_fd


def _validated_inherited_lock() -> int | None:
    raw_descriptor = os.environ.get("UNIKORN_BACKEND_MUTATION_LOCK_FD", "")
    if not raw_descriptor:
        return None
    if not raw_descriptor.isdecimal():
        raise ReconciliationBlocked("inherited mutation lock descriptor is invalid")
    descriptor = int(raw_descriptor)
    try:
        details = os.fstat(descriptor)
    except OSError as error:
        raise ReconciliationBlocked("inherited mutation lock is not open") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_nlink != 1
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_size != 0
        or os.environ.get("UNIKORN_BACKEND_MUTATION_LOCK_DEV_INO")
        != f"{details.st_dev}:{details.st_ino}"
    ):
        raise ReconciliationBlocked("inherited mutation lock has unsafe metadata")
    return descriptor


def _acquire_lock() -> int:
    inherited = _validated_inherited_lock()
    if inherited is not None:
        try:
            fcntl.flock(inherited, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ReconciliationBlocked(
                "another backend mutation holds the inherited lock"
            ) from error
        return inherited
    parent_fd = _open_lock_parent()
    try:
        descriptor = os.open(
            LOCK_PATH.name,
            os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
    except OSError as error:
        os.close(parent_fd)
        raise ReconciliationBlocked("cannot safely open the fixed mutation lock") from error
    _fsync_directory(parent_fd)
    details = os.fstat(descriptor)
    try:
        bound = os.stat(LOCK_PATH.name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        os.close(descriptor)
        os.close(parent_fd)
        raise ReconciliationBlocked("cannot revalidate fixed mutation lock") from error
    os.close(parent_fd)
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_nlink != 1
        or stat.S_IMODE(details.st_mode) != 0o600
        or not stat.S_ISREG(bound.st_mode)
        or (bound.st_dev, bound.st_ino) != (details.st_dev, details.st_ino)
    ):
        os.close(descriptor)
        raise ReconciliationBlocked("fixed mutation lock has unsafe metadata")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise ReconciliationBlocked("another backend mutation holds the lock") from error
    return descriptor


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        raise ReconciliationBlocked(f"git command failed: {' '.join(arguments)}")
    return result.stdout.rstrip("\n")


def _path_from_status(line: str) -> str:
    if len(line) < 4 or line[2] != " ":
        raise ReconciliationBlocked("unexpected git status format")
    path = line[3:]
    if " -> " in path or path.startswith('"'):
        raise ReconciliationBlocked("renamed or quoted git status paths are not allowed")
    return path


def _exact_untracked_allowlist(repo: Path) -> None:
    status = _git(
        repo,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--no-renames",
    )
    lines = status.splitlines() if status else []
    if any(not line.startswith("?? ") for line in lines):
        raise ReconciliationBlocked("checkout has tracked, staged, or unexpected dirty state")
    paths = {_path_from_status(line) for line in lines}
    if paths != ALLOWLIST_SET or len(lines) != len(ALLOWLIST):
        raise ReconciliationBlocked("untracked file set does not match the exact allowlist")


def _literal_assignment(tree: ast.Module, name: str, filename: str) -> Any:
    values = []
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
        if value is None or not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        try:
            values.append(ast.literal_eval(value))
        except (ValueError, TypeError) as error:
            raise ReconciliationBlocked(
                f"{filename}: {name} must be an AST literal"
            ) from error
    if len(values) != 1:
        raise ReconciliationBlocked(f"{filename}: expected one literal {name} assignment")
    return values[0]


def _validate_revision(value: Any, filename: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", value):
        raise ReconciliationBlocked(f"{filename}: invalid revision metadata")
    return value


def _validate_down_revision(value: Any, filename: str) -> str | list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _validate_revision(value, filename)
    if isinstance(value, (tuple, list)) and value:
        return [_validate_revision(parent, filename) for parent in value]
    raise ReconciliationBlocked(f"{filename}: invalid down_revision metadata")


def _read_bound_file(
    parent_fd: int,
    name: str,
    relative_path: str,
    *,
    expected: dict[str, Any] | None = None,
    allowed_link_counts: frozenset[int] = frozenset({1}),
    sync_contents: bool = False,
) -> tuple[dict[str, Any], tuple[int, int]]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except FileNotFoundError as error:
        raise ReconciliationBlocked(f"missing allowlisted file: {relative_path}") from error
    except OSError as error:
        raise ReconciliationBlocked(f"allowlisted path is not a regular file: {relative_path}")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReconciliationBlocked(
                f"allowlisted path is not a regular file: {relative_path}"
            )
        if TARGET_NAME == "production" and before.st_mode & 0o022:
            raise ReconciliationBlocked(
                "production allowlisted file has unsafe metadata: "
                f"{relative_path} (uid={before.st_uid}, gid={before.st_gid}, "
                f"mode={stat.S_IMODE(before.st_mode):04o})"
            )
        if before.st_nlink not in allowed_link_counts:
            raise ReconciliationBlocked(
                f"allowlisted file has an unexpected hard-link count: {relative_path}"
            )
        if before.st_size > MAX_FILE_BYTES:
            raise ReconciliationBlocked(f"allowlisted file is oversized: {relative_path}")
        with os.fdopen(descriptor, "rb", closefd=False) as source_file:
            payload = source_file.read(MAX_FILE_BYTES + 1)
        if sync_contents:
            os.fsync(descriptor)
        after = os.fstat(descriptor)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before != identity_after or len(payload) != before.st_size:
            raise ReconciliationBlocked(f"file changed while auditing: {relative_path}")
        source = payload.decode("utf-8")
        tree = ast.parse(source, filename=relative_path)
    except (OSError, UnicodeDecodeError, SyntaxError) as error:
        raise ReconciliationBlocked(f"cannot safely parse {relative_path}") from error
    finally:
        os.close(descriptor)
    result = {
        "path": relative_path,
        "size": before.st_size,
        "mode": stat.S_IMODE(before.st_mode),
        "uid": before.st_uid,
        "gid": before.st_gid,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "revision": _validate_revision(
            _literal_assignment(tree, "revision", relative_path), relative_path
        ),
        "down_revision": _validate_down_revision(
            _literal_assignment(tree, "down_revision", relative_path), relative_path
        ),
    }
    if expected is not None and result != expected:
        raise ReconciliationBlocked(f"file changed before move: {relative_path}")
    return result, (before.st_dev, before.st_ino)


def _open_source_parent(repo: Path) -> tuple[int, tuple[int, int]]:
    migrations_fd = _open_directory(repo / "migrations")
    try:
        versions_fd = os.open(
            "versions",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=migrations_fd,
        )
    finally:
        os.close(migrations_fd)
    details = os.fstat(versions_fd)
    return versions_fd, (details.st_dev, details.st_ino)


def _inspect_file(path: Path, relative_path: str) -> dict[str, Any]:
    parent_fd = _open_directory(path.parent)
    try:
        result, _identity = _read_bound_file(
            parent_fd, path.name, relative_path
        )
        return result
    finally:
        os.close(parent_fd)


def aggregate_digest(context: dict[str, Any]) -> str:
    canonical = json.dumps(
        context,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _optional_literal_assignment(tree: ast.Module, name: str, filename: str) -> Any:
    assignments = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == name
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    ]
    if not assignments:
        return None
    return _literal_assignment(tree, name, filename)


def _metadata_for_source(
    source: str, filename: str
) -> tuple[str, list[str], list[str]]:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as error:
        raise ReconciliationBlocked(f"cannot safely parse committed migration {filename}") from error
    revision = _validate_revision(_literal_assignment(tree, "revision", filename), filename)
    down_revision = _validate_down_revision(
        _literal_assignment(tree, "down_revision", filename), filename
    )
    if down_revision is None:
        parents: list[str] = []
    elif isinstance(down_revision, str):
        parents = [down_revision]
    else:
        parents = down_revision
    depends_on = _validate_down_revision(
        _optional_literal_assignment(tree, "depends_on", filename), filename
    )
    if depends_on is None:
        dependencies: list[str] = []
    elif isinstance(depends_on, str):
        dependencies = [depends_on]
    else:
        dependencies = depends_on
    return revision, parents, dependencies


def committed_graph(repo: Path, allowlisted_revisions: set[str]) -> dict[str, Any]:
    names = _git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", "migrations/versions")
    migration_paths = sorted(
        path for path in names.splitlines() if path.endswith(".py")
    )
    revisions: dict[str, list[str]] = {}
    dependencies: dict[str, list[str]] = {}
    for path in migration_paths:
        revision, parents, revision_dependencies = _metadata_for_source(
            _git(repo, "show", f"HEAD:{path}"), path
        )
        if revision in revisions:
            raise ReconciliationBlocked("committed graph has duplicate revision identifiers")
        revisions[revision] = parents
        dependencies[revision] = revision_dependencies
    all_parents = {parent for parents in revisions.values() for parent in parents}
    all_dependencies = {
        dependency
        for revision_dependencies in dependencies.values()
        for dependency in revision_dependencies
    }
    references = sorted(allowlisted_revisions & (all_parents | all_dependencies))
    return {
        "revisions": sorted(revisions),
        "heads": sorted(set(revisions) - all_parents),
        "allowlisted_revision_references": references,
        "allowlisted_revision_referenced": bool(references),
    }


def _live_database_revisions(repo: Path) -> list[str]:
    probe = f"""
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app.config import Config
url = make_url(Config.SQLALCHEMY_DATABASE_URI)
if url.get_backend_name() != 'postgresql' or url.database != {EXPECTED_DATABASE!r}:
    raise SystemExit(23)
engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, **Config.SQLALCHEMY_ENGINE_OPTIONS)
with engine.connect() as connection:
    if connection.execute(text('SELECT current_database()')).scalar_one() != {EXPECTED_DATABASE!r}:
        raise SystemExit(24)
    revisions = connection.execute(
        text('SELECT version_num FROM alembic_version ORDER BY version_num')
    ).scalars().all()
for revision in revisions:
    if not isinstance(revision, str) or not revision.replace('_', '').isalnum():
        raise SystemExit(25)
    print(revision)
"""
    environment = os.environ.copy()
    environment.update(
        {
            "AUTO_INIT_ON_STARTUP": "false",
            "ENABLE_BACKGROUND_TASKS": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    result = subprocess.run(
        [str(repo / "venv" / "bin" / "python"), "-c", probe],
        cwd=repo,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise ReconciliationBlocked("read-only Alembic revision probe failed")
    revisions = result.stdout.splitlines()
    if not revisions:
        raise ReconciliationBlocked("Alembic version table returned no current revisions")
    for revision in revisions:
        _validate_revision(revision, f"{TARGET_NAME} alembic_version")
    return sorted(set(revisions))


def audit(repo: Path, *, live_revisions: list[str] | None = None) -> dict[str, Any]:
    if repo != APP_DIR or repo.is_symlink() or repo.resolve() != APP_DIR:
        raise ReconciliationBlocked("repository path is not the fixed target checkout")
    if not (repo / ".git").exists():
        raise ReconciliationBlocked("fixed target checkout is not a git worktree")
    if _git(repo, "rev-parse", "--show-toplevel") != str(repo.resolve()):
        raise ReconciliationBlocked("git toplevel does not match the fixed target checkout")
    if _git(repo, "symbolic-ref", "--short", "HEAD") != EXPECTED_BRANCH:
        raise ReconciliationBlocked(
            f"fixed target checkout is not on {EXPECTED_BRANCH}"
        )
    _exact_untracked_allowlist(repo)
    files = [_inspect_file(repo / path, path) for path in ALLOWLIST]
    revisions = [entry["revision"] for entry in files]
    if len(set(revisions)) != len(revisions):
        raise ReconciliationBlocked("duplicate migration revision identifiers detected")
    graph = committed_graph(repo, set(revisions))
    current_revisions = (
        _live_database_revisions(repo) if live_revisions is None else live_revisions
    )
    current_revisions = sorted(set(current_revisions))
    for revision in current_revisions:
        _validate_revision(revision, f"{TARGET_NAME} alembic_version")
    current_allowlisted = sorted(set(revisions) & set(current_revisions))
    committed_duplicates = sorted(set(revisions) & set(graph["revisions"]))
    current_unknown = sorted(
        set(current_revisions) - set(graph["revisions"]) - set(revisions)
    )
    context = {
        "schema_version": 1,
        "target": TARGET_NAME,
        "repository": str(APP_DIR),
        "branch": EXPECTED_BRANCH,
        "repository_sha": _git(repo, "rev-parse", "HEAD"),
        "database": EXPECTED_DATABASE,
        "live_current_revisions": current_revisions,
        "live_current_allowlisted_revisions": current_allowlisted,
        "live_current_unknown_revisions": current_unknown,
        "committed_revisions": graph["revisions"],
        "committed_heads": graph["heads"],
        "committed_allowlisted_revision_duplicates": committed_duplicates,
        "committed_allowlisted_revision_references": graph[
            "allowlisted_revision_references"
        ],
        "committed_allowlisted_revision_referenced": graph[
            "allowlisted_revision_referenced"
        ],
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "files": files,
    }
    return {**context, "aggregate_sha256": aggregate_digest(context)}


def _validate_quarantine_target(path: Path, run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ReconciliationBlocked("workflow run id is invalid")
    expected = QUARANTINE_ROOT / f"run-{run_id}"
    if path != expected:
        raise ReconciliationBlocked("quarantine path does not match the fixed root/run id")
    if path.exists() or path.is_symlink():
        raise ReconciliationBlocked("quarantine destination already exists")
    return path


def _ensure_owned_directory(parent_fd: int, name: str, mode: int) -> int:
    try:
        os.mkdir(name, mode=mode, dir_fd=parent_fd)
        _fsync_directory(parent_fd)
    except FileExistsError:
        pass
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=parent_fd,
    )
    details = os.fstat(descriptor)
    if (
        details.st_uid != os.geteuid()
        or (
            stat.S_IMODE(details.st_mode) != mode
            if TARGET_NAME == "production"
            else bool(details.st_mode & 0o022)
        )
        or not stat.S_ISDIR(details.st_mode)
    ):
        os.close(descriptor)
        raise ReconciliationBlocked(f"unsafe private directory: {name}")
    return descriptor


def _write_durable_file(directory_fd: int, name: str, payload: bytes, mode: int) -> str:
    temporary_name = f".{name}.tmp-{os.getpid()}"
    descriptor = os.open(
        temporary_name,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        mode,
        dir_fd=directory_fd,
    )
    renamed = False
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.rename(
            temporary_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        renamed = True
    finally:
        if not renamed:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
                _fsync_directory(directory_fd)
            except FileNotFoundError:
                pass
    _fsync_directory(directory_fd)
    return hashlib.sha256(payload).hexdigest()


def _read_private_file(
    parent_fd: int,
    name: str,
    mode: int,
    *,
    max_bytes: int = MANIFEST_MAX_BYTES,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
) -> bytes:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except (OSError, ValueError) as error:
        raise ReconciliationBlocked(f"cannot safely open transaction file: {name}") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != (os.geteuid() if expected_uid is None else expected_uid)
            or (
                expected_gid is not None
                and before.st_gid != expected_gid
            )
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != mode
            or before.st_size > max_bytes
        ):
            raise ReconciliationBlocked(f"unsafe transaction file metadata: {name}")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            payload = source.read(max_bytes + 1)
        after = os.fstat(descriptor)
        if (
            len(payload) != before.st_size
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        ):
            raise ReconciliationBlocked(f"transaction file changed while reading: {name}")
        return payload
    finally:
        os.close(descriptor)


def _manifest_json(payload: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReconciliationBlocked(f"invalid transaction manifest: {name}") from error
    if not isinstance(value, dict):
        raise ReconciliationBlocked(f"transaction manifest is not an object: {name}")
    return value


def _validate_committed_transaction(
    quarantine_root_fd: int,
    transaction_name: str,
) -> dict[str, Any]:
    match = RUN_DIRECTORY_RE.fullmatch(transaction_name)
    if match is None:
        raise ReconciliationBlocked(
            f"unexpected production quarantine entry: {transaction_name}"
        )
    run_id = match.group(1)
    try:
        transaction_fd = os.open(
            transaction_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=quarantine_root_fd,
        )
    except OSError as error:
        raise ReconciliationBlocked(
            f"cannot safely open transaction directory: {transaction_name}"
        ) from error
    try:
        transaction_details = os.fstat(transaction_fd)
        if (
            transaction_details.st_uid != os.geteuid()
            or stat.S_IMODE(transaction_details.st_mode) != 0o700
        ):
            raise ReconciliationBlocked(
                f"unsafe transaction directory metadata: {transaction_name}"
            )
        try:
            entries = sorted(os.listdir(transaction_fd))
        except OSError as error:
            raise ReconciliationBlocked(
                f"cannot enumerate transaction directory: {transaction_name}"
            ) from error
        if entries != ["COMMITTED.json", "PREPARED.json", "files"]:
            raise ReconciliationBlocked(
                f"incomplete or unexpected transaction contents: {transaction_name}"
            )
        prepared_payload = _read_private_file(transaction_fd, "PREPARED.json", 0o400)
        committed_payload = _read_private_file(transaction_fd, "COMMITTED.json", 0o400)
        prepared = _manifest_json(prepared_payload, "PREPARED.json")
        committed = _manifest_json(committed_payload, "COMMITTED.json")
        required_committed = {
            "state",
            "prepared_sha256",
            "aggregate_sha256",
            "file_count",
            "git_clean",
        }
        if set(committed) != required_committed:
            raise ReconciliationBlocked("committed manifest fields are not exact")
        mappings = prepared.get("mappings")
        files = prepared.get("files")
        if (
            prepared.get("state") != "PREPARED"
            or prepared.get("target") != "production"
            or prepared.get("repository") != str(APP_DIR)
            or prepared.get("workflow_run_id") != run_id
            or prepared.get("quarantine")
            != str(QUARANTINE_ROOT / transaction_name)
            or not SHA256_RE.fullmatch(str(prepared.get("aggregate_sha256", "")))
            or not isinstance(mappings, list)
            or not isinstance(files, list)
            or not mappings
            or len(mappings) != len(files)
            or committed.get("state") != "COMMITTED"
            or committed.get("prepared_sha256")
            != hashlib.sha256(prepared_payload).hexdigest()
            or committed.get("aggregate_sha256") != prepared.get("aggregate_sha256")
            or committed.get("file_count") != len(mappings)
            or committed.get("git_clean") is not True
        ):
            raise ReconciliationBlocked(
                f"transaction manifests do not authenticate completion: {transaction_name}"
            )
        try:
            files_fd = os.open(
                "files",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=transaction_fd,
            )
        except OSError as error:
            raise ReconciliationBlocked(
                "cannot safely open transaction files directory"
            ) from error
        try:
            files_details = os.fstat(files_fd)
            if (
                files_details.st_uid != os.geteuid()
                or stat.S_IMODE(files_details.st_mode) != 0o700
            ):
                raise ReconciliationBlocked("unsafe transaction files directory")
            file_records = {
                item.get("path"): item
                for item in files
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            }
            if len(file_records) != len(files):
                raise ReconciliationBlocked("prepared file records are not exact")
            expected_names = []
            for mapping in mappings:
                if not isinstance(mapping, dict) or set(mapping) != {
                    "source",
                    "destination",
                    "sha256",
                    "size",
                    "source_device",
                    "source_inode",
                }:
                    raise ReconciliationBlocked("transaction mapping fields are not exact")
                if (
                    not isinstance(mapping["source_device"], int)
                    or isinstance(mapping["source_device"], bool)
                    or mapping["source_device"] < 0
                    or not isinstance(mapping["source_inode"], int)
                    or isinstance(mapping["source_inode"], bool)
                    or mapping["source_inode"] <= 0
                ):
                    raise ReconciliationBlocked("transaction source identity is invalid")
                source = mapping["source"]
                destination = mapping["destination"]
                if source not in PRODUCTION_ALLOWLIST:
                    raise ReconciliationBlocked("transaction source is outside the production allowlist")
                expected_destination = f"files/{PurePosixPath(source).name}"
                if destination != expected_destination:
                    raise ReconciliationBlocked("transaction destination is not canonical")
                name = PurePosixPath(destination).name
                if not SHA256_RE.fullmatch(str(mapping["sha256"])) or not isinstance(
                    mapping["size"], int
                ) or isinstance(mapping["size"], bool) or mapping["size"] < 0:
                    raise ReconciliationBlocked("transaction mapping digest or size is invalid")
                expected_file = file_records.get(source)
                if (
                    expected_file is None
                    or expected_file.get("sha256") != mapping["sha256"]
                    or expected_file.get("size") != mapping["size"]
                    or not isinstance(expected_file.get("mode"), int)
                    or isinstance(expected_file.get("mode"), bool)
                    or expected_file["mode"] & 0o022
                    or not isinstance(expected_file.get("uid"), int)
                    or isinstance(expected_file.get("uid"), bool)
                    or expected_file["uid"] < 0
                    or not isinstance(expected_file.get("gid"), int)
                    or isinstance(expected_file.get("gid"), bool)
                    or expected_file["gid"] < 0
                ):
                    raise ReconciliationBlocked("prepared file and mapping records disagree")
                payload = _read_private_file(
                    files_fd,
                    name,
                    expected_file["mode"],
                    max_bytes=MAX_FILE_BYTES,
                    expected_uid=expected_file["uid"],
                    expected_gid=expected_file["gid"],
                )
                if len(payload) != mapping["size"] or hashlib.sha256(payload).hexdigest() != mapping["sha256"]:
                    raise ReconciliationBlocked("quarantined file does not match its manifest")
                expected_names.append(name)
            try:
                actual_names = sorted(os.listdir(files_fd))
            except OSError as error:
                raise ReconciliationBlocked(
                    "cannot enumerate transaction files"
                ) from error
            if actual_names != sorted(expected_names):
                raise ReconciliationBlocked("quarantined file set is not exact")
        finally:
            os.close(files_fd)
        return {
            "run_id": run_id,
            "aggregate_sha256": committed["aggregate_sha256"],
            "file_count": committed["file_count"],
        }
    finally:
        os.close(transaction_fd)


def verify_production_transactions() -> dict[str, Any]:
    """Fail closed unless every retained production transaction is fully committed."""

    if TARGET_NAME != "production":
        raise ReconciliationBlocked("transaction verification is production-only")
    operations_root_fd = _open_lock_parent()
    try:
        try:
            quarantine_parent_fd = os.open(
                "quarantine",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=operations_root_fd,
            )
        except FileNotFoundError:
            return {"status": "clean", "transactions": []}
        try:
            parent_details = os.fstat(quarantine_parent_fd)
            if (
                parent_details.st_uid != os.geteuid()
                or stat.S_IMODE(parent_details.st_mode) != 0o700
            ):
                raise ReconciliationBlocked("unsafe production quarantine parent")
            try:
                quarantine_root_fd = os.open(
                    "legacy-migrations",
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=quarantine_parent_fd,
                )
            except OSError as error:
                raise ReconciliationBlocked(
                    "cannot safely open production quarantine root"
                ) from error
        finally:
            os.close(quarantine_parent_fd)
        try:
            root_details = os.fstat(quarantine_root_fd)
            if (
                root_details.st_uid != os.geteuid()
                or stat.S_IMODE(root_details.st_mode) != 0o700
            ):
                raise ReconciliationBlocked("unsafe production quarantine root")
            try:
                transaction_names = sorted(os.listdir(quarantine_root_fd))
            except OSError as error:
                raise ReconciliationBlocked(
                    "cannot enumerate production quarantine root"
                ) from error
            transactions = [
                _validate_committed_transaction(quarantine_root_fd, name)
                for name in transaction_names
            ]
        finally:
            os.close(quarantine_root_fd)
        return {"status": "clean", "transactions": transactions}
    finally:
        os.close(operations_root_fd)


def lock_and_exec_production(command: list[str]) -> None:
    """Acquire the hardened production lock, then replace this process."""

    if TARGET_NAME != "production" or not command or any(not item for item in command):
        raise ReconciliationBlocked("lock-exec requires a nonempty production command")
    descriptor = _acquire_lock()
    details = os.fstat(descriptor)
    os.set_inheritable(descriptor, True)
    environment = os.environ.copy()
    environment["UNIKORN_BACKEND_MUTATION_LOCK_FD"] = str(descriptor)
    environment["UNIKORN_BACKEND_MUTATION_LOCK_DEV_INO"] = (
        f"{details.st_dev}:{details.st_ino}"
    )
    try:
        os.execvpe(command[0], command, environment)
    except OSError as error:
        os.close(descriptor)
        raise ReconciliationBlocked("cannot execute command under production lock") from error


def _entry_destination(entry: dict[str, Any]) -> tuple[str, str]:
    relative = PurePosixPath(entry["path"])
    if relative.parts[:2] != ("migrations", "versions") or len(relative.parts) != 3:
        raise ReconciliationBlocked("allowlisted path escaped the fixed migration directory")
    return "files", relative.name


def _file_record(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in entry.items()
        if key not in {"source_device", "source_inode"}
    }


def _attempt_full_restore(
    source_parent_fd: int,
    files_fd: int,
    entries: list[dict[str, Any]],
    moved_names: list[str],
) -> bool:
    moved = set(moved_names)
    identities: dict[str, tuple[int, int]] = {}
    for entry in entries:
        _directory, name = _entry_destination(entry)
        expected_file = _file_record(entry)
        try:
            _read_bound_file(
                source_parent_fd, name, entry["path"], expected=expected_file
            )
            source_state = "expected"
        except ReconciliationBlocked:
            try:
                os.stat(name, dir_fd=source_parent_fd, follow_symlinks=False)
                source_state = "conflict"
            except FileNotFoundError:
                source_state = "missing"
        try:
            _quarantined, identity = _read_bound_file(
                files_fd, name, entry["path"], expected=expected_file
            )
            if identity != (entry["source_device"], entry["source_inode"]):
                quarantine_state = "conflict"
            else:
                quarantine_state = "expected"
            identities[name] = identity
        except ReconciliationBlocked:
            try:
                os.stat(name, dir_fd=files_fd, follow_symlinks=False)
                quarantine_state = "conflict"
            except FileNotFoundError:
                quarantine_state = "missing"
        if name in moved:
            if source_state != "missing" or quarantine_state != "expected":
                return False
        elif source_state != "expected" or quarantine_state != "missing":
            return False

    for entry in reversed(entries):
        _directory, name = _entry_destination(entry)
        if name not in moved:
            continue
        _failure_point(
            "before_restore_link",
            {"name": name, "source": entry["path"]},
        )
        os.link(
            name,
            name,
            src_dir_fd=files_fd,
            dst_dir_fd=source_parent_fd,
            follow_symlinks=False,
        )
        _fsync_directory(source_parent_fd)
        restored, identity = _read_bound_file(
            source_parent_fd,
            name,
            entry["path"],
            expected=_file_record(entry),
            allowed_link_counts=frozenset({2}),
        )
        if restored != _file_record(entry) or identity != identities[name]:
            return False
        os.unlink(name, dir_fd=files_fd)
        _fsync_directory(files_fd)
        restored, identity = _read_bound_file(
            source_parent_fd, name, entry["path"], expected=_file_record(entry)
        )
        if restored != _file_record(entry) or identity != identities[name]:
            return False
    return True


def _remove_empty_transaction(
    quarantine_root_fd: int,
    transaction_name: str,
    transaction_fd: int,
    files_fd: int,
    *,
    prepared_exists: bool,
) -> None:
    if prepared_exists:
        os.unlink("PREPARED.json", dir_fd=transaction_fd)
        _fsync_directory(transaction_fd)
    os.rmdir("files", dir_fd=transaction_fd)
    _fsync_directory(transaction_fd)
    os.close(files_fd)
    os.close(transaction_fd)
    os.rmdir(transaction_name, dir_fd=quarantine_root_fd)
    _fsync_directory(quarantine_root_fd)


def apply(
    repo: Path,
    expected_digest: str,
    confirmation: str,
    run_id: str,
) -> dict[str, Any]:
    if not SHA256_RE.fullmatch(expected_digest):
        raise ReconciliationBlocked("expected aggregate digest must be lowercase SHA-256")
    if confirmation != APPLY_CONFIRMATION:
        raise ReconciliationBlocked("apply confirmation is invalid")
    before = audit(repo)
    if before["aggregate_sha256"] != expected_digest:
        raise ReconciliationBlocked("current aggregate digest does not match the reviewed audit")
    if before["committed_allowlisted_revision_duplicates"]:
        raise ReconciliationBlocked(
            "an allowlisted file duplicates a committed migration revision"
        )
    if before["live_current_allowlisted_revisions"]:
        raise ReconciliationBlocked(
            f"live {TARGET_NAME} database still identifies an allowlisted migration as current"
        )
    if before["committed_allowlisted_revision_referenced"]:
        raise ReconciliationBlocked(
            "committed migration graph still references an allowlisted revision"
        )
    if before["live_current_unknown_revisions"]:
        raise ReconciliationBlocked(
            f"live {TARGET_NAME} database has a current revision outside the committed and allowlisted graphs"
        )

    destination = _validate_quarantine_target(
        QUARANTINE_ROOT / f"run-{run_id}", run_id
    )
    if TARGET_NAME == "dev" and (
        repo == QUARANTINE_ROOT
        or repo in QUARANTINE_ROOT.parents
        or QUARANTINE_ROOT in repo.parents
    ):
        raise ReconciliationBlocked("dev quarantine root must be outside the repository")
    if TARGET_NAME == "production" and LOCK_PATH.parent not in QUARANTINE_ROOT.parents:
        raise ReconciliationBlocked("production quarantine is outside the fixed Git ops root")
    operations_root_fd = _open_lock_parent()
    quarantine_parent = QUARANTINE_ROOT.parent
    if (
        quarantine_parent.parent != LOCK_PATH.parent
        or QUARANTINE_ROOT.parent != quarantine_parent
    ):
        os.close(operations_root_fd)
        raise ReconciliationBlocked("quarantine hierarchy is not fixed")
    try:
        quarantine_parent_fd = _ensure_owned_directory(
            operations_root_fd,
            quarantine_parent.name,
            0o700 if TARGET_NAME == "production" else 0o750,
        )
    except Exception:
        os.close(operations_root_fd)
        raise
    try:
        quarantine_root_fd = _ensure_owned_directory(
            quarantine_parent_fd,
            QUARANTINE_ROOT.name,
            0o700 if TARGET_NAME == "production" else 0o750,
        )
    except Exception:
        os.close(quarantine_parent_fd)
        os.close(operations_root_fd)
        raise
    os.close(quarantine_parent_fd)
    os.close(operations_root_fd)
    transaction_name = destination.name
    os.mkdir(transaction_name, mode=0o700, dir_fd=quarantine_root_fd)
    _fsync_directory(quarantine_root_fd)
    transaction_fd = os.open(
        transaction_name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=quarantine_root_fd,
    )
    transaction_details = os.fstat(transaction_fd)
    if transaction_details.st_uid != os.geteuid() or stat.S_IMODE(transaction_details.st_mode) != 0o700:
        os.close(transaction_fd)
        os.rmdir(transaction_name, dir_fd=quarantine_root_fd)
        _fsync_directory(quarantine_root_fd)
        os.close(quarantine_root_fd)
        raise ReconciliationBlocked("new transaction directory has unsafe metadata")
    os.mkdir("files", mode=0o700, dir_fd=transaction_fd)
    _fsync_directory(transaction_fd)
    files_fd = os.open(
        "files",
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=transaction_fd,
    )
    try:
        source_parent_fd, source_parent_identity = _open_source_parent(repo)
    except Exception:
        os.close(files_fd)
        os.rmdir("files", dir_fd=transaction_fd)
        _fsync_directory(transaction_fd)
        os.close(transaction_fd)
        os.rmdir(transaction_name, dir_fd=quarantine_root_fd)
        _fsync_directory(quarantine_root_fd)
        os.close(quarantine_root_fd)
        raise
    if os.fstat(source_parent_fd).st_dev != os.fstat(files_fd).st_dev:
        os.close(source_parent_fd)
        os.close(files_fd)
        os.rmdir("files", dir_fd=transaction_fd)
        _fsync_directory(transaction_fd)
        os.close(transaction_fd)
        os.rmdir(transaction_name, dir_fd=quarantine_root_fd)
        _fsync_directory(quarantine_root_fd)
        os.close(quarantine_root_fd)
        raise ReconciliationBlocked("quarantine must share the checkout filesystem")
    bound_entries = []
    try:
        for entry in before["files"]:
            _directory, name = _entry_destination(entry)
            _bound, identity = _read_bound_file(
                source_parent_fd, name, entry["path"], expected=entry
            )
            bound_entries.append(
                {
                    **entry,
                    "source_device": identity[0],
                    "source_inode": identity[1],
                }
            )
    except Exception:
        os.close(source_parent_fd)
        os.close(files_fd)
        os.rmdir("files", dir_fd=transaction_fd)
        _fsync_directory(transaction_fd)
        os.close(transaction_fd)
        os.rmdir(transaction_name, dir_fd=quarantine_root_fd)
        _fsync_directory(quarantine_root_fd)
        os.close(quarantine_root_fd)
        raise
    prepared = {
        **before,
        "quarantine": str(destination),
        "workflow_run_id": run_id,
        "state": "PREPARED",
        "source_parent_device": source_parent_identity[0],
        "source_parent_inode": source_parent_identity[1],
        "mappings": [
            {
                "source": entry["path"],
                "destination": f"files/{PurePosixPath(entry['path']).name}",
                "sha256": entry["sha256"],
                "size": entry["size"],
                "source_device": entry["source_device"],
                "source_inode": entry["source_inode"],
            }
            for entry in bound_entries
        ],
    }
    prepared_payload = (
        json.dumps(prepared, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    moved_names: list[str] = []
    committed_written = False
    prepared_written = False
    try:
        prepared_sha256 = _write_durable_file(
            transaction_fd, "PREPARED.json", prepared_payload, 0o400
        )
        prepared_written = True
        _fsync_directory(quarantine_root_fd)
        _failure_point("after_prepared", {"destination": str(destination)})
        for entry in bound_entries:
            _directory, name = _entry_destination(entry)
            expected_file_entry = _file_record(entry)
            _source_entry, source_identity = _read_bound_file(
                source_parent_fd,
                name,
                entry["path"],
                expected=expected_file_entry,
                sync_contents=True,
            )
            if source_identity != (entry["source_device"], entry["source_inode"]):
                raise ReconciliationBlocked(f"file inode changed before move: {entry['path']}")
            if source_identity[0] != source_parent_identity[0]:
                raise ReconciliationBlocked("allowlisted file changed filesystem")
            os.rename(
                name,
                name,
                src_dir_fd=source_parent_fd,
                dst_dir_fd=files_fd,
            )
            moved_names.append(name)
            _fsync_directory(source_parent_fd)
            _fsync_directory(files_fd)
            _failure_point(
                "after_move",
                {"name": name, "moved_count": len(moved_names)},
            )
            target_entry, target_identity = _read_bound_file(
                files_fd, name, entry["path"], expected=expected_file_entry
            )
            if target_entry != expected_file_entry or target_identity != source_identity:
                raise ReconciliationBlocked(f"quarantine verification failed: {entry['path']}")

        if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ReconciliationBlocked("git checkout is not clean after quarantine")
        _failure_point("before_committed", {"destination": str(destination)})
        committed = {
            "state": "COMMITTED",
            "prepared_sha256": prepared_sha256,
            "aggregate_sha256": expected_digest,
            "file_count": len(moved_names),
            "git_clean": True,
        }
        committed_payload = (
            json.dumps(committed, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        committed_sha256 = _write_durable_file(
            transaction_fd, "COMMITTED.json", committed_payload, 0o400
        )
        committed_written = True
        _failure_point("after_committed", {"destination": str(destination)})
        # Leave the transaction directory owner-accessible. PREPARED/COMMITTED
        # and the file hashes provide integrity; retaining 0700 directories
        # keeps manual recovery possible without chmod path traversal.
    except Exception as error:
        if committed_written:
            os.close(source_parent_fd)
            os.close(files_fd)
            os.close(transaction_fd)
            os.close(quarantine_root_fd)
            raise ManualRecoveryRequired(
                f"committed transaction retained at {destination}; manual verification required"
            ) from error
        restored = False
        try:
            restored = _attempt_full_restore(
                source_parent_fd,
                files_fd,
                bound_entries,
                moved_names,
            )
        except Exception:
            restored = False
        if restored:
            _remove_empty_transaction(
                quarantine_root_fd,
                transaction_name,
                transaction_fd,
                files_fd,
                prepared_exists=prepared_written,
            )
            os.close(source_parent_fd)
            os.close(quarantine_root_fd)
            raise
        os.close(source_parent_fd)
        os.close(files_fd)
        os.close(transaction_fd)
        os.close(quarantine_root_fd)
        raise ManualRecoveryRequired(
            f"transaction retained at {destination}; originals were not deleted; manual recovery required"
        ) from error

    os.close(source_parent_fd)
    os.close(files_fd)
    os.close(transaction_fd)
    os.close(quarantine_root_fd)

    return {
        "status": "quarantined",
        "aggregate_sha256": expected_digest,
        "file_count": len(moved_names),
        "quarantine": str(destination),
        "prepared_sha256": prepared_sha256,
        "committed_sha256": committed_sha256,
        "git_clean": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=tuple(TARGETS))
    parser.add_argument(
        "--mode",
        required=True,
        choices=("audit", "apply", "verify-transactions"),
    )
    parser.add_argument("--expected-aggregate-sha256", default="")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--workflow-run-id", default="")
    parser.add_argument("--lock-exec-command", nargs=argparse.REMAINDER)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    lock_descriptor: int | None = None
    try:
        configure_target(arguments.target)
        if arguments.lock_exec_command:
            if arguments.mode != "verify-transactions":
                raise ReconciliationBlocked("lock-exec is only valid for transaction verification")
            lock_and_exec_production(arguments.lock_exec_command)
        lock_descriptor = _acquire_lock()
        if arguments.mode in {"audit", "verify-transactions"}:
            if any(
                (
                    arguments.expected_aggregate_sha256,
                    arguments.confirmation,
                    arguments.workflow_run_id,
                )
            ):
                raise ReconciliationBlocked(
                    f"{arguments.mode} does not accept apply controls"
                )
            result = (
                audit(APP_DIR)
                if arguments.mode == "audit"
                else verify_production_transactions()
            )
        else:
            result = apply(
                APP_DIR,
                arguments.expected_aggregate_sha256,
                arguments.confirmation,
                arguments.workflow_run_id,
            )
    except ReconciliationBlocked as error:
        print(f"reconciliation blocked: {error}", file=sys.stderr)
        return 2
    finally:
        if lock_descriptor is not None:
            os.close(lock_descriptor)
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
