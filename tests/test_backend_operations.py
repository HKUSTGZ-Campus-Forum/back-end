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
from app.models.academic_map import CurriculumRequirementGroup
from app.models.course import Course
from app.models.course_domain import CourseCatalogVersion, CourseOffering, CourseSection
from app.scripts import run_backend_operation as operations
from app.scripts.import_pending_academic_data import PENDING_SCHEDULER_SUBJECTS


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


def _write_scheduler_package(tmp_path):
    payload = {
        "semester_id": "2610",
        "semester_start_date": "2026-09-01",
        "provenance": {
            "source_name": "HKUST-GZ Class Schedule & Quota",
            "term_url": "https://w5.hkust-gz.edu.cn/wcq/cgi-bin/2610/",
            "retrieved_at": "2026-08-09T12:00:00Z",
            "subjects": [
                {
                    "code": code,
                    "url": (
                        "https://w5.hkust-gz.edu.cn/wcq/cgi-bin/index.php"
                        f"?term=2610&subject={code}"
                    ),
                }
                for code in sorted(PENDING_SCHEDULER_SUBJECTS)
            ],
        },
        "courses": [
            {
                "course_code": "TEST1001",
                "course_title": "Test Course",
                "course_desc": "Reviewed description",
                "credit": 3,
                "subject": "TEST",
                "catalog_number": "1001",
                "course_title_abbr": "Test",
                "pg_course": False,
                "klms_course": False,
                "sections": [
                    {
                        "semester_id": "2610",
                        "section_id": "TEST1001-L01",
                        "course_code": "TEST1001",
                        "section_type": "L",
                        "name": "L01",
                        "bundle": 1,
                        "layer": 0,
                        "quota": 50,
                        "enrol": 47,
                        "avail": 3,
                        "wait": 2,
                        "is_main": True,
                        "lectures": [
                            {
                                "day": 1,
                                "start_time": "0900",
                                "end_time": "1050",
                                "room": "Room 101",
                                "instructor": "Dr. Test",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    path = tmp_path / "scheduler-2610.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return {
        "id": "scheduler-test",
        "kind": "scheduler",
        "semester_id": "2610",
        "resolved_path": path,
        "sha256": operations.file_sha256(path),
        "expected": {
            "courses": 1,
            "offered_courses": 1,
            "sections": 1,
            "lectures": 1,
        },
    }


def _write_curriculum_package(tmp_path):
    payload = {
        "programs": [
            {
                "code": "AI",
                "cohort": "2026",
                "name_en": "Artificial Intelligence",
                "source_url": "https://ait.hkust-gz.edu.cn/programs/undergraduate-program/",
                "source_pdf_sha256": "a" * 64,
                "source_retrieved_at": "2026-08-09",
                "total_min_credits": 120,
                "common_core_min_credits": 30,
                "major_min_credits": 85,
                "home_areas": ["Information Hub"],
                "requirement_groups": [
                    {
                        "key": "major_required",
                        "name_en": "Major Required Courses",
                        "category": "major_required",
                        "min_credits": 6,
                        "sort_order": 1,
                        "rule": {
                            "rule_tree": {
                                "type": "required",
                                "courses": ["AIAA 2205", "AIAA4490"],
                            }
                        },
                    },
                    {
                        "key": "major_electives",
                        "name_en": "Major Elective Courses",
                        "category": "major_elective",
                        "min_courses": 1,
                        "sort_order": 2,
                        "rule": {
                            "rule_tree": {
                                "type": "choose",
                                "courses": ["AIAA4001"],
                            }
                        },
                    },
                ],
            }
        ]
    }
    path = tmp_path / "curriculum-2026.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return {
        "id": "curriculum-test",
        "kind": "curriculum",
        "resolved_path": path,
        "sha256": operations.file_sha256(path),
        "expected": {
            "program_definitions": 1,
            "program_cohorts": 1,
            "requirement_groups": 2,
            "unique_course_codes": 3,
        },
    }


def _apply_args(operation, package_id, approved_id):
    args = _args(
        "--mode",
        "apply",
        "--approved-dry-run-id",
        approved_id,
    )
    args.operation = operation
    args.package_id = package_id
    return args


def _write_approved_report(request_id, result):
    serialized = operations._json_value(result)
    operations._write_report(
        {
            "request_id": request_id,
            "result": serialized,
            "result_sha256": operations._sha256_json(serialized),
        }
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


def test_scheduler_apply_retry_requires_exact_postconditions(
    app, monkeypatch, tmp_path
):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path / "reports"))
    monkeypatch.setattr(operations, "create_import_app", lambda: app)
    package = _write_scheduler_package(tmp_path)

    dry_args = _args()
    dry = operations._run(dry_args, package)
    _write_approved_report("scheduler-initial", dry)
    first = operations._run(
        _apply_args("scheduler-import", package["id"], "scheduler-initial"),
        package,
    )

    post_apply_dry = operations._run(dry_args, package)
    _write_approved_report("scheduler-after", post_apply_dry)
    retry = operations._run(
        _apply_args("scheduler-import", package["id"], "scheduler-after"),
        package,
    )

    assert operations._result_status(first) == "applied"
    assert retry["status"] == "already-applied"
    assert retry["postcondition"]["matches"] is True
    with app.app_context():
        assert CourseSection.query.one().quota == 50
        CourseSection.query.one().quota = 51
        db.session.commit()

    with pytest.raises(operations.OperationBlocked, match="postconditions do not match"):
        operations._run(
            _apply_args("scheduler-import", package["id"], "scheduler-after"),
            package,
        )


def test_scheduler_retry_rejects_course_field_drift(app, monkeypatch, tmp_path):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path / "reports"))
    monkeypatch.setattr(operations, "create_import_app", lambda: app)
    package = _write_scheduler_package(tmp_path)
    dry_args = _args()
    dry = operations._run(dry_args, package)
    _write_approved_report("scheduler-course-drift", dry)
    apply_args = _apply_args(
        "scheduler-import", package["id"], "scheduler-course-drift"
    )
    operations._run(apply_args, package)

    with app.app_context():
        Course.query.filter_by(normalized_code="TEST1001").one().name = "Corrupt title"
        db.session.commit()

    with pytest.raises(operations.OperationBlocked, match="postconditions do not match"):
        operations._run(apply_args, package)


def test_scheduler_retry_rejects_repointed_catalog_version(
    app, monkeypatch, tmp_path
):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path / "reports"))
    monkeypatch.setattr(operations, "create_import_app", lambda: app)
    package = _write_scheduler_package(tmp_path)
    dry_args = _args()
    dry = operations._run(dry_args, package)
    _write_approved_report("scheduler-catalog-drift", dry)
    apply_args = _apply_args(
        "scheduler-import", package["id"], "scheduler-catalog-drift"
    )
    operations._run(apply_args, package)

    with app.app_context():
        offering = CourseOffering.query.one()
        wrong_version = CourseCatalogVersion(
            course_id=offering.course_id,
            source="scheduler_offerings",
            source_version="9999",
            title="Wrong catalog version",
            credits=3,
        )
        db.session.add(wrong_version)
        db.session.flush()
        offering.catalog_version_id = wrong_version.id
        db.session.commit()

    with pytest.raises(operations.OperationBlocked, match="postconditions do not match"):
        operations._run(apply_args, package)


@pytest.mark.parametrize("ledger_status", ["applied", "running"])
def test_scheduler_ledger_without_data_never_reports_success(
    app, monkeypatch, tmp_path, ledger_status
):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path / "reports"))
    monkeypatch.setattr(operations, "create_import_app", lambda: app)
    package = _write_scheduler_package(tmp_path)
    dry_args = _args()
    dry = operations._run(dry_args, package)
    _write_approved_report("scheduler-ledger-only", dry)

    with app.app_context():
        db.session.execute(
            operations.text(
                """
                CREATE TABLE scheduler_offering_import_runs (
                    import_hash VARCHAR(64) PRIMARY KEY,
                    semester_id VARCHAR(16) NOT NULL,
                    mode VARCHAR(16) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    summary TEXT
                )
                """
            )
        )
        db.session.execute(
            operations.text(
                """
                INSERT INTO scheduler_offering_import_runs
                    (import_hash, semester_id, mode, status, summary)
                VALUES (:import_hash, '2610', 'apply', :status, '')
                """
            ),
            {"import_hash": package["sha256"], "status": ledger_status},
        )
        db.session.commit()

    with pytest.raises(operations.OperationBlocked, match="ledger exists"):
        operations._run(
            _apply_args(
                "scheduler-import", package["id"], "scheduler-ledger-only"
            ),
            package,
        )


def test_scheduler_already_applied_path_rechecks_package_hash(
    app, monkeypatch, tmp_path
):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path / "reports"))
    monkeypatch.setattr(operations, "create_import_app", lambda: app)
    package = _write_scheduler_package(tmp_path)
    dry_args = _args()
    dry = operations._run(dry_args, package)
    _write_approved_report("scheduler-hash", dry)
    operations._run(
        _apply_args("scheduler-import", package["id"], "scheduler-hash"), package
    )

    raw = json.loads(package["resolved_path"].read_text(encoding="utf-8"))
    raw["provenance"]["retrieved_at"] = "2026-08-10T12:00:00Z"
    package["resolved_path"].write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(operations.OperationBlocked, match="SHA-256 mismatch"):
        operations._run(
            _apply_args("scheduler-import", package["id"], "scheduler-hash"),
            package,
        )


def test_curriculum_apply_retry_requires_exact_normalized_postconditions(
    app, monkeypatch, tmp_path
):
    monkeypatch.setenv(operations.REPORT_DIR_ENV, str(tmp_path / "reports"))
    monkeypatch.setattr(operations, "create_import_app", lambda: app)
    package = _write_curriculum_package(tmp_path)
    dry_args = _args()
    dry_args.operation = "curriculum-sync"
    dry_args.package_id = package["id"]
    dry = operations._run(dry_args, package)
    _write_approved_report("curriculum-initial", dry)
    first = operations._run(
        _apply_args("curriculum-sync", package["id"], "curriculum-initial"),
        package,
    )

    post_apply_dry = operations._run(dry_args, package)
    _write_approved_report("curriculum-after", post_apply_dry)
    retry = operations._run(
        _apply_args("curriculum-sync", package["id"], "curriculum-after"),
        package,
    )

    assert operations._result_status(first) == "applied"
    assert retry["status"] == "already-applied"
    with app.app_context():
        group = CurriculumRequirementGroup.query.filter_by(
            key="major_required"
        ).one()
        assert group.rule["rule_tree"]["courses"][0] == "AIAA2205"
        group.rule = {"rule_tree": {"type": "required", "courses": []}}
        db.session.commit()

    with pytest.raises(operations.OperationBlocked, match="current data state"):
        operations._run(
            _apply_args("curriculum-sync", package["id"], "curriculum-after"),
            package,
        )


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
    assert 'readonly production_ops_dir="${production_git_dir}/unikorn-operations"' in workflow
    assert "owner_uid=%s, effective_uid=%s, group_gid=%s, effective_gid=%s" in workflow
    assert "forbidden_write_bits=0022" in workflow
    assert 'readonly production_lock_path="${production_ops_dir}/backend-mutations.lock"' in workflow
    assert "git rev-parse --absolute-git-dir" in workflow
    assert "/tmp/unikorn-backend-mutation-production.lock" not in workflow
    production = workflow[workflow.index("operate-production:") :]
    assert "--mode verify-transactions" in production
    assert "UNIKORN_BACKEND_MUTATION_LOCK_FD=9" in production
    assert production.index("flock -n 9") < production.index(
        "--mode verify-transactions"
    ) < production.index('test "$(git branch --show-current)" = "production"')
    assert "group: backend-mutations-production" in workflow
    assert "sudo /usr/bin/systemctl is-active" not in workflow
    assert '\"\"|APPLY_DEV|APPLY_PRODUCTION' in workflow
    assert "http://127.0.0.1:8001/scheduler/semesters" in workflow
    assert workflow.count("for attempt in {1..12}") == 2
    assert workflow.count('test "$api_ready" = "true"') == 2
    assert "appleboy/ssh-action@0ff4204d59e8e51228ff73bce53f80d53301dee2" in workflow
    assert "appleboy/ssh-action@master" not in workflow
    assert "${{ inputs." not in workflow.split("script: |", 1)[1]
