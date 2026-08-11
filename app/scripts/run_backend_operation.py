"""Run a reviewed, allowlisted backend operation for CI.

This is the execution side of the GitHub Actions ``workflow_dispatch`` API.
It intentionally accepts operation and package identifiers, never shell commands,
SQL, URLs, database URLs, module names, revisions, or filesystem paths.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterator

from sqlalchemy import func, text

from app.extensions import db
from app.models.academic_map import CurriculumProgram, CurriculumRequirementGroup
from app.models.course import Course
from app.models.course_domain import CourseMeeting, CourseOffering, CourseSection
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_section import SchedulerSection
from app.scripts.import_pending_academic_data import (
    CurriculumExpectations,
    run_pending_curriculum_update,
    run_pending_scheduler_update,
)
from app.scripts.import_scheduler_offerings import (
    SnapshotExpectations,
    create_import_app,
    file_sha256,
)
from app.scripts.reconcile_course_duplicates import run_reconciliation
from app.services.course_domain import normalize_course_code


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_REGISTRY_PATH = ROOT / "app" / "data" / "backend_operation_packages.json"
DEFAULT_REPORT_DIR = Path("/tmp/unikorn-backend-operation-reports")
REPORT_DIR_ENV = "BACKEND_OPERATION_REPORT_DIR"
LOCK_KEY = int.from_bytes(b"UNIKORN", byteorder="big", signed=False)

OPERATIONS = (
    "verify-release",
    "scheduler-import",
    "curriculum-sync",
    "course-duplicates",
    "database-upgrade-heads",
)
MUTATING_OPERATIONS = frozenset(
    {
        "scheduler-import",
        "curriculum-sync",
        "course-duplicates",
        "database-upgrade-heads",
    }
)
IDEMPOTENCY_FIELDS = (
    "schema_version",
    "request_id",
    "operation",
    "mode",
    "target",
    "release_sha",
    "actor",
    "package_id",
    "package_sha256",
    "approved_dry_run_id",
    "expected_database",
    "expected_plan_sha256",
    "expected_pairs",
    "expected_records",
    "expected_tags",
    "confirmation",
)
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DATABASE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")


class OperationBlocked(RuntimeError):
    """A reviewed precondition was not satisfied."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_pattern(value: str | None, pattern: re.Pattern[str], label: str) -> str:
    normalized = str(value or "").strip()
    if not pattern.fullmatch(normalized):
        raise OperationBlocked(f"{label} is invalid")
    return normalized


def _report_dir() -> Path:
    path = Path(os.getenv(REPORT_DIR_ENV, str(DEFAULT_REPORT_DIR))).resolve()
    path.mkdir(mode=0o750, parents=True, exist_ok=True)
    return path


def _report_path(request_id: str) -> Path:
    return _report_dir() / f"{request_id}.json"


def _read_report(request_id: str) -> dict[str, Any]:
    _require_pattern(request_id, REQUEST_ID_RE, "approved dry-run id")
    path = _report_path(request_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OperationBlocked(f"approved dry-run report {request_id!r} does not exist") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationBlocked(f"approved dry-run report {request_id!r} is unreadable") from exc
    if not isinstance(payload, dict):
        raise OperationBlocked("approved dry-run report is malformed")
    return payload


def _write_report(payload: dict[str, Any]) -> None:
    destination = _report_path(payload["request_id"])
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        delete=False,
    ) as handle:
        handle.write(serialized)
        temporary = Path(handle.name)
    os.chmod(temporary, 0o640)
    try:
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _load_registry() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(PACKAGE_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationBlocked("committed operation package registry is unreadable") from exc
    if payload.get("schema_version") != 1 or not isinstance(payload.get("packages"), dict):
        raise OperationBlocked("committed operation package registry is malformed")
    return payload["packages"]


def _resolve_package(package_id: str | None, expected_kind: str) -> dict[str, Any]:
    packages = _load_registry()
    package = packages.get(str(package_id or ""))
    if not isinstance(package, dict) or package.get("kind") != expected_kind:
        raise OperationBlocked(
            f"package_id must select a committed {expected_kind} package"
        )

    relative_path = package.get("path")
    if not isinstance(relative_path, str) or Path(relative_path).is_absolute():
        raise OperationBlocked("committed package path is invalid")
    resolved_path = (ROOT / relative_path).resolve()
    try:
        resolved_path.relative_to(ROOT)
    except ValueError as exc:
        raise OperationBlocked("committed package path escapes the repository") from exc

    expected_sha256 = _require_pattern(package.get("sha256"), SHA256_RE, "package SHA-256")
    try:
        actual_sha256 = file_sha256(resolved_path)
    except OSError as exc:
        raise OperationBlocked("committed package file is unreadable") from exc
    if actual_sha256 != expected_sha256:
        raise OperationBlocked(
            f"committed package SHA-256 mismatch: {actual_sha256} != {expected_sha256}"
        )

    return {
        **package,
        "id": package_id,
        "resolved_path": resolved_path,
        "sha256": expected_sha256,
    }


def _request_fields(args: argparse.Namespace, package: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "request_id": args.request_id,
        "workflow_run_id": args.workflow_run_id,
        "operation": args.operation,
        "mode": args.mode,
        "target": args.target,
        "release_sha": args.release_sha,
        "actor": args.actor,
        "package_id": package.get("id") if package else None,
        "package_sha256": package.get("sha256") if package else None,
        "approved_dry_run_id": args.approved_dry_run_id or None,
        "expected_database": args.expected_database or None,
        "expected_plan_sha256": args.expected_plan_sha256 or None,
        "expected_pairs": args.expected_pairs,
        "expected_records": args.expected_records,
        "expected_tags": args.expected_tags,
        "backup_sha256": args.backup_sha256 or None,
        "confirmation": args.confirmation or None,
    }


def _request_sha256(request: dict[str, Any]) -> str:
    return _sha256_json({key: request.get(key) for key in IDEMPOTENCY_FIELDS})


def _validate_args(args: argparse.Namespace) -> dict[str, Any] | None:
    args.request_id = _require_pattern(args.request_id, REQUEST_ID_RE, "request id")
    args.release_sha = _require_pattern(args.release_sha, GIT_SHA_RE, "release SHA")
    if not str(args.workflow_run_id).isdigit():
        raise OperationBlocked("workflow run id must be numeric")
    if not ACTOR_RE.fullmatch(args.actor):
        raise OperationBlocked("actor is invalid")

    package = None
    if args.operation == "scheduler-import":
        package = _resolve_package(args.package_id, "scheduler")
    elif args.operation == "curriculum-sync":
        package = _resolve_package(args.package_id, "curriculum")
    elif args.package_id:
        raise OperationBlocked("this operation does not accept package_id")

    if args.operation == "verify-release" and args.mode != "dry-run":
        raise OperationBlocked("verify-release only supports dry-run mode")
    if args.operation == "database-upgrade-heads" and args.mode != "apply":
        raise OperationBlocked("database-upgrade-heads only supports apply mode")

    if args.mode == "apply":
        expected_confirmation = f"APPLY_{args.target.upper()}"
        if args.confirmation != expected_confirmation:
            raise OperationBlocked(
                f"apply requires confirmation {expected_confirmation!r}"
            )
        args.backup_sha256 = _require_pattern(
            args.backup_sha256, SHA256_RE, "verified backup SHA-256"
        )

        if args.operation != "database-upgrade-heads":
            if not args.approved_dry_run_id:
                raise OperationBlocked("apply requires an approved dry-run id")
            _validate_approved_dry_run(args, package)

    if args.operation == "course-duplicates" and args.mode == "apply":
        args.expected_database = _require_pattern(
            args.expected_database, DATABASE_NAME_RE, "expected database"
        )
        args.expected_plan_sha256 = _require_pattern(
            args.expected_plan_sha256, SHA256_RE, "expected plan SHA-256"
        )
        for label in ("expected_pairs", "expected_records", "expected_tags"):
            value = getattr(args, label)
            if value is None or value < 0:
                raise OperationBlocked(f"{label.replace('_', ' ')} is required")
    elif any(
        value not in (None, "")
        for value in (
            args.expected_database,
            args.expected_plan_sha256,
            args.expected_pairs,
            args.expected_records,
            args.expected_tags,
        )
    ):
        raise OperationBlocked("reconciliation controls are only accepted for course-duplicates apply")

    return package


def _validate_approved_dry_run(
    args: argparse.Namespace,
    package: dict[str, Any] | None,
) -> None:
    approved = _read_report(args.approved_dry_run_id)
    expected = {
        "operation": args.operation,
        "mode": "dry-run",
        "target": args.target,
        "release_sha": args.release_sha,
        "package_id": package.get("id") if package else None,
        "package_sha256": package.get("sha256") if package else None,
    }
    mismatches = [
        key for key, value in expected.items() if approved.get(key) != value
    ]
    if mismatches:
        raise OperationBlocked(
            "approved dry-run does not match apply request: " + ", ".join(mismatches)
        )
    if approved.get("status") != "dry-run":
        raise OperationBlocked("approved report is not a successful dry-run")

    approved_result = approved.get("result")
    approved_result_sha256 = approved.get("result_sha256")
    if (
        not isinstance(approved_result, dict)
        or not isinstance(approved_result_sha256, str)
        or not SHA256_RE.fullmatch(approved_result_sha256)
        or _sha256_json(approved_result) != approved_result_sha256
    ):
        raise OperationBlocked("approved dry-run result digest is invalid")

    if args.operation == "course-duplicates":
        result = approved.get("result") if isinstance(approved.get("result"), dict) else {}
        plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
        approved_controls = {
            "expected_database": (plan.get("database") or {}).get("name"),
            "expected_plan_sha256": result.get("plan_sha256"),
            "expected_pairs": plan.get("pair_count"),
            "expected_records": plan.get("user_course_record_count"),
            "expected_tags": plan.get("tag_count"),
        }
        requested_controls = {
            key: getattr(args, key) for key in approved_controls
        }
        if approved_controls != requested_controls:
            raise OperationBlocked("reconciliation controls do not match approved dry-run")


def _validate_current_data_plan(
    args: argparse.Namespace,
    package: dict[str, Any],
) -> None:
    approved = _read_report(args.approved_dry_run_id)
    dry_run_args = argparse.Namespace(**vars(args))
    dry_run_args.mode = "dry-run"
    if args.operation == "scheduler-import":
        current_result = _scheduler_operation(dry_run_args, package)
    elif args.operation == "curriculum-sync":
        current_result = _curriculum_operation(dry_run_args, package)
    else:
        raise OperationBlocked("operation does not support data-plan validation")

    serialized_result = _json_value(current_result)
    if (
        _result_status(current_result) != "dry-run"
        or _sha256_json(serialized_result) != approved.get("result_sha256")
    ):
        raise OperationBlocked("current data plan does not match approved dry-run")


@contextmanager
def _database_operation_lock() -> Iterator[None]:
    if db.engine.dialect.name != "postgresql":
        yield
        return
    connection = db.engine.connect()
    try:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": LOCK_KEY})
        yield
    finally:
        try:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
        finally:
            connection.close()


def _scheduler_operation(args: argparse.Namespace, package: dict[str, Any]) -> Any:
    expected = package.get("expected") or {}
    if package.get("semester_id") != "2610":
        raise OperationBlocked("scheduler package is not allowlisted for semester 2610")
    expectations = SnapshotExpectations(
        courses=int(expected["courses"]),
        offered_courses=int(expected["offered_courses"]),
        sections=int(expected["sections"]),
        lectures=int(expected["lectures"]),
    )
    return run_pending_scheduler_update(
        mode=args.mode,
        file_path=package["resolved_path"],
        expected_sha256=package["sha256"],
        expected_counts=expectations,
    )


def _curriculum_operation(args: argparse.Namespace, package: dict[str, Any]) -> Any:
    expected = package.get("expected") or {}
    expectations = CurriculumExpectations(
        program_definitions=int(expected["program_definitions"]),
        program_cohorts=int(expected["program_cohorts"]),
        requirement_groups=int(expected["requirement_groups"]),
        unique_course_codes=int(expected["unique_course_codes"]),
    )
    return run_pending_curriculum_update(
        mode=args.mode,
        file_path=package["resolved_path"],
        expected_sha256=package["sha256"],
        expected_counts=expectations,
    )


def _reconciliation_operation(args: argparse.Namespace) -> dict[str, Any]:
    return run_reconciliation(
        apply=args.mode == "apply",
        expected_database=args.expected_database or None,
        expected_plan_sha256=args.expected_plan_sha256 or None,
        expected_pairs=args.expected_pairs,
        expected_records=args.expected_records,
        expected_tags=args.expected_tags,
    )


def _verify_release() -> dict[str, Any]:
    active_courses = Course.query.filter(
        Course.is_deleted.is_(False),
        Course.is_active.is_(True),
    ).all()
    normalized_counts = Counter(
        normalize_course_code(course.normalized_code or course.code)
        for course in active_courses
        if normalize_course_code(course.normalized_code or course.code)
    )
    duplicate_groups = sorted(
        code for code, count in normalized_counts.items() if count > 1
    )

    offering_ids = [
        offering_id
        for (offering_id,) in db.session.query(CourseOffering.id).filter_by(
            semester_id="2610",
            status="offered",
        )
    ]
    domain_sections = (
        db.session.query(func.count(CourseSection.id))
        .filter(CourseSection.offering_id.in_(offering_ids))
        .scalar()
        if offering_ids
        else 0
    )
    domain_meetings = (
        db.session.query(func.count(CourseMeeting.id))
        .join(CourseSection, CourseMeeting.section_id == CourseSection.id)
        .filter(CourseSection.offering_id.in_(offering_ids))
        .scalar()
        if offering_ids
        else 0
    )
    legacy_sections = SchedulerSection.query.filter_by(semester_id="2610").count()
    legacy_meetings = SchedulerLecture.query.filter_by(semester_id="2610").count()
    tba_rows = (
        CourseSection.query.filter(
            CourseSection.offering_id.in_(offering_ids),
            CourseSection.source_section_id.in_(("6951", "6952")),
        ).count()
        if offering_ids
        else 0
    )
    curriculum_programs = CurriculumProgram.query.filter_by(
        cohort="2026",
        is_active=True,
    ).count()
    curriculum_groups = (
        db.session.query(func.count(CurriculumRequirementGroup.id))
        .join(CurriculumProgram)
        .filter(
            CurriculumProgram.cohort == "2026",
            CurriculumProgram.is_active.is_(True),
        )
        .scalar()
    )

    checks = {
        "offered_courses": len(offering_ids),
        "domain_sections": int(domain_sections or 0),
        "domain_meetings": int(domain_meetings or 0),
        "legacy_sections": int(legacy_sections or 0),
        "legacy_meetings": int(legacy_meetings or 0),
        "curriculum_programs": int(curriculum_programs or 0),
        "curriculum_groups": int(curriculum_groups or 0),
        "duplicate_course_groups": len(duplicate_groups),
        "provenance_only_tba_rows_present": int(tba_rows or 0),
    }
    expected = {
        "offered_courses": 383,
        "domain_sections": 801,
        "domain_meetings": 820,
        "legacy_sections": 801,
        "legacy_meetings": 820,
        "curriculum_programs": 8,
        "curriculum_groups": 32,
        "duplicate_course_groups": 0,
        "provenance_only_tba_rows_present": 0,
    }
    return {
        "status": "verified" if checks == expected else "blocked",
        "checks": checks,
        "expected": expected,
        "duplicate_course_codes": duplicate_groups[:25],
    }


def _database_upgrade_heads() -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "flask", "db", "upgrade", "heads"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "database upgrade failed: " + (completed.stderr or completed.stdout)[-1000:]
        )
    return {
        "status": "applied",
        "message": "database upgraded to committed Alembic heads",
        "output": (completed.stdout or "")[-1000:],
    }


def _run(args: argparse.Namespace, package: dict[str, Any] | None) -> Any:
    app = create_import_app()
    with app.app_context(), _database_operation_lock():
        if args.operation == "database-upgrade-heads":
            return _database_upgrade_heads()
        if args.operation == "verify-release":
            return _verify_release()
        if args.operation == "scheduler-import":
            if args.mode == "apply":
                _validate_current_data_plan(args, package)
            return _scheduler_operation(args, package)
        if args.operation == "curriculum-sync":
            if args.mode == "apply":
                _validate_current_data_plan(args, package)
            return _curriculum_operation(args, package)
        if args.operation == "course-duplicates":
            return _reconciliation_operation(args)
    raise OperationBlocked("operation is not allowlisted")


def _result_status(result: Any) -> str:
    serialized = _json_value(result)
    if isinstance(serialized, dict):
        return str(serialized.get("status") or "failed")
    return "failed"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one allowlisted backend CI operation.")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--operation", required=True, choices=OPERATIONS)
    parser.add_argument("--mode", required=True, choices=("dry-run", "apply"))
    parser.add_argument("--target", required=True, choices=("dev", "production"))
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--package-id")
    parser.add_argument("--approved-dry-run-id")
    parser.add_argument("--expected-database")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--expected-pairs", type=int)
    parser.add_argument("--expected-records", type=int)
    parser.add_argument("--expected-tags", type=int)
    parser.add_argument("--backup-sha256")
    parser.add_argument("--confirmation")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    started_at = _utc_now()
    report: dict[str, Any]
    exit_code = 0
    package = None
    try:
        package = _validate_args(args)
        request = _request_fields(args, package)
        request_sha256 = _request_sha256(request)
        existing_path = _report_path(args.request_id)
        if existing_path.exists():
            existing = _read_report(args.request_id)
            if existing.get("request_sha256") != request_sha256:
                raise OperationBlocked("request id was already used for a different operation")
            print(json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True))
            status = str(existing.get("status") or "failed")
            raise SystemExit(2 if status == "blocked" else 1 if status == "failed" else 0)

        result = _run(args, package)
        status = _result_status(result)
        report = {
            **request,
            "request_sha256": request_sha256,
            "status": status,
            "result": _json_value(result),
            "result_sha256": _sha256_json(result),
            "started_at": started_at,
            "finished_at": _utc_now(),
        }
        if status in {"blocked", "failed"}:
            exit_code = 2 if status == "blocked" else 1
    except OperationBlocked as exc:
        exit_code = 2
        request_id = getattr(args, "request_id", "invalid-request")
        if not REQUEST_ID_RE.fullmatch(str(request_id)):
            request_id = "invalid-request"
        report = {
            "schema_version": 1,
            "request_id": request_id,
            "workflow_run_id": str(getattr(args, "workflow_run_id", "")),
            "operation": getattr(args, "operation", None),
            "mode": getattr(args, "mode", None),
            "target": getattr(args, "target", None),
            "release_sha": getattr(args, "release_sha", None),
            "status": "blocked",
            "error_code": "precondition_failed",
            "message": str(exc)[:1000],
            "started_at": started_at,
            "finished_at": _utc_now(),
        }
    except Exception as exc:
        exit_code = 1
        request_id = getattr(args, "request_id", "invalid-request")
        if not REQUEST_ID_RE.fullmatch(str(request_id)):
            request_id = "invalid-request"
        report = {
            "schema_version": 1,
            "request_id": request_id,
            "workflow_run_id": str(getattr(args, "workflow_run_id", "")),
            "operation": getattr(args, "operation", None),
            "mode": getattr(args, "mode", None),
            "target": getattr(args, "target", None),
            "release_sha": getattr(args, "release_sha", None),
            "status": "failed",
            "error_code": type(exc).__name__,
            "message": str(exc)[:1000],
            "started_at": started_at,
            "finished_at": _utc_now(),
        }

    try:
        _write_report(report)
    except FileExistsError:
        print(
            f"Existing operation report {report['request_id']!r} was preserved.",
            file=sys.stderr,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
