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

from sqlalchemy import func, inspect, text

from app.extensions import db
from app.models.academic_map import CurriculumProgram, CurriculumRequirementGroup
from app.models.course import Course
from app.models.course_domain import (
    CourseCatalogVersion,
    CourseMeeting,
    CourseOffering,
    CourseSection,
)
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_section import SchedulerSection
from app.scripts.import_pending_academic_data import (
    CurriculumExpectations,
    load_curriculum_file,
    run_pending_curriculum_update,
    run_pending_scheduler_update,
)
from app.scripts.import_scheduler_offerings import (
    SnapshotExpectations,
    create_import_app,
    file_sha256,
    load_offerings_file,
)
from app.scripts.reconcile_course_duplicates import run_reconciliation
from app.services.academic_curriculum_sync import curriculum_persisted_projection
from app.services.course_domain import display_course_code, normalize_course_code


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
    "oauth-redirect",
)
TARGET_CONFIRMATIONS = {
    "dev": "APPLY_DEV",
    "production": "APPLY_PRODUCTION",
    "campus": "APPLY_CAMPUS",
}
CAMPUS_OPERATION_ALLOWLIST = frozenset(
    {
        ("verify-release", "dry-run", ""),
        ("scheduler-import", "dry-run", "scheduler-2610-v1"),
        ("scheduler-import", "apply", "scheduler-2610-v1"),
        ("curriculum-sync", "dry-run", "curriculum-2026-v1"),
        ("curriculum-sync", "apply", "curriculum-2026-v1"),
    }
)
MUTATING_OPERATIONS = frozenset(
    {
        "scheduler-import",
        "curriculum-sync",
        "course-duplicates",
        "database-upgrade-heads",
        "oauth-redirect",
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
COURSEPLAN_OAUTH_CLIENT_ID = "PF41TCVh1knwDaRCHXUH"
COURSEPLAN_SCHOOL_REDIRECT_URI = (
    "https://unikorn.hkust-gz.edu.cn/api/auth/campus-forum/callback"
)


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
    if args.target == "campus":
        campus_request = (args.operation, args.mode, args.package_id or "")
        if campus_request not in CAMPUS_OPERATION_ALLOWLIST:
            raise OperationBlocked(
                "operation, mode, and package are not allowlisted for campus"
            )

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
        expected_confirmation = TARGET_CONFIRMATIONS[args.target]
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
    approved_result = approved.get("result")
    if isinstance(approved_result, dict):
        approved_result = {
            key: value
            for key, value in approved_result.items()
            if key not in {"pre_state_sha256", "desired_state_sha256"}
        }
    if (
        _result_status(current_result) != "dry-run"
        or not isinstance(approved_result, dict)
        or _sha256_json(serialized_result) != _sha256_json(approved_result)
    ):
        raise OperationBlocked("current data plan does not match approved dry-run")


def _validate_approved_pre_state(
    args: argparse.Namespace,
    package: dict[str, Any],
) -> None:
    approved = _read_report(args.approved_dry_run_id)
    result = approved.get("result")
    approved_pre_state = (
        result.get("pre_state_sha256") if isinstance(result, dict) else None
    )
    if not isinstance(approved_pre_state, str) or not SHA256_RE.fullmatch(
        approved_pre_state
    ):
        raise OperationBlocked("approved dry-run is missing an exact pre-state digest")
    current_pre_state = _data_pre_state(args.operation, package)
    if current_pre_state["pre_state_sha256"] != approved_pre_state:
        raise OperationBlocked("current data state does not match approved dry-run")


def _sorted_projection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=_canonical_json)


def _scheduler_desired_projection(snapshot: Any, package_sha256: str) -> dict[str, Any]:
    courses = _scheduler_desired_course_projection(snapshot)
    offerings = []
    catalog_versions = []
    domain_sections = []
    legacy_sections = []
    domain_meetings = []
    legacy_meetings = []

    for course in snapshot.courses:
        code = normalize_course_code(course.course_code)
        catalog_versions.append(
            {
                "course_code": code,
                "source": "scheduler_offerings",
                "source_version": snapshot.semester_id,
                "catalog_year": snapshot.semester_id[:2],
                "title": course.course_title,
                "title_abbr": course.course_title_abbr,
                "description": course.course_desc,
                "credits": course.credit,
                "pre_requirement_raw": course.pre_requirement,
                "co_requirement_raw": course.co_requirement,
                "exclusion_raw": course.exclusion,
                "pg_course": course.pg_course,
                "klms_course": course.klms_course,
                "vector": course.vector,
                "effective_from_semester_id": snapshot.semester_id,
            }
        )
        if course.sections:
            offerings.append(
                {
                    "course_code": code,
                    "offering_code": course.course_code,
                    "title_snapshot": course.course_title,
                    "credits_snapshot": course.credit,
                    "source": "scheduler_offerings",
                    "import_hash": package_sha256,
                    "status": "offered",
                    "course_is_active": True,
                    "course_is_deleted": False,
                    "catalog_version_course_code": code,
                    "catalog_version_source": "scheduler_offerings",
                    "catalog_version_source_version": snapshot.semester_id,
                }
            )

        for section in course.sections:
            common_section = {
                "course_code": code,
                "section_id": section.section_id,
                "name": section.name,
                "section_type": section.section_type,
                "bundle": section.bundle,
                "layer": section.layer,
                "quota": section.quota,
                "is_main": section.is_main,
            }
            domain_sections.append(
                {
                    **common_section,
                    "enrol": section.enrol,
                    "avail": section.avail,
                    "wait": section.wait,
                }
            )
            legacy_sections.append(common_section)
            for lecture in section.lectures:
                common_meeting = {
                    "course_code": code,
                    "section_id": section.section_id,
                    "day": lecture.day,
                    "start_time": lecture.start_time,
                    "end_time": lecture.end_time,
                    "room": lecture.room,
                }
                domain_meetings.append(
                    {**common_meeting, "instructor": lecture.instructor}
                )
                legacy_meetings.append(
                    {**common_meeting, "instructor": lecture.instructor}
                )

    return {
        "semester_id": snapshot.semester_id,
        "courses": courses,
        "offerings": _sorted_projection(offerings),
        "catalog_versions": _sorted_projection(catalog_versions),
        "domain_sections": _sorted_projection(domain_sections),
        "domain_meetings": _sorted_projection(domain_meetings),
        "legacy_sections": _sorted_projection(legacy_sections),
        "legacy_meetings": _sorted_projection(legacy_meetings),
        "invalid_archived_offerings": [],
    }


def _scheduler_desired_course_projection(snapshot: Any) -> list[dict[str, Any]]:
    rows = []
    for course in snapshot.courses:
        row = {
            "course_code": normalize_course_code(course.course_code),
            "normalized_code": normalize_course_code(course.course_code),
            "display_code": display_course_code(course.course_code),
            "canonical_title": course.course_title,
            "name": course.course_title,
            "description": course.course_desc,
            "credits": course.credit,
            "subject": course.subject.upper() if course.subject else None,
            "catalog_number": course.catalog_number,
            "course_title_abbr": course.course_title_abbr,
            "pg_course": course.pg_course,
            "klms_course": course.klms_course,
            "vector": course.vector,
            "is_active": True,
            "is_deleted": False,
        }
        for attribute in ("pre_requirement", "co_requirement", "exclusion"):
            value = getattr(course, attribute)
            if value is not None:
                row[attribute] = value
        rows.append(row)
    return _sorted_projection(rows)


def _scheduler_current_course_projection(
    desired: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    desired_by_code = {row["course_code"]: row for row in desired}
    rows = []
    for course in Course.query.all():
        code = normalize_course_code(course.normalized_code or course.code)
        expected = desired_by_code.get(code)
        if expected is None:
            continue
        rows.append(
            {
                key: code if key == "course_code" else getattr(course, key)
                for key in expected
            }
        )
    return _sorted_projection(rows)


def _scheduler_current_projection(
    semester_id: str,
    desired_courses: list[dict[str, Any]],
) -> dict[str, Any]:
    active_offerings = (
        CourseOffering.query.join(Course, CourseOffering.course_id == Course.id)
        .filter(
            CourseOffering.semester_id == semester_id,
            CourseOffering.status != "archived",
        )
        .all()
    )
    offerings = [
        {
            "course_code": normalize_course_code(offering.course.normalized_code or offering.course.code),
            "offering_code": offering.offering_code,
            "title_snapshot": offering.title_snapshot,
            "credits_snapshot": offering.credits_snapshot,
            "source": offering.source,
            "import_hash": offering.import_hash,
            "status": offering.status,
            "course_is_active": offering.course.is_active,
            "course_is_deleted": offering.course.is_deleted,
            "catalog_version_course_code": (
                normalize_course_code(
                    offering.catalog_version.course.normalized_code
                    or offering.catalog_version.course.code
                )
                if offering.catalog_version is not None
                else None
            ),
            "catalog_version_source": (
                offering.catalog_version.source
                if offering.catalog_version is not None
                else None
            ),
            "catalog_version_source_version": (
                offering.catalog_version.source_version
                if offering.catalog_version is not None
                else None
            ),
        }
        for offering in active_offerings
    ]

    catalog_versions = [
        {
            "course_code": normalize_course_code(version.course.normalized_code or version.course.code),
            "source": version.source,
            "source_version": version.source_version,
            "catalog_year": version.catalog_year,
            "title": version.title,
            "title_abbr": version.title_abbr,
            "description": version.description,
            "credits": version.credits,
            "pre_requirement_raw": version.pre_requirement_raw,
            "co_requirement_raw": version.co_requirement_raw,
            "exclusion_raw": version.exclusion_raw,
            "pg_course": version.pg_course,
            "klms_course": version.klms_course,
            "vector": version.vector,
            "effective_from_semester_id": version.effective_from_semester_id,
        }
        for version in (
            db.session.query(CourseCatalogVersion)
            .join(Course)
            .filter(
                CourseCatalogVersion.source == "scheduler_offerings",
                CourseCatalogVersion.source_version == semester_id,
            )
            .all()
        )
    ]

    domain_sections = [
        {
            "course_code": normalize_course_code(section.offering.course.normalized_code or section.offering.course.code),
            "section_id": section.source_section_id,
            "name": section.name,
            "section_type": section.section_type,
            "bundle": section.bundle,
            "layer": section.layer,
            "quota": section.quota,
            "is_main": section.is_main,
            "enrol": section.enrol,
            "avail": section.avail,
            "wait": section.wait,
        }
        for section in (
            CourseSection.query.join(CourseOffering)
            .filter(
                CourseOffering.semester_id == semester_id,
                CourseOffering.status != "archived",
            )
            .all()
        )
    ]
    domain_meetings = [
        {
            "course_code": normalize_course_code(meeting.section.offering.course.normalized_code or meeting.section.offering.course.code),
            "section_id": meeting.section.source_section_id,
            "day": meeting.day,
            "start_time": meeting.start_time,
            "end_time": meeting.end_time,
            "room": meeting.room,
            "instructor": meeting.instructor_text,
        }
        for meeting in (
            CourseMeeting.query.join(CourseSection).join(CourseOffering)
            .filter(
                CourseOffering.semester_id == semester_id,
                CourseOffering.status != "archived",
            )
            .all()
        )
    ]
    legacy_sections = [
        {
            "course_code": normalize_course_code(section.course.normalized_code or section.course.code),
            "section_id": section.section_id,
            "name": section.name,
            "section_type": section.section_type,
            "bundle": section.bundle,
            "layer": section.layer,
            "quota": section.quota,
            "is_main": section.is_main,
        }
        for section in SchedulerSection.query.filter_by(semester_id=semester_id).all()
    ]
    legacy_by_section = {
        section.section_id: normalize_course_code(section.course.normalized_code or section.course.code)
        for section in SchedulerSection.query.filter_by(semester_id=semester_id).all()
    }
    legacy_meetings = [
        {
            "course_code": legacy_by_section.get(lecture.section_id, ""),
            "section_id": lecture.section_id,
            "day": lecture.day,
            "start_time": lecture.start_time,
            "end_time": lecture.end_time,
            "room": lecture.room,
            "instructor": lecture.instructor,
        }
        for lecture in SchedulerLecture.query.filter_by(semester_id=semester_id).all()
    ]
    invalid_archived_offerings = [
        normalize_course_code(offering.course.normalized_code or offering.course.code)
        for offering in CourseOffering.query.filter_by(
            semester_id=semester_id, status="archived"
        ).all()
        if CourseSection.query.filter_by(offering_id=offering.id).count()
    ]
    return {
        "semester_id": semester_id,
        "courses": _scheduler_current_course_projection(desired_courses),
        "offerings": _sorted_projection(offerings),
        "catalog_versions": _sorted_projection(catalog_versions),
        "domain_sections": _sorted_projection(domain_sections),
        "domain_meetings": _sorted_projection(domain_meetings),
        "legacy_sections": _sorted_projection(legacy_sections),
        "legacy_meetings": _sorted_projection(legacy_meetings),
        "invalid_archived_offerings": sorted(invalid_archived_offerings),
    }


def _curriculum_current_projection(desired: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current = []
    for expected in desired:
        program = CurriculumProgram.query.filter_by(
            code=expected["code"], cohort=expected["cohort"]
        ).one_or_none()
        if program is None:
            continue
        groups = [
            {
                "key": group.key,
                "name_en": group.name_en,
                "name_zh": group.name_zh,
                "category": group.category,
                "min_credits": group.min_credits,
                "min_courses": group.min_courses,
                "rule": group.rule or {},
                "sort_order": group.sort_order,
            }
            for group in CurriculumRequirementGroup.query.filter_by(
                program_id=program.id
            ).all()
        ]
        current.append(
            {
                "code": program.code,
                "cohort": program.cohort,
                "name_en": program.name_en,
                "name_zh": program.name_zh,
                "total_min_credits": program.total_min_credits,
                "common_core_min_credits": program.common_core_min_credits,
                "major_min_credits": program.major_min_credits,
                "home_areas": program.home_areas or [],
                "is_active": program.is_active,
                "requirement_groups": sorted(groups, key=lambda group: group["key"]),
            }
        )
    return sorted(current, key=lambda program: (program["code"], program["cohort"]))


def _scheduler_import_run(package_sha256: str) -> dict[str, Any] | None:
    if not inspect(db.engine).has_table("scheduler_offering_import_runs"):
        return None
    row = db.session.execute(
        text(
            """
            SELECT semester_id, status
            FROM scheduler_offering_import_runs
            WHERE import_hash = :import_hash
            """
        ),
        {"import_hash": package_sha256},
    ).mappings().first()
    return dict(row) if row else None


def _data_postcondition(
    operation: str,
    package: dict[str, Any],
) -> dict[str, Any]:
    try:
        actual_sha256 = file_sha256(package["resolved_path"])
    except OSError as exc:
        raise OperationBlocked("committed package file is unreadable") from exc
    if actual_sha256 != package["sha256"]:
        raise OperationBlocked(
            f"committed package SHA-256 mismatch: {actual_sha256} != {package['sha256']}"
        )

    expected = package.get("expected") or {}
    if operation == "scheduler-import":
        expectations = SnapshotExpectations(
            courses=int(expected["courses"]),
            offered_courses=int(expected["offered_courses"]),
            sections=int(expected["sections"]),
            lectures=int(expected["lectures"]),
        )
        snapshot = load_offerings_file(
            package["resolved_path"], package.get("semester_id")
        )
        actual_counts = {
            "courses": len(snapshot.courses),
            "offered_courses": sum(bool(course.sections) for course in snapshot.courses),
            "sections": sum(len(course.sections) for course in snapshot.courses),
            "lectures": sum(
                len(section.lectures)
                for course in snapshot.courses
                for section in course.sections
            ),
        }
        reviewed_counts = asdict(expectations)
        if actual_counts != reviewed_counts:
            raise OperationBlocked(
                "scheduler package no longer matches reviewed exact counts"
            )
        desired = _scheduler_desired_projection(snapshot, package["sha256"])
        current = _scheduler_current_projection(
            snapshot.semester_id,
            desired["courses"],
        )
        ledger = _scheduler_import_run(package["sha256"])
        mismatches = [
            key
            for key in desired
            if _canonical_json(desired[key]) != _canonical_json(current[key])
        ]
        return {
            "matches": not mismatches,
            "desired_state_sha256": _sha256_json(desired),
            "current_state_sha256": _sha256_json(current),
            "mismatched_components": mismatches,
            "ledger_status": (ledger or {}).get("status"),
            "ledger_semester_id": (ledger or {}).get("semester_id"),
        }

    if operation == "curriculum-sync":
        expectations = CurriculumExpectations(
            program_definitions=int(expected["program_definitions"]),
            program_cohorts=int(expected["program_cohorts"]),
            requirement_groups=int(expected["requirement_groups"]),
            unique_course_codes=int(expected["unique_course_codes"]),
        )
        snapshot = load_curriculum_file(package["resolved_path"], expectations)
        desired = curriculum_persisted_projection(snapshot.payload)
        current = _curriculum_current_projection(desired)
        return {
            "matches": _canonical_json(desired) == _canonical_json(current),
            "desired_state_sha256": _sha256_json(desired),
            "current_state_sha256": _sha256_json(current),
            "expected_programs": len(desired),
            "current_programs": len(current),
        }

    raise OperationBlocked("operation does not have data postconditions")


def _data_pre_state(operation: str, package: dict[str, Any]) -> dict[str, str]:
    postcondition = _data_postcondition(operation, package)
    if operation == "scheduler-import":
        state = {"package_owned": postcondition["current_state_sha256"]}
    else:
        state = {"package_owned": postcondition["current_state_sha256"]}
    return {"pre_state_sha256": _sha256_json(state)}


def _with_data_state(result: Any, operation: str, package: dict[str, Any]) -> dict[str, Any]:
    serialized = _json_value(result)
    if not isinstance(serialized, dict):
        raise OperationBlocked("data operation returned a malformed result")
    postcondition = _data_postcondition(operation, package)
    return {
        **serialized,
        **_data_pre_state(operation, package),
        "desired_state_sha256": postcondition["desired_state_sha256"],
    }


def _already_applied_result(
    operation: str,
    package: dict[str, Any],
    postcondition: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "already-applied",
        "mode": "apply",
        "message": (
            f"{operation} package is already present; exact package-owned "
            "postconditions were verified."
        ),
        "package_id": package.get("id"),
        "package_sha256": package["sha256"],
        "postcondition": postcondition,
    }


def _run_data_apply(
    args: argparse.Namespace,
    package: dict[str, Any],
    operation: Any,
) -> Any:
    before = _data_postcondition(args.operation, package)
    if before["matches"]:
        return _already_applied_result(args.operation, package, before)

    if args.operation == "scheduler-import" and before.get("ledger_status") in {
        "applied",
        "running",
    }:
        raise OperationBlocked(
            "scheduler import ledger exists but exact package postconditions do not match"
        )

    _validate_current_data_plan(args, package)
    _validate_approved_pre_state(args, package)
    result = operation(args, package)
    if _result_status(result) == "skipped":
        raise OperationBlocked(
            "data importer returned skipped without verified exact postconditions"
        )
    if _result_status(result) != "applied":
        return result

    after = _data_postcondition(args.operation, package)
    if not after["matches"]:
        raise OperationBlocked(
            "data importer completed without satisfying exact package postconditions"
        )
    return {
        **_json_value(result),
        "postcondition": after,
    }


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


def _courseplan_oauth_state(*, for_update: bool = False) -> dict[str, Any]:
    lock_clause = (
        " FOR UPDATE"
        if for_update and db.engine.dialect.name == "postgresql"
        else ""
    )
    rows = db.session.execute(
        text(
            "SELECT id, client_id, redirect_uris, is_active "
            "FROM oauth_clients WHERE client_id = :client_id" + lock_clause
        ),
        {"client_id": COURSEPLAN_OAUTH_CLIENT_ID},
    ).mappings().all()
    if len(rows) != 1:
        raise OperationBlocked(
            "expected exactly one active CoursePlan OAuth client; "
            f"found {len(rows)}"
        )

    row = rows[0]
    if not row["is_active"]:
        raise OperationBlocked("CoursePlan OAuth client is inactive")
    try:
        redirect_uris = json.loads(row["redirect_uris"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise OperationBlocked("CoursePlan OAuth redirect URI JSON is invalid") from exc
    if (
        not isinstance(redirect_uris, list)
        or any(not isinstance(uri, str) or not uri for uri in redirect_uris)
        or len(set(redirect_uris)) != len(redirect_uris)
    ):
        raise OperationBlocked(
            "CoursePlan OAuth redirect URIs must be a unique JSON string array"
        )

    state = {
        "client_id": row["client_id"],
        "is_active": bool(row["is_active"]),
        "redirect_uris": redirect_uris,
    }
    return {
        "row_id": row["id"],
        "redirect_uris": redirect_uris,
        "pre_state_sha256": _sha256_json(state),
    }


def _oauth_redirect_operation(args: argparse.Namespace) -> dict[str, Any]:
    state = _courseplan_oauth_state(for_update=args.mode == "apply")
    current_uris = state["redirect_uris"]
    already_present = COURSEPLAN_SCHOOL_REDIRECT_URI in current_uris
    plan = {
        "client_id": COURSEPLAN_OAUTH_CLIENT_ID,
        "redirect_uri": COURSEPLAN_SCHOOL_REDIRECT_URI,
        "existing_redirect_count": len(current_uris),
        "already_present": already_present,
        "pre_state_sha256": state["pre_state_sha256"],
    }
    if args.mode == "dry-run":
        return {"status": "dry-run", **plan}

    approved = _read_report(args.approved_dry_run_id)
    approved_result = approved.get("result")
    if (
        not isinstance(approved_result, dict)
        or approved_result.get("pre_state_sha256") != state["pre_state_sha256"]
    ):
        raise OperationBlocked(
            "current OAuth client state does not match the approved dry-run"
        )
    if already_present:
        return {"status": "already-applied", **plan}

    updated_uris = [*current_uris, COURSEPLAN_SCHOOL_REDIRECT_URI]
    result = db.session.execute(
        text(
            "UPDATE oauth_clients "
            "SET redirect_uris = :redirect_uris, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = :row_id AND client_id = :client_id AND is_active = true"
        ),
        {
            "redirect_uris": json.dumps(updated_uris, ensure_ascii=False),
            "row_id": state["row_id"],
            "client_id": COURSEPLAN_OAUTH_CLIENT_ID,
        },
    )
    if result.rowcount != 1:
        db.session.rollback()
        raise OperationBlocked("CoursePlan OAuth client changed during the update")
    db.session.commit()

    verified = _courseplan_oauth_state()
    if (
        verified["redirect_uris"] != updated_uris
        or COURSEPLAN_SCHOOL_REDIRECT_URI not in verified["redirect_uris"]
    ):
        raise OperationBlocked(
            "OAuth redirect update failed exact postcondition verification"
        )
    return {
        "status": "applied",
        **plan,
        "updated_redirect_count": len(updated_uris),
        "post_state_sha256": verified["pre_state_sha256"],
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
                return _run_data_apply(args, package, _scheduler_operation)
            result = _scheduler_operation(args, package)
            if _result_status(result) == "dry-run":
                return _with_data_state(result, args.operation, package)
            return result
        if args.operation == "curriculum-sync":
            if args.mode == "apply":
                return _run_data_apply(args, package, _curriculum_operation)
            result = _curriculum_operation(args, package)
            if _result_status(result) == "dry-run":
                return _with_data_state(result, args.operation, package)
            return result
        if args.operation == "course-duplicates":
            return _reconciliation_operation(args)
        if args.operation == "oauth-redirect":
            return _oauth_redirect_operation(args)
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
    parser.add_argument(
        "--target", required=True, choices=tuple(TARGET_CONFIRMATIONS)
    )
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
