"""
Import one semester of scheduler offerings from a JSON snapshot.

Usage:
    python -m app.scripts.import_scheduler_offerings --file ../更新材料/25-26summer.json --dry-run
    python -m app.scripts.import_scheduler_offerings --file ../更新材料/25-26summer.json --apply

The importer upserts Course rows and replaces SchedulerSection /
SchedulerLecture rows only for the target semester. Scheduler carts, map
components, map lines, and other semesters are left untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Flask, has_app_context
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.config import Config, normalize_database_config
from app.extensions import db
from app.models.course import Course
from app.models.scheduler_cart import SchedulerUserCourseCart
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_section import SchedulerSection


logger = logging.getLogger(__name__)

SEMESTER_RE = re.compile(r"^\d{4}$")
COURSE_CODE_RE = re.compile(r"^[A-Z0-9]{4,16}$")
BUNDLED_SCHEDULER_OFFERINGS_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "scheduler_offerings"
)
BUNDLED_25_26_FALL_OFFERINGS_FILE = BUNDLED_SCHEDULER_OFFERINGS_DIR / "25-26fall.json"
BUNDLED_25_26_FALL_SEMESTER_ID = "2510"
BUNDLED_25_26_FALL_SHA256 = "507853bd299c25dc9e34e6f67ebd926ea86feda095d5571493eb776d36d3bb9e"
BUNDLED_25_26_SPRING_OFFERINGS_FILE = BUNDLED_SCHEDULER_OFFERINGS_DIR / "25-26spring.json"
BUNDLED_25_26_SPRING_SEMESTER_ID = "2530"
BUNDLED_25_26_SPRING_SHA256 = "e904ef4d50a2044850b002d4758620ca55b386037328aa49d5dc05a32fdd43bb"
BUNDLED_25_26_SUMMER_OFFERINGS_FILE = (
    BUNDLED_SCHEDULER_OFFERINGS_DIR / "25-26summer.json"
)
BUNDLED_25_26_SUMMER_SEMESTER_ID = "2540"
BUNDLED_25_26_SUMMER_SHA256 = "608eefa7520497ace53a1e6f5275ca81e99b1c7d5523ee977801da0df07e2c23"

# Two-deploy switch:
#   1. Keep "dry-run", push to main, and inspect dev logs.
#   2. After confirming the dry-run summary, change to "apply" and push once.
#   3. Keep "apply"; import hashes make repeated deploys skip already-applied files.
DEPLOY_SCHEDULER_OFFERING_UPDATE_MODE = "apply"

SEASON_META = {
    "10": ("Fall", "秋"),
    "20": ("Winter", "冬"),
    "30": ("Spring", "春"),
    "40": ("Summer", "夏"),
}


class OfferingValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class NormalizedLecture:
    day: int
    start_time: int
    end_time: int
    room: str
    instructor: str


@dataclass(frozen=True)
class NormalizedSection:
    semester_id: str
    section_id: str
    course_code: str
    section_type: str
    name: str
    bundle: int
    layer: int
    quota: int
    is_main: bool
    lectures: list[NormalizedLecture]


@dataclass(frozen=True)
class NormalizedCourse:
    course_code: str
    course_title: str
    course_desc: str
    credit: int
    subject: str | None
    catalog_number: str | None
    course_title_abbr: str | None
    pre_requirement: str | None
    co_requirement: str | None
    exclusion: str | None
    pg_course: bool
    klms_course: bool
    vector: str | None
    sections: list[NormalizedSection]


@dataclass(frozen=True)
class OfferingSnapshot:
    semester_id: str
    courses: list[NormalizedCourse]


@dataclass(frozen=True)
class ImportPlan:
    semester_id: str
    courses: int
    sections: int
    lectures: int
    zero_section_courses: list[str]
    offered_course_codes: list[str]
    course_rows_to_insert: int
    course_rows_to_update: int
    existing_sections_to_replace: int
    existing_lectures_to_replace: int
    stale_cart_references: list[str]


@dataclass(frozen=True)
class DeployOfferingResult:
    status: str
    mode: str
    message: str
    import_hash: str | None
    plan: ImportPlan | None = None


@dataclass(frozen=True)
class BundledOfferingUpdate:
    label: str
    mode: str
    file_path: Path
    expected_semester_id: str
    expected_sha256: str


def _field(data: dict[str, Any], name: str, context: str) -> Any:
    if name not in data:
        raise OfferingValidationError(f"{context}: missing required field {name!r}")
    return data[name]


def _string(value: Any, context: str, *, allow_empty: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not allow_empty and not value:
        raise OfferingValidationError(f"{context}: expected non-empty string")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    value = _string(value, "optional string", allow_empty=True)
    return value or None


def _int(value: Any, context: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise OfferingValidationError(f"{context}: expected integer-compatible value") from exc


def _bool(value: Any, context: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    raise OfferingValidationError(f"{context}: expected boolean-compatible value")


def _normalize_course_code(value: Any, context: str) -> str:
    code = _string(value, context).replace(" ", "").upper()
    if not COURSE_CODE_RE.fullmatch(code):
        raise OfferingValidationError(f"{context}: invalid normalized course code {code!r}")
    return code


def _validate_semester_id(value: Any, context: str) -> str:
    semester_id = _string(value, context)
    if not SEMESTER_RE.fullmatch(semester_id):
        raise OfferingValidationError(f"{context}: semester_id must match four digits")
    return semester_id


def _parse_lecture(data: Any, context: str) -> NormalizedLecture:
    if not isinstance(data, dict):
        raise OfferingValidationError(f"{context}: lecture must be an object")

    day = _int(_field(data, "day", context), f"{context}.day")
    if day < 1 or day > 7:
        raise OfferingValidationError(f"{context}.day: expected 1-7")

    start_time = _int(_field(data, "start_time", context), f"{context}.start_time")
    end_time = _int(_field(data, "end_time", context), f"{context}.end_time")
    if start_time < 0 or start_time > 2359 or end_time < 0 or end_time > 2359:
        raise OfferingValidationError(f"{context}: times must be HHMM-compatible integers")
    if start_time >= end_time:
        raise OfferingValidationError(f"{context}: start_time must be before end_time")

    return NormalizedLecture(
        day=day,
        start_time=start_time,
        end_time=end_time,
        room=_string(_field(data, "room", context), f"{context}.room", allow_empty=True),
        instructor=_string(_field(data, "instructor", context), f"{context}.instructor", allow_empty=True),
    )


def _parse_section(
    data: Any,
    context: str,
    *,
    expected_semester_id: str,
    expected_course_code: str,
    seen_section_keys: set[tuple[str, str]],
) -> NormalizedSection:
    if not isinstance(data, dict):
        raise OfferingValidationError(f"{context}: section must be an object")

    semester_id = _validate_semester_id(_field(data, "semester_id", context), f"{context}.semester_id")
    if semester_id != expected_semester_id:
        raise OfferingValidationError(
            f"{context}.semester_id: {semester_id!r} does not match top-level {expected_semester_id!r}"
        )

    section_id = _string(_field(data, "section_id", context), f"{context}.section_id")
    key = (semester_id, section_id)
    if key in seen_section_keys:
        raise OfferingValidationError(f"{context}: duplicate section key {semester_id}/{section_id}")
    seen_section_keys.add(key)

    course_code = _normalize_course_code(_field(data, "course_code", context), f"{context}.course_code")
    if course_code != expected_course_code:
        raise OfferingValidationError(
            f"{context}.course_code: {course_code!r} does not match parent course {expected_course_code!r}"
        )

    lectures = data.get("lectures", [])
    if not isinstance(lectures, list):
        raise OfferingValidationError(f"{context}.lectures: expected list")

    return NormalizedSection(
        semester_id=semester_id,
        section_id=section_id,
        course_code=course_code,
        section_type=_string(_field(data, "section_type", context), f"{context}.section_type"),
        name=_string(_field(data, "name", context), f"{context}.name"),
        bundle=_int(_field(data, "bundle", context), f"{context}.bundle"),
        layer=_int(_field(data, "layer", context), f"{context}.layer"),
        quota=_int(_field(data, "quota", context), f"{context}.quota"),
        is_main=_bool(_field(data, "is_main", context), f"{context}.is_main"),
        lectures=[
            _parse_lecture(lecture, f"{context}.lectures[{lecture_index}]")
            for lecture_index, lecture in enumerate(lectures)
        ],
    )


def _parse_course(
    data: Any,
    context: str,
    *,
    semester_id: str,
    seen_course_codes: set[str],
    seen_section_keys: set[tuple[str, str]],
) -> NormalizedCourse:
    if not isinstance(data, dict):
        raise OfferingValidationError(f"{context}: course must be an object")

    course_code = _normalize_course_code(_field(data, "course_code", context), f"{context}.course_code")
    if course_code in seen_course_codes:
        raise OfferingValidationError(f"{context}: duplicate course_code {course_code!r}")
    seen_course_codes.add(course_code)

    sections = data.get("sections", [])
    if not isinstance(sections, list):
        raise OfferingValidationError(f"{context}.sections: expected list")

    return NormalizedCourse(
        course_code=course_code,
        course_title=_string(_field(data, "course_title", context), f"{context}.course_title"),
        course_desc=_string(data.get("course_desc", ""), f"{context}.course_desc", allow_empty=True),
        credit=_int(_field(data, "credit", context), f"{context}.credit"),
        subject=_optional_string(data.get("subject")),
        catalog_number=_optional_string(data.get("catalog_number")),
        course_title_abbr=_optional_string(data.get("course_title_abbr")),
        pre_requirement=_optional_string(data.get("pre_requirement")),
        co_requirement=_optional_string(data.get("co_requirement")),
        exclusion=_optional_string(data.get("exclusion")),
        pg_course=_bool(data.get("pg_course", False), f"{context}.pg_course"),
        klms_course=_bool(data.get("klms_course", False), f"{context}.klms_course"),
        vector=_optional_string(data.get("vector")),
        sections=[
            _parse_section(
                section,
                f"{context}.sections[{section_index}]",
                expected_semester_id=semester_id,
                expected_course_code=course_code,
                seen_section_keys=seen_section_keys,
            )
            for section_index, section in enumerate(sections)
        ],
    )


def load_offerings_file(file_path: Path, semester_override: str | None = None) -> OfferingSnapshot:
    with file_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, dict):
        raise OfferingValidationError("top level JSON must be an object")

    semester_id = _validate_semester_id(_field(data, "semester_id", "top level"), "top level semester_id")
    if semester_override is not None:
        semester_override = _validate_semester_id(semester_override, "semester override")
        if semester_override != semester_id:
            raise OfferingValidationError(
                f"semester override {semester_override!r} does not match file semester_id {semester_id!r}"
            )

    raw_courses = _field(data, "courses", "top level")
    if not isinstance(raw_courses, list):
        raise OfferingValidationError("top level courses must be a list")

    seen_course_codes: set[str] = set()
    seen_section_keys: set[tuple[str, str]] = set()
    courses = [
        _parse_course(
            course,
            f"courses[{course_index}]",
            semester_id=semester_id,
            seen_course_codes=seen_course_codes,
            seen_section_keys=seen_section_keys,
        )
        for course_index, course in enumerate(raw_courses)
    ]

    return OfferingSnapshot(semester_id=semester_id, courses=courses)


def file_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def semester_display_label(semester_id: str) -> str:
    year = int(semester_id[:2])
    next_year = (year + 1) % 100
    season_en, season_zh = SEASON_META.get(semester_id[2:], (semester_id, semester_id))
    return f"20{year:02d}-{next_year:02d} {season_en} / {year:02d}-{next_year:02d}{season_zh}"


def build_import_plan(snapshot: OfferingSnapshot) -> ImportPlan:
    course_codes = [course.course_code for course in snapshot.courses]
    offered_course_codes = sorted(
        course.course_code for course in snapshot.courses if course.sections
    )
    existing_course_codes = {
        code
        for (code,) in db.session.query(Course.code).filter(Course.code.in_(course_codes)).all()
    }
    stale_cart_references = sorted(
        {
            course_code
            for (course_code,) in (
                db.session.query(SchedulerUserCourseCart.course_code)
                .filter(SchedulerUserCourseCart.semester_id == snapshot.semester_id)
                .all()
            )
            if course_code not in offered_course_codes
        }
    )

    return ImportPlan(
        semester_id=snapshot.semester_id,
        courses=len(snapshot.courses),
        sections=sum(len(course.sections) for course in snapshot.courses),
        lectures=sum(len(section.lectures) for course in snapshot.courses for section in course.sections),
        zero_section_courses=sorted(course.course_code for course in snapshot.courses if not course.sections),
        offered_course_codes=offered_course_codes,
        course_rows_to_insert=len(set(course_codes) - existing_course_codes),
        course_rows_to_update=len(set(course_codes) & existing_course_codes),
        existing_sections_to_replace=SchedulerSection.query.filter_by(
            semester_id=snapshot.semester_id
        ).count(),
        existing_lectures_to_replace=SchedulerLecture.query.filter_by(
            semester_id=snapshot.semester_id
        ).count(),
        stale_cart_references=stale_cart_references,
    )


def apply_offerings(snapshot: OfferingSnapshot) -> ImportPlan:
    plan = build_import_plan(snapshot)

    try:
        SchedulerLecture.query.filter_by(semester_id=snapshot.semester_id).delete(
            synchronize_session=False
        )
        SchedulerSection.query.filter_by(semester_id=snapshot.semester_id).delete(
            synchronize_session=False
        )

        course_by_code = {
            course.code: course
            for course in Course.query.filter(
                Course.code.in_([course.course_code for course in snapshot.courses])
            ).all()
        }
        for item in snapshot.courses:
            course = course_by_code.get(item.course_code)
            if course is None:
                course = Course(code=item.course_code, name=item.course_title, credits=item.credit)
                course_by_code[item.course_code] = course
            course.name = item.course_title
            course.description = item.course_desc
            course.credits = item.credit
            course.subject = item.subject.upper() if item.subject else None
            course.catalog_number = item.catalog_number
            course.course_title_abbr = item.course_title_abbr
            course.pre_requirement = item.pre_requirement
            course.co_requirement = item.co_requirement
            course.exclusion = item.exclusion
            course.pg_course = item.pg_course
            course.klms_course = item.klms_course
            course.vector = item.vector
            course.is_active = True
            course.is_deleted = False
            db.session.add(course)
        db.session.flush()

        for item in snapshot.courses:
            course = course_by_code[item.course_code]
            for section in item.sections:
                db.session.add(SchedulerSection(
                    semester_id=section.semester_id,
                    section_id=section.section_id,
                    course_id=course.id,
                    name=section.name,
                    bundle=section.bundle,
                    layer=section.layer,
                    quota=section.quota,
                    section_type=section.section_type,
                    is_main=section.is_main,
                ))
        db.session.flush()

        for item in snapshot.courses:
            for section in item.sections:
                for lecture in section.lectures:
                    db.session.add(SchedulerLecture(
                        semester_id=section.semester_id,
                        section_id=section.section_id,
                        day=lecture.day,
                        start_time=lecture.start_time,
                        end_time=lecture.end_time,
                        room=lecture.room,
                        instructor=lecture.instructor,
                    ))

        db.session.commit()
        return plan
    except Exception:
        db.session.rollback()
        raise


def _ensure_import_run_table() -> None:
    db.session.execute(text(
        """
        CREATE TABLE IF NOT EXISTS scheduler_offering_import_runs (
            import_hash VARCHAR(64) PRIMARY KEY,
            semester_id VARCHAR(16) NOT NULL,
            mode VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL,
            summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    ))
    db.session.commit()


def _import_run_status(import_hash: str) -> str | None:
    row = db.session.execute(
        text("SELECT status FROM scheduler_offering_import_runs WHERE import_hash = :import_hash"),
        {"import_hash": import_hash},
    ).fetchone()
    return row[0] if row else None


def _record_import_run(import_hash: str, semester_id: str, mode: str, status: str, summary: str) -> None:
    existing = _import_run_status(import_hash)
    if existing is None:
        db.session.execute(
            text(
                """
                INSERT INTO scheduler_offering_import_runs
                    (import_hash, semester_id, mode, status, summary)
                VALUES
                    (:import_hash, :semester_id, :mode, :status, :summary)
                """
            ),
            {
                "import_hash": import_hash,
                "semester_id": semester_id,
                "mode": mode,
                "status": status,
                "summary": summary,
            },
        )
    else:
        db.session.execute(
            text(
                """
                UPDATE scheduler_offering_import_runs
                SET mode = :mode,
                    status = :status,
                    summary = :summary,
                    updated_at = CURRENT_TIMESTAMP
                WHERE import_hash = :import_hash
                """
            ),
            {
                "import_hash": import_hash,
                "mode": mode,
                "status": status,
                "summary": summary,
            },
        )
    db.session.commit()


def _plan_summary_json(plan: ImportPlan) -> str:
    return json.dumps({
        "semester_id": plan.semester_id,
        "courses": plan.courses,
        "sections": plan.sections,
        "lectures": plan.lectures,
        "zero_section_courses": plan.zero_section_courses,
        "course_rows_to_insert": plan.course_rows_to_insert,
        "course_rows_to_update": plan.course_rows_to_update,
        "existing_sections_to_replace": plan.existing_sections_to_replace,
        "existing_lectures_to_replace": plan.existing_lectures_to_replace,
        "stale_cart_references": plan.stale_cart_references,
    }, ensure_ascii=True, sort_keys=True)


def run_deploy_scheduler_offering_update(
    *,
    mode: str,
    file_path: Path,
    expected_semester_id: str,
    expected_sha256: str,
) -> DeployOfferingResult:
    normalized_mode = mode.strip().lower()
    if normalized_mode in {"", "disabled", "off", "false", "0"}:
        return DeployOfferingResult(
            status="disabled",
            mode=normalized_mode or "disabled",
            message="Deployment scheduler offering update is disabled.",
            import_hash=None,
        )
    if normalized_mode not in {"dry-run", "apply"}:
        return DeployOfferingResult(
            status="blocked",
            mode=normalized_mode,
            message=f"Unsupported deployment scheduler offering mode: {mode!r}",
            import_hash=None,
        )

    file_path = file_path.resolve()
    import_hash = file_sha256(file_path)
    if import_hash != expected_sha256:
        return DeployOfferingResult(
            status="blocked",
            mode=normalized_mode,
            message=(
                "Bundled scheduler offering JSON hash mismatch: "
                f"{import_hash} != {expected_sha256}"
            ),
            import_hash=import_hash,
        )

    snapshot = load_offerings_file(file_path, expected_semester_id)
    plan = build_import_plan(snapshot)

    if normalized_mode == "dry-run":
        db.session.rollback()
        return DeployOfferingResult(
            status="dry-run",
            mode=normalized_mode,
            message="Deployment scheduler offering dry-run completed; no database changes were made.",
            import_hash=import_hash,
            plan=plan,
        )

    _ensure_import_run_table()
    status = _import_run_status(import_hash)
    if status == "applied":
        return DeployOfferingResult(
            status="skipped",
            mode=normalized_mode,
            message="Scheduler offering import already applied for this JSON hash.",
            import_hash=import_hash,
            plan=plan,
        )
    if status == "running":
        return DeployOfferingResult(
            status="skipped",
            mode=normalized_mode,
            message="Scheduler offering import is already running in another worker.",
            import_hash=import_hash,
            plan=plan,
        )

    _record_import_run(import_hash, snapshot.semester_id, normalized_mode, "running", _plan_summary_json(plan))
    try:
        applied_plan = apply_offerings(snapshot)
        _record_import_run(
            import_hash,
            snapshot.semester_id,
            normalized_mode,
            "applied",
            _plan_summary_json(applied_plan),
        )
        return DeployOfferingResult(
            status="applied",
            mode=normalized_mode,
            message="Scheduler offering import applied successfully.",
            import_hash=import_hash,
            plan=applied_plan,
        )
    except Exception as exc:
        db.session.rollback()
        _record_import_run(import_hash, snapshot.semester_id, normalized_mode, "failed", str(exc))
        raise


def run_bundled_25_26_summer_deploy_update(
    mode: str = DEPLOY_SCHEDULER_OFFERING_UPDATE_MODE,
) -> DeployOfferingResult:
    return run_deploy_scheduler_offering_update(
        mode=mode,
        file_path=BUNDLED_25_26_SUMMER_OFFERINGS_FILE,
        expected_semester_id=BUNDLED_25_26_SUMMER_SEMESTER_ID,
        expected_sha256=BUNDLED_25_26_SUMMER_SHA256,
    )


def bundled_scheduler_offering_updates(
    mode: str = DEPLOY_SCHEDULER_OFFERING_UPDATE_MODE,
) -> list[BundledOfferingUpdate]:
    return [
        BundledOfferingUpdate(
            label="25-26 fall",
            mode=mode,
            file_path=BUNDLED_25_26_FALL_OFFERINGS_FILE,
            expected_semester_id=BUNDLED_25_26_FALL_SEMESTER_ID,
            expected_sha256=BUNDLED_25_26_FALL_SHA256,
        ),
        BundledOfferingUpdate(
            label="25-26 spring",
            mode=mode,
            file_path=BUNDLED_25_26_SPRING_OFFERINGS_FILE,
            expected_semester_id=BUNDLED_25_26_SPRING_SEMESTER_ID,
            expected_sha256=BUNDLED_25_26_SPRING_SHA256,
        ),
        BundledOfferingUpdate(
            label="25-26 summer",
            mode=mode,
            file_path=BUNDLED_25_26_SUMMER_OFFERINGS_FILE,
            expected_semester_id=BUNDLED_25_26_SUMMER_SEMESTER_ID,
            expected_sha256=BUNDLED_25_26_SUMMER_SHA256,
        ),
    ]


def run_bundled_scheduler_offering_updates(
    mode: str = DEPLOY_SCHEDULER_OFFERING_UPDATE_MODE,
) -> list[tuple[BundledOfferingUpdate, DeployOfferingResult]]:
    results = []
    for update in bundled_scheduler_offering_updates(mode):
        result = run_deploy_scheduler_offering_update(
            mode=update.mode,
            file_path=update.file_path,
            expected_semester_id=update.expected_semester_id,
            expected_sha256=update.expected_sha256,
        )
        results.append((update, result))
    return results


def create_import_app(database_url: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["ENABLE_BACKGROUND_TASKS"] = False
    if database_url:
        normalized_uri, engine_options = normalize_database_config(database_url)
        app.config["SQLALCHEMY_DATABASE_URI"] = normalized_uri
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = engine_options
    db.init_app(app)
    return app


def _database_target() -> str:
    try:
        return make_url(db.engine.url).render_as_string(hide_password=True)
    except Exception:
        return str(db.engine.url)


def _format_list(values: list[str]) -> str:
    if not values:
        return "none"
    return ", ".join(values)


def print_summary(file_path: Path, plan: ImportPlan, *, database_target: str, mode: str) -> None:
    print(f"Mode: {mode}")
    print(f"JSON path: {file_path}")
    print(f"Database target: {database_target}")
    print(f"Target semester: {plan.semester_id} ({semester_display_label(plan.semester_id)})")
    print(f"Course count: {plan.courses}")
    print(f"Section count: {plan.sections}")
    print(f"Lecture count: {plan.lectures}")
    print(f"Zero-section courses: {len(plan.zero_section_courses)}")
    print(f"Zero-section course codes: {_format_list(plan.zero_section_courses)}")
    print(f"Course rows to insert: {plan.course_rows_to_insert}")
    print(f"Course rows to update: {plan.course_rows_to_update}")
    print(f"Rows currently in target semester to replace: sections={plan.existing_sections_to_replace}, lectures={plan.existing_lectures_to_replace}")
    print(f"Stale cart references after import: {len(plan.stale_cart_references)}")
    print(f"Stale cart course codes: {_format_list(plan.stale_cart_references)}")
    if mode == "dry-run":
        print("No database changes were made.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import one semester of UniKorn scheduler offerings.")
    parser.add_argument("--file", required=True, help="Path to offering JSON file.")
    parser.add_argument("--semester", help="Optional semester id override; must match the JSON file.")
    parser.add_argument("--database-url", help="Optional destination database URL; otherwise DATABASE_URL is used.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate and summarize without committing changes.")
    mode.add_argument("--apply", action="store_true", help="Apply the replacement transaction.")
    args = parser.parse_args()

    file_path = Path(args.file).expanduser().resolve()
    snapshot = load_offerings_file(file_path, args.semester)

    app = create_import_app(args.database_url)
    try:
        with app.app_context():
            if args.apply:
                plan = apply_offerings(snapshot)
                print_summary(file_path, plan, database_target=_database_target(), mode="apply")
                return

            plan = build_import_plan(snapshot)
            print_summary(file_path, plan, database_target=_database_target(), mode="dry-run")
            db.session.rollback()
    except SQLAlchemyError as exc:
        if has_app_context():
            db.session.rollback()
        print(
            "Database connection/query failed. Set DATABASE_URL or pass --database-url "
            "for the local/dev scheduler database, then rerun --dry-run before --apply.",
            file=sys.stderr,
        )
        print(f"SQLAlchemy error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
