#!/usr/bin/env python3
"""Audit and recoverably quarantine one known dev-checkout migration set.

This helper is intentionally stdlib-only so a reviewed copy can be streamed to
the dev host without importing or executing any migration module.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any


APP_DIR = Path("/data/dev_unikorn/back-end")
QUARANTINE_ROOT = Path("/data/dev_unikorn/quarantine/legacy-migrations")
EXPECTED_BRANCH = "main"
EXPECTED_DATABASE = "dev_unikorn"
APPLY_CONFIRMATION = "QUARANTINE_DEV_LEGACY_MIGRATIONS"
MAX_FILE_BYTES = 1_048_576
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID_RE = re.compile(r"[1-9][0-9]{0,19}\Z")

ALLOWLIST = (
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
ALLOWLIST_SET = frozenset(ALLOWLIST)


class ReconciliationBlocked(RuntimeError):
    """The checkout does not exactly match the reviewed recovery boundary."""


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


def _inspect_file(path: Path, relative_path: str) -> dict[str, Any]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
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
        if before.st_nlink != 1:
            raise ReconciliationBlocked(
                f"allowlisted file has multiple hard links: {relative_path}"
            )
        if before.st_size > MAX_FILE_BYTES:
            raise ReconciliationBlocked(f"allowlisted file is oversized: {relative_path}")
        with os.fdopen(descriptor, "rb", closefd=False) as source_file:
            payload = source_file.read(MAX_FILE_BYTES + 1)
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
    return {
        "path": relative_path,
        "size": before.st_size,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "revision": _validate_revision(
            _literal_assignment(tree, "revision", relative_path), relative_path
        ),
        "down_revision": _validate_down_revision(
            _literal_assignment(tree, "down_revision", relative_path), relative_path
        ),
    }


def aggregate_digest(files: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        files,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _metadata_for_source(source: str, filename: str) -> tuple[str, list[str]]:
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
    return revision, parents


def committed_graph(repo: Path, allowlisted_revisions: set[str]) -> dict[str, Any]:
    names = _git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", "migrations/versions")
    migration_paths = sorted(
        path for path in names.splitlines() if path.endswith(".py")
    )
    revisions: dict[str, list[str]] = {}
    for path in migration_paths:
        revision, parents = _metadata_for_source(
            _git(repo, "show", f"HEAD:{path}"), path
        )
        if revision in revisions:
            raise ReconciliationBlocked("committed graph has duplicate revision identifiers")
        revisions[revision] = parents
    all_parents = {parent for parents in revisions.values() for parent in parents}
    references = sorted(allowlisted_revisions & all_parents)
    return {
        "heads": sorted(set(revisions) - all_parents),
        "allowlisted_revision_references": references,
        "allowlisted_revision_referenced": bool(references),
    }


def _live_database_revisions(repo: Path) -> list[str]:
    probe = """
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app.config import Config
url = make_url(Config.SQLALCHEMY_DATABASE_URI)
if url.get_backend_name() != 'postgresql' or url.database != 'dev_unikorn':
    raise SystemExit(23)
engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, **Config.SQLALCHEMY_ENGINE_OPTIONS)
with engine.connect() as connection:
    if connection.execute(text('SELECT current_database()')).scalar_one() != 'dev_unikorn':
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
        raise ReconciliationBlocked("read-only dev Alembic revision probe failed")
    revisions = result.stdout.splitlines()
    if not revisions:
        raise ReconciliationBlocked("dev Alembic version table returned no current revisions")
    for revision in revisions:
        _validate_revision(revision, "dev alembic_version")
    return sorted(set(revisions))


def audit(repo: Path, *, live_revisions: list[str] | None = None) -> dict[str, Any]:
    if repo != APP_DIR or repo.is_symlink() or repo.resolve() != APP_DIR:
        raise ReconciliationBlocked("repository path is not the fixed dev checkout")
    if not (repo / ".git").exists():
        raise ReconciliationBlocked("fixed dev checkout is not a git worktree")
    if _git(repo, "rev-parse", "--show-toplevel") != str(repo.resolve()):
        raise ReconciliationBlocked("git toplevel does not match the fixed dev checkout")
    if _git(repo, "symbolic-ref", "--short", "HEAD") != EXPECTED_BRANCH:
        raise ReconciliationBlocked("fixed dev checkout is not on main")
    _exact_untracked_allowlist(repo)
    files = [_inspect_file(repo / path, path) for path in ALLOWLIST]
    revisions = [entry["revision"] for entry in files]
    if len(set(revisions)) != len(revisions):
        raise ReconciliationBlocked("duplicate migration revision identifiers detected")
    graph = committed_graph(repo, set(revisions))
    current_revisions = (
        _live_database_revisions(repo) if live_revisions is None else live_revisions
    )
    current_allowlisted = sorted(set(revisions) & set(current_revisions))
    return {
        "schema_version": 1,
        "target": "dev",
        "repository": str(APP_DIR),
        "branch": EXPECTED_BRANCH,
        "database": EXPECTED_DATABASE,
        "live_current_revisions": current_revisions,
        "live_current_allowlisted_revisions": current_allowlisted,
        "committed_heads": graph["heads"],
        "committed_allowlisted_revision_references": graph[
            "allowlisted_revision_references"
        ],
        "committed_allowlisted_revision_referenced": graph[
            "allowlisted_revision_referenced"
        ],
        "files": files,
        "aggregate_sha256": aggregate_digest(files),
    }


def _validate_quarantine_target(path: Path, run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ReconciliationBlocked("workflow run id is invalid")
    expected = QUARANTINE_ROOT / f"run-{run_id}"
    if path != expected:
        raise ReconciliationBlocked("quarantine path does not match the fixed root/run id")
    if path.exists() or path.is_symlink():
        raise ReconciliationBlocked("quarantine destination already exists")
    return path


def _validate_fixed_parent(path: Path) -> None:
    details = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_mode & 0o022
    ):
        raise ReconciliationBlocked(f"unsafe parent directory: {path}")


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
    if before["live_current_allowlisted_revisions"]:
        raise ReconciliationBlocked(
            "live dev database still identifies an allowlisted migration as current"
        )

    destination = _validate_quarantine_target(
        QUARANTINE_ROOT / f"run-{run_id}", run_id
    )
    if repo == QUARANTINE_ROOT or repo in QUARANTINE_ROOT.parents or QUARANTINE_ROOT in repo.parents:
        raise ReconciliationBlocked("quarantine root must be outside the repository")
    fixed_data_parent = APP_DIR.parent
    _validate_fixed_parent(fixed_data_parent)
    quarantine_parent = QUARANTINE_ROOT.parent
    quarantine_parent.mkdir(mode=0o750, exist_ok=True)
    _validate_fixed_parent(quarantine_parent)
    QUARANTINE_ROOT.mkdir(mode=0o750, exist_ok=True)
    if (
        QUARANTINE_ROOT.is_symlink()
        or not QUARANTINE_ROOT.is_dir()
        or QUARANTINE_ROOT.resolve() != QUARANTINE_ROOT
    ):
        raise ReconciliationBlocked("quarantine root is not a fixed regular directory")
    os.chmod(QUARANTINE_ROOT, 0o750)
    _validate_fixed_parent(QUARANTINE_ROOT)
    destination.mkdir(mode=0o700)
    moved: list[tuple[Path, Path]] = []
    manifest = {
        **before,
        "quarantine": str(destination),
        "workflow_run_id": run_id,
    }
    try:
        for entry in before["files"]:
            relative = PurePosixPath(entry["path"])
            source = repo.joinpath(*relative.parts)
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            source_details = source.lstat()
            if not stat.S_ISREG(source_details.st_mode) or source.is_symlink():
                raise ReconciliationBlocked(f"file changed before move: {entry['path']}")
            if (
                source_details.st_size != entry["size"]
                or source_details.st_nlink != 1
                or hashlib.sha256(source.read_bytes()).hexdigest() != entry["sha256"]
            ):
                raise ReconciliationBlocked(f"file changed before move: {entry['path']}")
            if source_details.st_dev != destination.stat().st_dev:
                raise ReconciliationBlocked("quarantine must share the checkout filesystem")
            os.replace(source, target)
            moved.append((source, target))
            if _inspect_file(target, entry["path"]) != entry:
                raise ReconciliationBlocked(f"quarantine verification failed: {entry['path']}")

        manifest_payload = (
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        manifest_path = destination / "manifest.json"
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination, prefix=".manifest-", delete=False
        ) as temporary:
            temporary.write(manifest_payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, manifest_path)
        os.chmod(manifest_path, 0o400)
        if manifest_path.read_bytes() != manifest_payload:
            raise ReconciliationBlocked("quarantine manifest verification failed")
        if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ReconciliationBlocked("git checkout is not clean after quarantine")
        for _source, target in moved:
            os.chmod(target, 0o400)
        for directory in sorted(
            (path for path in destination.rglob("*") if path.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            os.chmod(directory, 0o500)
        os.chmod(destination, 0o500)
    except Exception:
        if destination.exists() and not destination.is_symlink():
            for child in destination.rglob("*"):
                try:
                    if child.is_dir() and not child.is_symlink():
                        os.chmod(child, 0o700)
                    elif child.is_file() and not child.is_symlink():
                        os.chmod(child, 0o600)
                except OSError:
                    pass
            try:
                os.chmod(destination, 0o700)
            except OSError:
                pass
        for source, target in reversed(moved):
            if target.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, source)
        shutil.rmtree(destination, ignore_errors=True)
        raise

    return {
        "status": "quarantined",
        "aggregate_sha256": expected_digest,
        "file_count": len(moved),
        "quarantine": str(destination),
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "git_clean": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("audit", "apply"))
    parser.add_argument("--expected-aggregate-sha256", default="")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--workflow-run-id", default="")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        if arguments.mode == "audit":
            if any(
                (
                    arguments.expected_aggregate_sha256,
                    arguments.confirmation,
                    arguments.workflow_run_id,
                )
            ):
                raise ReconciliationBlocked("audit does not accept apply controls")
            result = audit(APP_DIR)
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
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
