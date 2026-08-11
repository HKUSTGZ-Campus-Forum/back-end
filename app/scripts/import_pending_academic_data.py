"""Validate or explicitly apply pending scheduler and curriculum snapshots.

This module is intentionally not imported by application startup.  It is a
manual operator tool for data packages that have been collected and reviewed
but are not ready to become bundled, automatically applied application data.

Both subcommands default to a database-backed dry-run.  Applying a package
requires ``--apply`` plus an independently recorded SHA-256 digest and exact
control totals.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.extensions import db
from app.models.academic_map import CurriculumProgram, CurriculumRequirementGroup
from app.scripts.import_scheduler_offerings import (
    DeployOfferingResult,
    SnapshotExpectations,
    create_import_app,
    file_sha256,
    run_deploy_scheduler_offering_update,
)
from app.services.academic_curriculum_sync import (
    MAJOR_CODE_ALIASES,
    sync_curriculum_requirements_from_payload,
)


PENDING_SCHEDULER_SEMESTER_ID = "2610"
PENDING_SCHEDULER_START_DATE = "2026-09-01"
PENDING_SCHEDULER_SUBJECTS = {
    "AIAA", "AMAT", "BSBE", "CMAA", "CNCC", "CNGF", "DLED", "DSAA",
    "EOAS", "FTEC", "FUNH", "INFH", "INTR", "IOTA", "IPEN", "LANG",
    "MICS", "MSSM", "PDEV", "PLED", "ROAS", "SEEN", "SMMG", "SOCH",
    "SYSH", "UCMP", "UCUG", "UFUG", "UGOD",
}
COURSE_CODE_RE = re.compile(r"^[A-Z0-9]{4,16}$")
COHORT_RE = re.compile(r"^\d{4}$")
COURSE_LIST_KEYS = {"courses", "required_courses", "choices", "electives"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PendingAcademicDataValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CurriculumExpectations:
    program_definitions: int
    program_cohorts: int
    requirement_groups: int
    unique_course_codes: int


@dataclass(frozen=True)
class CurriculumSnapshot:
    payload: dict[str, Any]
    counts: CurriculumExpectations
    program_groups: dict[tuple[str, str], set[str]]


@dataclass(frozen=True)
class CurriculumImportPlan:
    program_rows_to_insert: int
    program_rows_to_update: int
    group_rows_to_insert: int
    group_rows_to_update: int
    omitted_group_keys: list[str]


@dataclass(frozen=True)
class PendingCurriculumResult:
    status: str
    mode: str
    message: str
    import_hash: str
    counts: CurriculumExpectations | None = None
    plan: CurriculumImportPlan | None = None


def _validate_pending_scheduler_metadata(file_path: Path) -> None:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PendingAcademicDataValidationError(
            f"unable to read pending scheduler JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise PendingAcademicDataValidationError(
            "pending scheduler top level JSON must be an object"
        )
    if payload.get("semester_start_date") != PENDING_SCHEDULER_START_DATE:
        raise PendingAcademicDataValidationError(
            "pending scheduler semester_start_date must be 2026-09-01"
        )

    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise PendingAcademicDataValidationError(
            "pending scheduler provenance must be an object"
        )
    _clean_string(provenance.get("source_name"), "provenance.source_name")

    term_url = _clean_string(provenance.get("term_url"), "provenance.term_url")
    parsed_term_url = urlparse(term_url)
    if (
        parsed_term_url.scheme != "https"
        or parsed_term_url.hostname != "w5.hkust-gz.edu.cn"
        or not parsed_term_url.path.rstrip("/").endswith("/wcq/cgi-bin/2610")
    ):
        raise PendingAcademicDataValidationError(
            "provenance.term_url must be the official HKUST-GZ WCQ 2610 URL"
        )

    retrieved_at = _clean_string(
        provenance.get("retrieved_at"), "provenance.retrieved_at"
    )
    try:
        parsed_retrieved_at = dt.datetime.fromisoformat(
            retrieved_at.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise PendingAcademicDataValidationError(
            "provenance.retrieved_at must be an ISO-8601 timestamp"
        ) from exc
    if parsed_retrieved_at.tzinfo is None:
        raise PendingAcademicDataValidationError(
            "provenance.retrieved_at must include a timezone"
        )

    subjects = provenance.get("subjects")
    if not isinstance(subjects, list):
        raise PendingAcademicDataValidationError(
            "provenance.subjects must be a list"
        )
    subject_codes: set[str] = set()
    for index, subject in enumerate(subjects):
        context = f"provenance.subjects[{index}]"
        if not isinstance(subject, dict):
            raise PendingAcademicDataValidationError(f"{context}: expected an object")
        code = _clean_string(subject.get("code"), f"{context}.code").upper()
        if code in subject_codes:
            raise PendingAcademicDataValidationError(
                f"{context}: duplicate subject code {code}"
            )
        subject_codes.add(code)
        source_url = _clean_string(subject.get("url"), f"{context}.url")
        parsed_source_url = urlparse(source_url)
        query = parse_qs(parsed_source_url.query)
        if (
            parsed_source_url.scheme != "https"
            or parsed_source_url.hostname != "w5.hkust-gz.edu.cn"
            or not parsed_source_url.path.endswith("/wcq/cgi-bin/index.php")
            or query.get("term") != [PENDING_SCHEDULER_SEMESTER_ID]
            or query.get("subject") != [code]
        ):
            raise PendingAcademicDataValidationError(
                f"{context}.url: expected the official WCQ query URL for {code}"
            )
    if subject_codes != PENDING_SCHEDULER_SUBJECTS:
        missing = sorted(PENDING_SCHEDULER_SUBJECTS - subject_codes)
        unexpected = sorted(subject_codes - PENDING_SCHEDULER_SUBJECTS)
        raise PendingAcademicDataValidationError(
            "pending scheduler provenance does not cover the reviewed 2610 subjects"
            + (f"; missing={','.join(missing)}" if missing else "")
            + (f"; unexpected={','.join(unexpected)}" if unexpected else "")
        )


def _clean_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PendingAcademicDataValidationError(
            f"{context}: expected a non-empty string"
        )
    return value.strip()


def _program_code(value: Any, context: str) -> str:
    code = _clean_string(value, context).replace(" ", "").upper()
    code = MAJOR_CODE_ALIASES.get(code, code)
    if not re.fullmatch(r"[A-Z0-9]{2,32}", code):
        raise PendingAcademicDataValidationError(
            f"{context}: invalid program code {code!r}"
        )
    return code


def _reviewed_non_negative_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PendingAcademicDataValidationError(
            f"{context}: expected a non-negative integer"
        )
    return value


def _optional_non_negative_int(value: Any, context: str) -> None:
    if value is None:
        return
    _reviewed_non_negative_int(value, context)


def _reviewed_positive_int(value: Any, context: str) -> int:
    parsed = _reviewed_non_negative_int(value, context)
    if parsed == 0:
        raise PendingAcademicDataValidationError(
            f"{context}: expected a positive integer"
        )
    return parsed


def _validate_program_metadata(program: dict[str, Any], context: str) -> None:
    home_areas = program.get("home_areas")
    if not isinstance(home_areas, list):
        raise PendingAcademicDataValidationError(
            f"{context}.home_areas: expected a list"
        )
    normalized_areas = [
        _clean_string(value, f"{context}.home_areas[{index}]")
        for index, value in enumerate(home_areas)
    ]
    if len(normalized_areas) != len(set(normalized_areas)):
        raise PendingAcademicDataValidationError(
            f"{context}.home_areas: duplicate values"
        )

    if "is_active" in program and not isinstance(program["is_active"], bool):
        raise PendingAcademicDataValidationError(
            f"{context}.is_active: expected a boolean"
        )

    digest = _clean_string(
        program.get("source_pdf_sha256"),
        f"{context}.source_pdf_sha256",
    ).lower()
    if not SHA256_RE.fullmatch(digest):
        raise PendingAcademicDataValidationError(
            f"{context}.source_pdf_sha256: expected 64 lowercase hex characters"
        )

    retrieved_at = _clean_string(
        program.get("source_retrieved_at"),
        f"{context}.source_retrieved_at",
    )
    try:
        dt.date.fromisoformat(retrieved_at)
    except ValueError as exc:
        raise PendingAcademicDataValidationError(
            f"{context}.source_retrieved_at: expected an ISO date"
        ) from exc


def _official_source_urls(program: dict[str, Any], context: str) -> list[str]:
    raw_urls: list[Any] = []
    if "source_url" in program:
        raw_urls.append(program["source_url"])
    if "source_urls" in program:
        if not isinstance(program["source_urls"], list):
            raise PendingAcademicDataValidationError(
                f"{context}.source_urls: expected a list"
            )
        raw_urls.extend(program["source_urls"])
    if not raw_urls:
        raise PendingAcademicDataValidationError(
            f"{context}: at least one official source_url is required"
        )

    urls = []
    for index, value in enumerate(raw_urls):
        url = _clean_string(value, f"{context}.source_urls[{index}]")
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "hkust-gz.edu.cn" or hostname.endswith(".hkust-gz.edu.cn")
        ):
            raise PendingAcademicDataValidationError(
                f"{context}.source_urls[{index}]: expected an official HTTPS hkust-gz.edu.cn URL"
            )
        urls.append(url)
    return urls


def _cohorts(program: dict[str, Any], context: str) -> list[str]:
    has_cohort = bool(str(program.get("cohort") or "").strip())
    has_cohorts = "cohorts" in program
    if has_cohort and has_cohorts:
        raise PendingAcademicDataValidationError(
            f"{context}: use either cohort or cohorts, not both"
        )

    values: Any
    if has_cohort:
        values = [program["cohort"]]
    else:
        values = program.get("cohorts")
    if not isinstance(values, list) or not values:
        raise PendingAcademicDataValidationError(
            f"{context}: at least one cohort is required"
        )

    cohorts = [
        _clean_string(value, f"{context}.cohorts[{index}]")
        for index, value in enumerate(values)
    ]
    for cohort in cohorts:
        if not COHORT_RE.fullmatch(cohort):
            raise PendingAcademicDataValidationError(
                f"{context}: invalid cohort {cohort!r}; expected YYYY"
            )
    if len(cohorts) != len(set(cohorts)):
        raise PendingAcademicDataValidationError(
            f"{context}: duplicate cohort values"
        )
    return cohorts


def _validate_rule(
    value: Any,
    context: str,
    *,
    course_codes: set[str],
) -> None:
    if not isinstance(value, dict):
        raise PendingAcademicDataValidationError(f"{context}: expected an object")

    for key, raw in value.items():
        child_context = f"{context}.{key}"
        if key in COURSE_LIST_KEYS:
            if not isinstance(raw, list):
                raise PendingAcademicDataValidationError(
                    f"{child_context}: expected a list"
                )
            normalized_codes = []
            for index, item in enumerate(raw):
                code = _clean_string(item, f"{child_context}[{index}]").replace(
                    " ", ""
                ).upper()
                if not COURSE_CODE_RE.fullmatch(code):
                    raise PendingAcademicDataValidationError(
                        f"{child_context}[{index}]: invalid course code {code!r}"
                    )
                normalized_codes.append(code)
                course_codes.add(code)
            if len(normalized_codes) != len(set(normalized_codes)):
                raise PendingAcademicDataValidationError(
                    f"{child_context}: duplicate course codes"
                )
        elif key in {"min_credits", "min_courses", "max_credits", "max_courses"}:
            _optional_non_negative_int(raw, child_context)
        elif isinstance(raw, dict):
            _validate_rule(raw, child_context, course_codes=course_codes)
        elif isinstance(raw, list):
            for index, item in enumerate(raw):
                if isinstance(item, dict):
                    _validate_rule(
                        item,
                        f"{child_context}[{index}]",
                        course_codes=course_codes,
                    )


def _validate_curriculum_counts(
    actual: CurriculumExpectations,
    expected: CurriculumExpectations | None,
) -> None:
    if expected is None:
        raise PendingAcademicDataValidationError(
            "independently reviewed curriculum counts are required"
        )
    for field_name in (
        "program_definitions",
        "program_cohorts",
        "requirement_groups",
        "unique_course_codes",
    ):
        _reviewed_non_negative_int(
            getattr(expected, field_name),
            f"expected {field_name}",
        )
    mismatches = [
        f"{field_name}={getattr(actual, field_name)} "
        f"(expected {getattr(expected, field_name)})"
        for field_name in (
            "program_definitions",
            "program_cohorts",
            "requirement_groups",
            "unique_course_codes",
        )
        if getattr(actual, field_name) != getattr(expected, field_name)
    ]
    if mismatches:
        raise PendingAcademicDataValidationError(
            "curriculum snapshot does not match independently reviewed counts: "
            + "; ".join(mismatches)
        )


def validate_curriculum_payload(
    payload: Any,
    expected_counts: CurriculumExpectations | None,
) -> CurriculumSnapshot:
    if not isinstance(payload, dict):
        raise PendingAcademicDataValidationError("top level JSON must be an object")
    programs = payload.get("programs")
    if not isinstance(programs, list) or not programs:
        raise PendingAcademicDataValidationError(
            "top level programs must be a non-empty list"
        )

    program_groups: dict[tuple[str, str], set[str]] = {}
    course_codes: set[str] = set()
    expanded_group_count = 0
    for program_index, item in enumerate(programs):
        context = f"programs[{program_index}]"
        if not isinstance(item, dict):
            raise PendingAcademicDataValidationError(f"{context}: expected an object")
        code = _program_code(item.get("code"), f"{context}.code")
        _clean_string(item.get("name_en"), f"{context}.name_en")
        _official_source_urls(item, context)
        _validate_program_metadata(item, context)
        cohorts = _cohorts(item, context)
        for field_name in (
            "total_min_credits",
            "common_core_min_credits",
            "major_min_credits",
        ):
            _reviewed_positive_int(item.get(field_name), f"{context}.{field_name}")

        groups = item.get("requirement_groups")
        if not isinstance(groups, list) or not groups:
            raise PendingAcademicDataValidationError(
                f"{context}.requirement_groups: expected a non-empty list"
            )
        group_keys: set[str] = set()
        for group_index, group in enumerate(groups):
            group_context = f"{context}.requirement_groups[{group_index}]"
            if not isinstance(group, dict):
                raise PendingAcademicDataValidationError(
                    f"{group_context}: expected an object"
                )
            key = _clean_string(group.get("key"), f"{group_context}.key")
            if key in group_keys:
                raise PendingAcademicDataValidationError(
                    f"{group_context}: duplicate requirement group key {key!r}"
                )
            group_keys.add(key)
            _clean_string(group.get("name_en"), f"{group_context}.name_en")
            _clean_string(group.get("category"), f"{group_context}.category")
            _optional_non_negative_int(
                group.get("min_credits"), f"{group_context}.min_credits"
            )
            _optional_non_negative_int(
                group.get("min_courses"), f"{group_context}.min_courses"
            )
            _optional_non_negative_int(
                group.get("sort_order"), f"{group_context}.sort_order"
            )
            _validate_rule(
                group.get("rule"),
                f"{group_context}.rule",
                course_codes=course_codes,
            )

        for cohort in cohorts:
            program_key = (code, cohort)
            if program_key in program_groups:
                raise PendingAcademicDataValidationError(
                    f"{context}: duplicate canonical program/cohort {code}/{cohort}"
                )
            program_groups[program_key] = group_keys
            expanded_group_count += len(group_keys)

    counts = CurriculumExpectations(
        program_definitions=len(programs),
        program_cohorts=len(program_groups),
        requirement_groups=expanded_group_count,
        unique_course_codes=len(course_codes),
    )
    _validate_curriculum_counts(counts, expected_counts)
    return CurriculumSnapshot(
        payload=payload,
        counts=counts,
        program_groups=program_groups,
    )


def load_curriculum_file(
    file_path: Path,
    expected_counts: CurriculumExpectations | None,
) -> CurriculumSnapshot:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PendingAcademicDataValidationError(
            f"unable to read curriculum JSON: {exc}"
        ) from exc
    return validate_curriculum_payload(payload, expected_counts)


def build_curriculum_import_plan(snapshot: CurriculumSnapshot) -> CurriculumImportPlan:
    inserts = 0
    updates = 0
    group_inserts = 0
    group_updates = 0
    omitted_group_keys: list[str] = []

    for (code, cohort), incoming_keys in snapshot.program_groups.items():
        program = CurriculumProgram.query.filter_by(code=code, cohort=cohort).first()
        if program is None:
            inserts += 1
            group_inserts += len(incoming_keys)
            continue

        updates += 1
        existing_keys = {
            key
            for (key,) in (
                db.session.query(CurriculumRequirementGroup.key)
                .filter_by(program_id=program.id)
                .all()
            )
        }
        group_inserts += len(incoming_keys - existing_keys)
        group_updates += len(incoming_keys & existing_keys)
        omitted_group_keys.extend(
            f"{code}/{cohort}/{key}" for key in sorted(existing_keys - incoming_keys)
        )

    return CurriculumImportPlan(
        program_rows_to_insert=inserts,
        program_rows_to_update=updates,
        group_rows_to_insert=group_inserts,
        group_rows_to_update=group_updates,
        omitted_group_keys=sorted(omitted_group_keys),
    )


def run_pending_curriculum_update(
    *,
    mode: str,
    file_path: Path,
    expected_sha256: str,
    expected_counts: CurriculumExpectations | None,
) -> PendingCurriculumResult:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"dry-run", "apply"}:
        return PendingCurriculumResult(
            status="blocked",
            mode=normalized_mode,
            message=f"Unsupported pending curriculum mode: {mode!r}",
            import_hash="",
        )

    resolved_path = file_path.resolve()
    try:
        actual_hash = file_sha256(resolved_path)
    except OSError as exc:
        return PendingCurriculumResult(
            status="blocked",
            mode=normalized_mode,
            message=f"Unable to hash pending curriculum JSON: {exc}",
            import_hash="",
        )
    if actual_hash != expected_sha256:
        return PendingCurriculumResult(
            status="blocked",
            mode=normalized_mode,
            message=(
                "Pending curriculum JSON hash mismatch: "
                f"{actual_hash} != {expected_sha256}"
            ),
            import_hash=actual_hash,
        )

    try:
        snapshot = load_curriculum_file(resolved_path, expected_counts)
        plan = build_curriculum_import_plan(snapshot)
    except PendingAcademicDataValidationError as exc:
        db.session.rollback()
        return PendingCurriculumResult(
            status="blocked",
            mode=normalized_mode,
            message=str(exc),
            import_hash=actual_hash,
        )

    if plan.omitted_group_keys:
        db.session.rollback()
        return PendingCurriculumResult(
            status="blocked",
            mode=normalized_mode,
            message=(
                "Curriculum snapshot omits existing requirement groups; refusing "
                "destructive replacement: " + ", ".join(plan.omitted_group_keys)
            ),
            import_hash=actual_hash,
            counts=snapshot.counts,
            plan=plan,
        )

    if normalized_mode == "dry-run":
        db.session.rollback()
        return PendingCurriculumResult(
            status="dry-run",
            mode=normalized_mode,
            message="Pending curriculum dry-run completed; no database changes were made.",
            import_hash=actual_hash,
            counts=snapshot.counts,
            plan=plan,
        )

    sync_curriculum_requirements_from_payload(snapshot.payload)
    return PendingCurriculumResult(
        status="applied",
        mode=normalized_mode,
        message="Pending curriculum import applied successfully.",
        import_hash=actual_hash,
        counts=snapshot.counts,
        plan=plan,
    )


def run_pending_scheduler_update(
    *,
    mode: str,
    file_path: Path,
    expected_sha256: str,
    expected_counts: SnapshotExpectations,
) -> DeployOfferingResult:
    resolved_path = file_path.resolve()
    try:
        _validate_pending_scheduler_metadata(resolved_path)
    except PendingAcademicDataValidationError as exc:
        return DeployOfferingResult(
            status="blocked",
            mode=mode.strip().lower(),
            message=str(exc),
            import_hash=file_sha256(resolved_path) if resolved_path.is_file() else None,
        )
    return run_deploy_scheduler_offering_update(
        mode=mode,
        file_path=resolved_path,
        expected_semester_id=PENDING_SCHEDULER_SEMESTER_ID,
        expected_sha256=expected_sha256,
        expected_counts=expected_counts,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or explicitly apply reviewed pending academic data."
    )
    subparsers = parser.add_subparsers(dest="data_kind", required=True)

    scheduler = subparsers.add_parser(
        "scheduler", help="Validate the pending 2026-27 Fall (2610) offering snapshot."
    )
    scheduler.add_argument("--file", required=True)
    scheduler.add_argument("--database-url")
    scheduler.add_argument("--expected-sha256", required=True)
    scheduler.add_argument("--expected-courses", required=True, type=int)
    scheduler.add_argument("--expected-offered-courses", required=True, type=int)
    scheduler.add_argument("--expected-sections", required=True, type=int)
    scheduler.add_argument("--expected-lectures", required=True, type=int)
    scheduler.add_argument(
        "--apply",
        action="store_true",
        help="Apply only after reviewing the default dry-run output.",
    )

    curriculum = subparsers.add_parser(
        "curriculum", help="Validate a pending official curriculum requirement snapshot."
    )
    curriculum.add_argument("--file", required=True)
    curriculum.add_argument("--database-url")
    curriculum.add_argument("--expected-sha256", required=True)
    curriculum.add_argument("--expected-program-definitions", required=True, type=int)
    curriculum.add_argument("--expected-program-cohorts", required=True, type=int)
    curriculum.add_argument("--expected-requirement-groups", required=True, type=int)
    curriculum.add_argument("--expected-unique-course-codes", required=True, type=int)
    curriculum.add_argument(
        "--apply",
        action="store_true",
        help="Apply only after reviewing the default dry-run output.",
    )
    return parser


def _serialize_result(result: Any) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    mode = "apply" if args.apply else "dry-run"
    app = create_import_app(args.database_url)
    with app.app_context():
        if args.data_kind == "scheduler":
            result = run_pending_scheduler_update(
                mode=mode,
                file_path=Path(args.file).expanduser(),
                expected_sha256=args.expected_sha256,
                expected_counts=SnapshotExpectations(
                    courses=args.expected_courses,
                    offered_courses=args.expected_offered_courses,
                    sections=args.expected_sections,
                    lectures=args.expected_lectures,
                ),
            )
        else:
            result = run_pending_curriculum_update(
                mode=mode,
                file_path=Path(args.file).expanduser(),
                expected_sha256=args.expected_sha256,
                expected_counts=CurriculumExpectations(
                    program_definitions=args.expected_program_definitions,
                    program_cohorts=args.expected_program_cohorts,
                    requirement_groups=args.expected_requirement_groups,
                    unique_course_codes=args.expected_unique_course_codes,
                ),
            )

    print(_serialize_result(result))
    if result.status == "blocked":
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except PendingAcademicDataValidationError as exc:
        print(f"Pending academic data import blocked: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
