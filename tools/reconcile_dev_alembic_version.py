#!/usr/bin/env python3
"""Reconcile one reviewed legacy dev Alembic head to its canonical equivalent.

The helper is intentionally stdlib-only. Database access is delegated to the
checked-out virtualenv so the streamed, reviewed helper does not depend on the
GitHub runner environment.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any


APP_DIR = Path("/data/dev_unikorn/back-end")
EXPECTED_BRANCH = "main"
EXPECTED_DATABASE = "dev_unikorn"
LEGACY_REVISION = "1effc88ae61e"
CANONICAL_REVISION = "5202003d1ec0"
COMPANION_REVISION = "20260807_sched_popularity"
APPLY_CONFIRMATION = "REPLACE_DEV_LEGACY_ALEMBIC_HEAD"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
BACKUP_SHA256_RE = SHA256_RE
LOCK_KEY = int.from_bytes(b"UNIKORN", byteorder="big", signed=False)

LEGACY_FILES = {
    "migrations/versions/0e18af78068e_.py": (
        "319acb28363c22d746f541ce34200e00436277b3a706cf6b4d65c4453e07899e"
    ),
    "migrations/versions/1effc88ae61e_.py": (
        "6f9d00e4029f1d86170b7556cc5831aca62204d17a5a1aa56fb425480317eeca"
    ),
    "migrations/versions/6734a89a7bb7_.py": (
        "fa09dd9e11dda63e6fc1503230eeceac310d7ac522888cc5f37e2664aa29c660"
    ),
    "migrations/versions/67c45f677a8a_.py": (
        "2119f9a1545282ac8a9359a7a7cb5e2f9cdfffd7c26608d22795610058f37cc4"
    ),
    "migrations/versions/6fd25dd56cc7_.py": (
        "c7fa3c802e62f25aeeb0830ec8ebcfe6fb8b6531a803c282c1d25ec66d3675bd"
    ),
    "migrations/versions/70dd1b7c30df_.py": (
        "4b2e26e65c2b36c7fffa7447ee78bdfb30aeec3d20b783b0cc1bfb973545cfb0"
    ),
    "migrations/versions/73e858c1a76c_.py": (
        "77e72d44fb9c5c4cdc43b3ccbe54e79463e8213f608d2c0226bf9c748bc47f20"
    ),
    "migrations/versions/8accb2f129c8_.py": (
        "7887eacf7412a2f41128616133831967e6d8a0d0ac2834726fe77056154f1a68"
    ),
    "migrations/versions/c93a7d7db52a_initial20250821.py": (
        "c7ee2647b934562ca230860cadbe7f0cb030ca7ecb0c8049cd68fb8358b41f6b"
    ),
    "migrations/versions/ca9460bf287f_.py": (
        "b46140cf07799b846ebd18227d81d0f064e7f6a48384cc2f6a6758865f024b26"
    ),
    "migrations/versions/d79de51fc5f3_.py": (
        "cb1a1c14ecf98bffb6e8765f0d2e660c9c6eec90af4f8c67f23f7878ca9bcae5"
    ),
    "migrations/versions/da5f7cad7d38_.py": (
        "4bcfdc2082fb99d975ca8a7b685182e91df608d096644031cc75d31f69610e1f"
    ),
}


class ReconciliationBlocked(RuntimeError):
    """The live host no longer matches the reviewed reconciliation boundary."""


def _run(*arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments),
        cwd=APP_DIR,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    return result


def _git(*arguments: str) -> str:
    result = _run("git", "-C", str(APP_DIR), *arguments)
    if result.returncode:
        raise ReconciliationBlocked("git preflight failed")
    return result.stdout.rstrip("\n")


def _literal(source: str, name: str, label: str) -> Any:
    try:
        tree = ast.parse(source, filename=label)
    except SyntaxError as error:
        raise ReconciliationBlocked(f"invalid migration syntax: {label}") from error
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                try:
                    return ast.literal_eval(node.value)
                except (TypeError, ValueError) as error:
                    raise ReconciliationBlocked(
                        f"migration metadata is not literal: {label}"
                    ) from error
    raise ReconciliationBlocked(f"migration metadata is missing: {label}")


def _parents(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ReconciliationBlocked(f"migration parent metadata is invalid: {label}")


def _committed_graph() -> dict[str, Any]:
    names = _git("ls-tree", "-r", "--name-only", "HEAD", "--", "migrations/versions")
    paths = sorted(path for path in names.splitlines() if path.endswith(".py"))
    revisions: dict[str, list[str]] = {}
    for path in paths:
        source = _git("show", f"HEAD:{path}")
        revision = _literal(source, "revision", path)
        if not isinstance(revision, str) or revision in revisions:
            raise ReconciliationBlocked("committed migration revisions are invalid or duplicated")
        revisions[revision] = _parents(_literal(source, "down_revision", path), path)
    parents = {parent for values in revisions.values() for parent in values}
    heads = sorted(set(revisions) - parents)
    if CANONICAL_REVISION not in revisions:
        raise ReconciliationBlocked("canonical replacement revision is not committed")
    if LEGACY_REVISION in revisions:
        raise ReconciliationBlocked("legacy revision unexpectedly exists in committed history")
    if heads != [COMPANION_REVISION, CANONICAL_REVISION]:
        raise ReconciliationBlocked("committed migration heads do not match the reviewed graph")
    return {"heads": heads, "revision_count": len(revisions)}


def _validate_checkout() -> dict[str, Any]:
    if APP_DIR.is_symlink() or APP_DIR.resolve() != APP_DIR or not (APP_DIR / ".git").is_dir():
        raise ReconciliationBlocked("fixed dev checkout path is invalid")
    details = APP_DIR.stat()
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_mode & 0o022
    ):
        raise ReconciliationBlocked("fixed dev checkout has unsafe ownership or permissions")
    if _git("rev-parse", "--show-toplevel") != str(APP_DIR):
        raise ReconciliationBlocked("git toplevel does not match the fixed dev checkout")
    if _git("symbolic-ref", "--short", "HEAD") != EXPECTED_BRANCH:
        raise ReconciliationBlocked("fixed dev checkout is not on main")
    repository_sha = _git("rev-parse", "HEAD")
    if not GIT_SHA_RE.fullmatch(repository_sha):
        raise ReconciliationBlocked("repository SHA is invalid")
    status = _git("status", "--porcelain=v1", "--untracked-files=all", "--no-renames")
    expected_status = {f"?? {path}" for path in LEGACY_FILES}
    if set(status.splitlines()) != expected_status or len(status.splitlines()) != len(
        expected_status
    ):
        raise ReconciliationBlocked("checkout does not contain only the reviewed legacy files")
    inspected = []
    for relative_path, expected_sha256 in sorted(LEGACY_FILES.items()):
        path = APP_DIR / relative_path
        file_details = path.lstat()
        if (
            not stat.S_ISREG(file_details.st_mode)
            or file_details.st_uid != os.geteuid()
            or file_details.st_gid != os.getegid()
            or file_details.st_nlink != 1
            or stat.S_IMODE(file_details.st_mode) != 0o664
        ):
            raise ReconciliationBlocked(f"legacy file metadata is unsafe: {relative_path}")
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ReconciliationBlocked(f"legacy file digest changed: {relative_path}")
        inspected.append({"path": relative_path, "sha256": actual_sha256})
    return {"repository_sha": repository_sha, "legacy_files": inspected}


def _runtime_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "AUTO_INIT_ON_STARTUP": "false",
            "ENABLE_BACKGROUND_TASKS": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return environment


def _database_probe(*, apply: bool = False) -> dict[str, Any]:
    script = f"""
import json
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app.config import Config
url = make_url(Config.SQLALCHEMY_DATABASE_URI)
if url.get_backend_name() != 'postgresql' or url.database != {EXPECTED_DATABASE!r}:
    raise SystemExit(31)
engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, **Config.SQLALCHEMY_ENGINE_OPTIONS)
with engine.begin() as connection:
    if connection.execute(text('SELECT current_database()')).scalar_one() != {EXPECTED_DATABASE!r}:
        raise SystemExit(32)
    connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {{'key': {LOCK_KEY}}})
    before = connection.execute(
        text('SELECT version_num FROM alembic_version ORDER BY version_num FOR UPDATE')
    ).scalars().all()
    expected = [{LEGACY_REVISION!r}, {COMPANION_REVISION!r}]
    if before != expected:
        raise SystemExit(33)
    if {apply!r}:
        deleted = connection.execute(
            text('DELETE FROM alembic_version WHERE version_num = :revision'),
            {{'revision': {LEGACY_REVISION!r}}},
        )
        if deleted.rowcount != 1:
            raise SystemExit(34)
        connection.execute(
            text('INSERT INTO alembic_version (version_num) VALUES (:revision)'),
            {{'revision': {CANONICAL_REVISION!r}}},
        )
        after = connection.execute(
            text('SELECT version_num FROM alembic_version ORDER BY version_num')
        ).scalars().all()
        if after != [{COMPANION_REVISION!r}, {CANONICAL_REVISION!r}]:
            raise SystemExit(35)
    else:
        after = before
print(json.dumps(
    {{'database': {EXPECTED_DATABASE!r}, 'before': before, 'after': after}},
    sort_keys=True,
))
"""
    result = _run(
        str(APP_DIR / "venv" / "bin" / "python"),
        "-c",
        script,
        env=_runtime_environment(),
    )
    if result.returncode:
        raise ReconciliationBlocked("database revision probe or transaction failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ReconciliationBlocked("database revision probe returned invalid output") from error
    return payload


def _schema_check() -> None:
    # Compare the live schema directly with SQLAlchemy metadata. Do not invoke
    # Alembic's ScriptDirectory while the reviewed untracked migration files
    # are present, because loading migration modules would execute their code.
    script = f"""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app import create_app
from app.config import Config
from app.extensions import db
url = make_url(Config.SQLALCHEMY_DATABASE_URI)
if url.get_backend_name() != 'postgresql' or url.database != {EXPECTED_DATABASE!r}:
    raise SystemExit(41)
app = create_app()
engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, **Config.SQLALCHEMY_ENGINE_OPTIONS)
with app.app_context(), engine.connect() as connection:
    if connection.execute(text('SELECT current_database()')).scalar_one() != {EXPECTED_DATABASE!r}:
        raise SystemExit(42)
    differences = compare_metadata(MigrationContext.configure(connection), db.metadata)
if differences:
    raise SystemExit(43)
"""
    result = _run(
        str(APP_DIR / "venv" / "bin" / "python"),
        "-c",
        script,
        env=_runtime_environment(),
    )
    if result.returncode:
        raise ReconciliationBlocked("database schema does not match the checked-out models")


def _digest(context: dict[str, Any]) -> str:
    payload = json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def audit() -> dict[str, Any]:
    checkout = _validate_checkout()
    graph = _committed_graph()
    database = _database_probe()
    _schema_check()
    context = {
        "schema_version": 1,
        "target": "dev",
        "database": EXPECTED_DATABASE,
        "legacy_revision": LEGACY_REVISION,
        "canonical_revision": CANONICAL_REVISION,
        "companion_revision": COMPANION_REVISION,
        "schema_check": "passed",
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        **checkout,
        "committed_graph": graph,
        "current_revisions": database["before"],
    }
    return {**context, "aggregate_sha256": _digest(context), "status": "requires_reconciliation"}


def apply(expected_digest: str, confirmation: str, backup_sha256: str) -> dict[str, Any]:
    if not SHA256_RE.fullmatch(expected_digest):
        raise ReconciliationBlocked("expected aggregate digest must be lowercase SHA-256")
    if confirmation != APPLY_CONFIRMATION:
        raise ReconciliationBlocked("apply confirmation is invalid")
    if not BACKUP_SHA256_RE.fullmatch(backup_sha256):
        raise ReconciliationBlocked("verified backup digest is invalid")
    before = audit()
    if before["aggregate_sha256"] != expected_digest:
        raise ReconciliationBlocked("current host state does not match the reviewed audit")
    transaction = _database_probe(apply=True)
    return {
        "status": "reconciled",
        "database": EXPECTED_DATABASE,
        "before": transaction["before"],
        "after": transaction["after"],
        "aggregate_sha256": expected_digest,
        "backup_sha256": backup_sha256,
        "repository_sha": before["repository_sha"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("audit", "apply"))
    parser.add_argument("--expected-aggregate-sha256", default="")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--backup-sha256", default="")
    arguments = parser.parse_args()
    try:
        if arguments.mode == "audit":
            if (
                arguments.expected_aggregate_sha256
                or arguments.confirmation
                or arguments.backup_sha256
            ):
                raise ReconciliationBlocked("audit does not accept apply controls")
            result = audit()
        else:
            result = apply(
                arguments.expected_aggregate_sha256,
                arguments.confirmation,
                arguments.backup_sha256,
            )
    except ReconciliationBlocked as error:
        print(f"reconciliation blocked: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
