import argparse
import json
import os
from pathlib import Path

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app import create_app
from app.config import Config
from app.extensions import db
from app.scripts import run_backend_operation as operations


ROOT = Path(__file__).resolve().parents[1]


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CACHE_TYPE = "SimpleCache"
    ENABLE_BACKGROUND_TASKS = False
    JWT_SECRET_KEY = "test-secret"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "test-key"))
    monkeypatch.setenv("DASHSCOPE_API_KEY", os.getenv("DASHSCOPE_API_KEY", "test-key"))
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _args(*extra):
    return operations.build_parser().parse_args(
        [
            "--request-id",
            "test-request",
            "--workflow-run-id",
            "12345",
            "--operation",
            "scheduler-import",
            "--mode",
            "dry-run",
            "--target",
            "dev",
            "--release-sha",
            "a" * 40,
            "--actor",
            "test-actor",
            "--package-id",
            "scheduler-2610-v1",
            *extra,
        ]
    )


def test_committed_operation_packages_match_reviewed_files():
    registry = operations._load_registry()

    scheduler = operations._resolve_package("scheduler-2610-v1", "scheduler")
    assert scheduler["resolved_path"] == (
        ROOT / "app/data/pending/scheduler_offerings/26-27fall.json"
    ).resolve()
    assert scheduler["sha256"] == "4ec2cb305a31348944cba064dba9435825f19d5c1b99f9e2e8177e233eddfbff"
    assert scheduler["expected"] == {
        "courses": 383,
        "offered_courses": 383,
        "sections": 801,
        "lectures": 820,
    }

    curriculum = operations._resolve_package("curriculum-2026-v1", "curriculum")
    assert curriculum["sha256"] == "a99cbe5c120ba5fcd707651f4609a6ca08e6d5bfa205734979ad6d9739f6b056"
    assert curriculum["expected"] == {
        "program_definitions": 8,
        "program_cohorts": 8,
        "requirement_groups": 32,
        "unique_course_codes": 292,
    }
    assert set(registry) == {"scheduler-2610-v1", "curriculum-2026-v1"}


def test_parser_exposes_only_allowlisted_operations():
    with pytest.raises(SystemExit):
        operations.build_parser().parse_args(
            [
                "--request-id",
                "request",
                "--workflow-run-id",
                "1",
                "--operation",
                "run-sql",
                "--mode",
                "apply",
                "--target",
                "production",
                "--release-sha",
                "a" * 40,
                "--actor",
                "actor",
            ]
        )

    option_names = {option for action in operations.build_parser()._actions for option in action.option_strings}
    assert not option_names & {
        "--command",
        "--sql",
        "--url",
        "--path",
        "--database-url",
        "--revision",
        "--module",
    }


def test_scheduler_dry_run_request_uses_committed_package():
    args = _args()

    package = operations._validate_args(args)

    assert package["id"] == "scheduler-2610-v1"
    assert package["semester_id"] == "2610"


@pytest.mark.parametrize(
    ("operation", "package_id"),
    [
        ("verify-release", None),
        ("scheduler-import", "scheduler-2610-v1"),
        ("curriculum-sync", "curriculum-2026-v1"),
    ],
)
def test_campus_target_accepts_only_academic_operations(operation, package_id):
    argv = [
        "--request-id",
        "campus-dry-run",
        "--workflow-run-id",
        "12345",
        "--operation",
        operation,
        "--mode",
        "dry-run",
        "--target",
        "campus",
        "--release-sha",
        "a" * 40,
        "--actor",
        "test-actor",
    ]
    if package_id:
        argv.extend(("--package-id", package_id))

    args = operations.build_parser().parse_args(argv)
    package = operations._validate_args(args)

    assert args.target == "campus"
    assert (package or {}).get("id") == package_id


@pytest.mark.parametrize("operation", ["course-duplicates", "database-upgrade-heads"])
def test_campus_target_rejects_non_academic_operations(operation):
    args = operations.build_parser().parse_args(
        [
            "--request-id",
            "campus-blocked",
            "--workflow-run-id",
            "12345",
            "--operation",
            operation,
            "--mode",
            "apply",
            "--target",
            "campus",
            "--release-sha",
            "a" * 40,
            "--actor",
            "test-actor",
            "--confirmation",
            "APPLY_CAMPUS",
            "--backup-sha256",
            "b" * 64,
        ]
    )

    with pytest.raises(operations.OperationBlocked, match="not allowlisted for campus"):
        operations._validate_args(args)


@pytest.mark.parametrize(
    ("operation", "mode", "package_id"),
    [
        ("verify-release", "apply", None),
        ("verify-release", "dry-run", "scheduler-2610-v1"),
        ("scheduler-import", "dry-run", "curriculum-2026-v1"),
        ("curriculum-sync", "dry-run", "scheduler-2610-v1"),
    ],
)
def test_campus_operation_mode_and_package_allowlist_is_exact(
    operation, mode, package_id
):
    argv = [
        "--request-id",
        "campus-blocked",
        "--workflow-run-id",
        "12345",
        "--operation",
        operation,
        "--mode",
        mode,
        "--target",
        "campus",
        "--release-sha",
        "a" * 40,
        "--actor",
        "test-actor",
    ]
    if package_id:
        argv.extend(("--package-id", package_id))
    if mode == "apply":
        argv.extend(("--confirmation", "APPLY_CAMPUS"))
        argv.extend(("--backup-sha256", "b" * 64))

    args = operations.build_parser().parse_args(argv)

    with pytest.raises(operations.OperationBlocked, match="not allowlisted for campus"):
        operations._validate_args(args)


def test_campus_apply_requires_explicit_campus_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path))
    args = _args("--target", "campus", "--mode", "apply")

    args.confirmation = "APPLY_PRODUCTION"
    with pytest.raises(operations.OperationBlocked, match="APPLY_CAMPUS"):
        operations._validate_args(args)

    args.confirmation = "APPLY_CAMPUS"
    with pytest.raises(operations.OperationBlocked, match="backup"):
        operations._validate_args(args)

    assert operations.TARGET_CONFIRMATIONS["campus"] == "APPLY_CAMPUS"


def test_campus_apply_is_bound_to_campus_dry_run_and_verified_backup(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path))
    dry_run = {
        "schema_version": 1,
        "request_id": "campus-approved",
        "workflow_run_id": "111",
        "operation": "scheduler-import",
        "mode": "dry-run",
        "target": "campus",
        "release_sha": "a" * 40,
        "package_id": "scheduler-2610-v1",
        "package_sha256": "4ec2cb305a31348944cba064dba9435825f19d5c1b99f9e2e8177e233eddfbff",
        "status": "dry-run",
        "result": {"status": "dry-run"},
    }
    dry_run["result_sha256"] = operations._sha256_json(dry_run["result"])
    operations._write_report(dry_run)
    args = _args(
        "--target",
        "campus",
        "--mode",
        "apply",
        "--confirmation",
        "APPLY_CAMPUS",
        "--backup-sha256",
        "b" * 64,
        "--approved-dry-run-id",
        "campus-approved",
    )

    package = operations._validate_args(args)

    assert package["id"] == "scheduler-2610-v1"

    args.target = "production"
    with pytest.raises(operations.OperationBlocked, match="APPLY_PRODUCTION"):
        operations._validate_args(args)


def test_github_app_actor_is_accepted():
    args = _args()
    args.actor = "course-loader[bot]"

    assert operations._validate_args(args)["id"] == "scheduler-2610-v1"


def test_apply_requires_confirmation_backup_and_approved_dry_run(monkeypatch, tmp_path):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path))
    args = _args("--mode", "apply")

    with pytest.raises(operations.OperationBlocked, match="confirmation"):
        operations._validate_args(args)

    args.confirmation = "APPLY_DEV"
    with pytest.raises(operations.OperationBlocked, match="backup"):
        operations._validate_args(args)

    args.backup_sha256 = "b" * 64
    with pytest.raises(operations.OperationBlocked, match="approved dry-run"):
        operations._validate_args(args)


def test_apply_is_bound_to_matching_dry_run_release_and_package(monkeypatch, tmp_path):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path))
    dry_run = {
        "schema_version": 1,
        "request_id": "approved-1",
        "workflow_run_id": "111",
        "operation": "scheduler-import",
        "mode": "dry-run",
        "target": "dev",
        "release_sha": "a" * 40,
        "package_id": "scheduler-2610-v1",
        "package_sha256": "4ec2cb305a31348944cba064dba9435825f19d5c1b99f9e2e8177e233eddfbff",
        "status": "dry-run",
        "result": {"status": "dry-run"},
    }
    dry_run["result_sha256"] = operations._sha256_json(dry_run["result"])
    operations._write_report(dry_run)
    args = _args(
        "--mode",
        "apply",
        "--confirmation",
        "APPLY_DEV",
        "--backup-sha256",
        "b" * 64,
        "--approved-dry-run-id",
        "approved-1",
    )

    package = operations._validate_args(args)
    assert package["id"] == "scheduler-2610-v1"

    args.release_sha = "c" * 40
    with pytest.raises(operations.OperationBlocked, match="release_sha"):
        operations._validate_args(args)


def test_operation_reports_are_immutable(monkeypatch, tmp_path):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path))
    original = {"request_id": "immutable-report", "status": "dry-run"}
    operations._write_report(original)

    with pytest.raises(FileExistsError):
        operations._write_report(
            {"request_id": "immutable-report", "status": "blocked"}
        )

    assert operations._read_report("immutable-report") == original


def test_request_idempotency_ignores_execution_evidence():
    args = _args()
    package = operations._resolve_package("scheduler-2610-v1", "scheduler")
    first = operations._request_fields(args, package)
    second = {
        **first,
        "workflow_run_id": "99999",
        "backup_sha256": "b" * 64,
    }

    assert operations._request_sha256(first) == operations._request_sha256(second)

    second["release_sha"] = "c" * 40
    assert operations._request_sha256(first) != operations._request_sha256(second)


def test_reconciliation_apply_controls_must_match_approved_plan(monkeypatch, tmp_path):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path))
    plan_sha = "d" * 64
    dry_run = {
        "schema_version": 1,
        "request_id": "reconcile-approved",
        "workflow_run_id": "222",
        "operation": "course-duplicates",
        "mode": "dry-run",
        "target": "production",
        "release_sha": "a" * 40,
        "package_id": None,
        "package_sha256": None,
        "status": "dry-run",
        "result": {
            "status": "dry-run",
            "plan_sha256": plan_sha,
            "plan": {
                "database": {"name": "prod_unikorn"},
                "pair_count": 3,
                "user_course_record_count": 2,
                "tag_count": 1,
            },
        },
    }
    dry_run["result_sha256"] = operations._sha256_json(dry_run["result"])
    operations._write_report(dry_run)
    args = operations.build_parser().parse_args(
        [
            "--request-id",
            "reconcile-apply",
            "--workflow-run-id",
            "223",
            "--operation",
            "course-duplicates",
            "--mode",
            "apply",
            "--target",
            "production",
            "--release-sha",
            "a" * 40,
            "--actor",
            "test-actor",
            "--approved-dry-run-id",
            "reconcile-approved",
            "--expected-database",
            "prod_unikorn",
            "--expected-plan-sha256",
            plan_sha,
            "--expected-pairs",
            "3",
            "--expected-records",
            "2",
            "--expected-tags",
            "1",
            "--backup-sha256",
            "b" * 64,
            "--confirmation",
            "APPLY_PRODUCTION",
        ]
    )

    assert operations._validate_args(args) is None

    args.expected_pairs = 4
    with pytest.raises(operations.OperationBlocked, match="controls"):
        operations._validate_args(args)


def test_data_apply_replans_under_lock_and_matches_approved_result(monkeypatch, tmp_path):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path))
    approved_result = {"status": "dry-run", "plan": {"sections": 801}}
    approved = {
        "request_id": "scheduler-approved",
        "result": approved_result,
        "result_sha256": operations._sha256_json(approved_result),
    }
    operations._write_report(approved)
    args = _args(
        "--mode",
        "apply",
        "--approved-dry-run-id",
        "scheduler-approved",
    )
    package = operations._resolve_package("scheduler-2610-v1", "scheduler")
    monkeypatch.setattr(
        operations,
        "_scheduler_operation",
        lambda *_args, **_kwargs: {
            "status": "dry-run",
            "plan": {"sections": 801},
        },
    )

    operations._validate_current_data_plan(args, package)

    monkeypatch.setattr(
        operations,
        "_scheduler_operation",
        lambda *_args, **_kwargs: {
            "status": "dry-run",
            "plan": {"sections": 802},
        },
    )
    with pytest.raises(operations.OperationBlocked, match="current data plan"):
        operations._validate_current_data_plan(args, package)


def test_database_upgrade_uses_fixed_argv_without_shell(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return argparse.Namespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(operations.subprocess, "run", fake_run)

    result = operations._database_upgrade_heads()

    assert result["status"] == "applied"
    assert calls[0][0] == [
        operations.sys.executable,
        "-m",
        "flask",
        "db",
        "upgrade",
        "heads",
    ]
    assert "shell" not in calls[0][1]


def test_verify_release_queries_current_legacy_scheduler_schema(app):
    with app.app_context():
        result = operations._verify_release()

    assert result["status"] == "blocked"
    assert result["checks"]["legacy_sections"] == 0
    assert result["checks"]["legacy_meetings"] == 0


def test_operation_workflow_is_a_hardened_dispatch_api():
    workflow = (ROOT / ".github/workflows/backend-operations.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "environment: production" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "set -Eeuo pipefail" in workflow
    assert "python -m app.scripts.create_verified_database_backup" in workflow
    assert "--expected-database prod_unikorn" in workflow
    assert (
        "backend-operation-${OPS_REQUEST_ID}-${OPS_WORKFLOW_RUN_ID}-${OPS_WORKFLOW_RUN_ATTEMPT}.dump"
        in workflow
    )
    assert workflow.index('if [ -f "$report_file" ]') < workflow.index(
        "python -m app.scripts.create_verified_database_backup"
    )
    assert "test -z \"$(git status --porcelain)\"" in workflow
    assert "test \"$(git rev-parse HEAD)\" = \"$OPS_RELEASE_SHA\"" in workflow
    assert "flock -n 9" in workflow
    assert "group: backend-mutations-production" in workflow
    assert "sudo /usr/bin/systemctl is-active" not in workflow
    assert '\"\"|APPLY_DEV|APPLY_PRODUCTION' in workflow
    assert "http://127.0.0.1:8001/scheduler/semesters" in workflow
    assert workflow.count("for attempt in {1..12}") == 2
    assert workflow.count('test "$api_ready" = "true"') == 2
    assert "appleboy/ssh-action@0ff4204d59e8e51228ff73bce53f80d53301dee2" in workflow
    assert "appleboy/ssh-action@master" not in workflow
    assert "${{ inputs." not in workflow.split("script: |", 1)[1]
