from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from app.models.academic_map import CurriculumProgram, CurriculumRequirementGroup, UserAcademicProfile, UserCourseRecord
from app.models.course import Course
from app.models.course_domain import CourseCatalogRequirement, UserCourseAttempt, UserCourseState
from app.services.course_domain import best_completed_attempt, current_catalog_version, expression_missing_courses
from app.services.academic_curriculum_evaluator import evaluate_requirement_program
from app.utils.academic_map_import_text import clean_copied_status_text


ACTIVE_STATUSES = {
    UserCourseRecord.STATUS_COMPLETED,
    UserCourseRecord.STATUS_IN_PROGRESS,
    UserCourseRecord.STATUS_PLANNED,
}

STATUS_RANK = {
    UserCourseRecord.STATUS_IN_PROGRESS: 0,
    UserCourseRecord.STATUS_PLANNED: 0,
    UserCourseRecord.STATUS_COMPLETED: 1,
    UserCourseRecord.STATUS_INTERESTED: 3,
    UserCourseRecord.STATUS_NOT_INTERESTED: 4,
}

GRADE_POINTS = {
    "A+": 4.3,
    "A": 4.0,
    "A-": 3.7,
    "B+": 3.3,
    "B": 3.0,
    "B-": 2.7,
    "C+": 2.3,
    "C": 2.0,
    "C-": 1.7,
    "D": 1.0,
    "F": 0.0,
}

NON_GPA_GRADES = {"AU", "DI", "I", "P", "PA", "PP", "T", "W"}


class DomainCourseRecordAdapter:
    import_source = "course_domain"
    needs_review = False
    review_reason = None
    raw_payload = {}

    def __init__(
        self,
        *,
        user_id: int,
        course_id: int,
        course_code: str,
        course_title: str | None,
        term_label: str | None,
        term_code: str | None,
        units: float | None,
        status: str,
        grade: str | None,
        keep_grade: bool,
        created_at,
        updated_at,
    ):
        self.id = None
        self.user_id = user_id
        self.course_id = course_id
        self.course_code = course_code
        self.course_title = course_title
        self.term_label = term_label
        self.term_code = term_code
        self.units = Decimal(str(units)) if units is not None else None
        self.status = status
        self.grade = grade
        self.keep_grade = keep_grade
        self.created_at = created_at
        self.updated_at = updated_at

    def to_dict(self, include_grade=False):
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "course_id": self.course_id,
            "course_code": self.course_code,
            "course_title": clean_copied_status_text(self.course_title),
            "term_label": self.term_label,
            "term_code": self.term_code,
            "units": float(self.units) if self.units is not None else None,
            "status": self.status,
            "keep_grade": self.keep_grade,
            "import_source": self.import_source,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_grade and self.keep_grade:
            data["grade"] = self.grade
        return data


def _units(record) -> float:
    if record.units is None:
        return 0.0
    if isinstance(record.units, Decimal):
        return float(record.units)
    return float(record.units)


def _normalized_code(code: str | None) -> str:
    return (code or "").replace(" ", "").upper()


def _grade_point(grade: str | None) -> float | None:
    if not grade:
        return None
    normalized = grade.strip().upper()
    if normalized in NON_GPA_GRADES:
        return None
    return GRADE_POINTS.get(normalized)


def _average_grade_points(records: list) -> dict:
    included = []
    excluded = 0
    for record in records:
        if not record.keep_grade or not record.grade:
            continue
        point = _grade_point(record.grade)
        if point is None:
            excluded += 1
            continue
        included.append(point)
    if not included:
        return {"status": "not_uploaded", "value": None, "included_courses": 0, "excluded_courses": excluded}
    return {
        "status": "available",
        "value": round(sum(included) / len(included), 2),
        "included_courses": len(included),
        "excluded_courses": excluded,
    }


def _programs_for_profile(profile: UserAcademicProfile) -> list[CurriculumProgram]:
    if not profile.cohort or not profile.target_majors:
        return []
    return CurriculumProgram.query.filter(
        CurriculumProgram.cohort == profile.cohort,
        CurriculumProgram.code.in_(profile.target_majors),
        CurriculumProgram.is_active == True,
    ).all()


def _codes_from_rule(rule: dict) -> set[str]:
    codes: set[str] = set()
    for key in ("courses", "required_courses", "choices", "electives"):
        value = rule.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    codes.add(_normalized_code(item))
                elif isinstance(item, dict) and item.get("course_code"):
                    codes.add(_normalized_code(item.get("course_code")))
    for key in ("items", "children", "constraints"):
        for item in rule.get(key, []) if isinstance(rule.get(key), list) else []:
            if isinstance(item, dict):
                codes.update(_codes_from_rule(item))
    if isinstance(rule.get("rule_tree"), dict):
        codes.update(_codes_from_rule(rule["rule_tree"]))
    return {code for code in codes if code}


def _major_grade_records(records: list, program: CurriculumProgram | None) -> list:
    if program is None:
        return []
    required_codes: set[str] = set()
    for group in program.requirement_groups.order_by(CurriculumRequirementGroup.sort_order.asc()).all():
        required_codes.update(_codes_from_rule(group.rule or {}))
    if not required_codes:
        return []
    return [record for record in records if _normalized_code(record.course_code) in required_codes]


def _record_by_code(records: list) -> dict[str, UserCourseRecord]:
    return {_normalized_code(record.course_code): record for record in records}


def strongest_record_for_course(records: list[UserCourseRecord], course_code: str) -> UserCourseRecord | None:
    normalized = _normalized_code(course_code)
    matches = [record for record in records if _normalized_code(record.course_code) == normalized]
    if not matches:
        return None
    return sorted(matches, key=lambda record: STATUS_RANK.get(record.status, 9))[0]


def _domain_status_to_record_status(status: str) -> str | None:
    return {
        "completed": UserCourseRecord.STATUS_COMPLETED,
        "in_progress": UserCourseRecord.STATUS_IN_PROGRESS,
        "interested": UserCourseRecord.STATUS_INTERESTED,
        "not_taken": None,
    }.get(status)


def _course_units(course, attempt: UserCourseAttempt | None = None) -> float | None:
    version = current_catalog_version(course)
    if version and version.credits is not None:
        return float(version.credits)
    if attempt and attempt.offering and attempt.offering.credits_snapshot is not None:
        return float(attempt.offering.credits_snapshot)
    if course.credits is not None:
        return float(course.credits)
    return None


def _course_title(course, attempt: UserCourseAttempt | None = None) -> str | None:
    version = current_catalog_version(course)
    if version and version.title:
        return version.title
    if course.canonical_title:
        return course.canonical_title
    if attempt and attempt.offering and attempt.offering.title_snapshot:
        return attempt.offering.title_snapshot
    return course.name


def _record_from_domain_state(state: UserCourseState) -> DomainCourseRecordAdapter | None:
    status = _domain_status_to_record_status(state.status)
    if status is None:
        return None
    attempt = state.best_attempt
    grade = state.best_grade_letter if status == UserCourseRecord.STATUS_COMPLETED else None
    return DomainCourseRecordAdapter(
        user_id=state.user_id,
        course_id=state.course_id,
        course_code=state.course.normalized_code or _normalized_code(state.course.code),
        course_title=_course_title(state.course, attempt),
        term_label=(attempt.term_label if attempt else None) or (attempt.offering.semester_id if attempt and attempt.offering else None),
        term_code=attempt.offering.semester_id if attempt and attempt.offering else None,
        units=_course_units(state.course, attempt),
        status=status,
        grade=grade,
        keep_grade=bool(grade),
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


def _record_from_domain_attempts(user_id: int, course_id: int, attempts: list[UserCourseAttempt]) -> DomainCourseRecordAdapter | None:
    if not attempts:
        return None
    course = attempts[0].course
    best = best_completed_attempt(attempts)
    if best is not None:
        status = UserCourseRecord.STATUS_COMPLETED
        attempt = best
        grade = best.grade_letter
    elif any(attempt.status == "in_progress" for attempt in attempts):
        status = UserCourseRecord.STATUS_IN_PROGRESS
        attempt = next(attempt for attempt in attempts if attempt.status == "in_progress")
        grade = None
    else:
        return None
    return DomainCourseRecordAdapter(
        user_id=user_id,
        course_id=course_id,
        course_code=course.normalized_code or _normalized_code(course.code),
        course_title=_course_title(course, attempt),
        term_label=attempt.term_label or (attempt.offering.semester_id if attempt.offering else None),
        term_code=attempt.offering.semester_id if attempt.offering else None,
        units=_course_units(course, attempt),
        status=status,
        grade=grade,
        keep_grade=bool(grade),
        created_at=attempt.created_at,
        updated_at=attempt.updated_at,
    )


def _domain_records_for_user(user_id: int) -> list[DomainCourseRecordAdapter]:
    records = []
    states = UserCourseState.query.filter_by(user_id=user_id).all()
    state_course_ids = {state.course_id for state in states}
    for state in states:
        record = _record_from_domain_state(state)
        if record is not None:
            records.append(record)

    attempts_by_course: dict[int, list[UserCourseAttempt]] = {}
    for attempt in UserCourseAttempt.query.filter_by(user_id=user_id).all():
        if attempt.course_id in state_course_ids:
            continue
        attempts_by_course.setdefault(attempt.course_id, []).append(attempt)
    for course_id, attempts in attempts_by_course.items():
        record = _record_from_domain_attempts(user_id, course_id, attempts)
        if record is not None:
            records.append(record)
    return records


def _dedupe_records(records: list) -> list:
    by_code = {}
    for index, record in enumerate(records):
        code = _normalized_code(record.course_code)
        source_rank = 0 if getattr(record, "import_source", None) == "course_domain" else 1
        rank = (
            source_rank,
            STATUS_RANK.get(record.status, 9),
            0 if record.keep_grade else 1,
            index,
        )
        existing = by_code.get(code)
        if existing is None or rank < existing[0]:
            by_code[code] = (rank, record)
    return [item[1] for item in sorted(by_code.values(), key=lambda item: item[0])]


def _catalog_title_by_code() -> dict[str, str]:
    return {
        _normalized_code(course.code): course.name
        for course in Course.query.filter(Course.is_deleted == False).all()
    }


def _catalog_by_code() -> dict[str, dict]:
    return {
        _normalized_code(course.code): {
            "title": course.name,
            "credits": float(course.credits) if course.credits is not None else None,
        }
        for course in Course.query.filter(Course.is_deleted == False).all()
    }


def _evaluation_courses(
    records_by_code: dict[str, UserCourseRecord],
    catalog_by_code: dict[str, dict],
    shared_map: dict[str, list[str]],
) -> dict[str, dict]:
    codes = set(catalog_by_code) | set(records_by_code)
    result = {}
    for code in codes:
        record = records_by_code.get(code)
        catalog = catalog_by_code.get(code, {})
        if catalog.get("credits") is not None:
            credits = catalog["credits"]
            credit_source = "catalog"
        elif record and record.units is not None:
            credits = _units(record)
            credit_source = "record"
        else:
            credits = None
            credit_source = None
        result[code] = {
            "course_code": code,
            "title": (clean_copied_status_text(record.course_title) if record else None) or catalog.get("title"),
            "record_status": record.status if record else None,
            "credits": credits,
            "credit_source": credit_source,
            "shared_majors": shared_map.get(code, []),
        }
    return result


def _shared_major_map(programs: list[CurriculumProgram]) -> dict[str, list[str]]:
    usage: dict[str, list[str]] = {}
    for program in programs:
        for group in program.requirement_groups.order_by(CurriculumRequirementGroup.sort_order.asc()).all():
            for code in _codes_from_rule(group.rule or {}):
                usage.setdefault(code, [])
                if program.code not in usage[code]:
                    usage[code].append(program.code)
    return usage


def _cell_for_code(
    code: str,
    records_by_code: dict[str, UserCourseRecord],
    catalog_title_by_code: dict[str, str],
    shared_map: dict[str, list[str]],
    default_status: str = "need",
) -> dict:
    normalized = _normalized_code(code)
    record = records_by_code.get(normalized)
    if record:
        status = "now" if record.status in {UserCourseRecord.STATUS_IN_PROGRESS, UserCourseRecord.STATUS_PLANNED} else "done"
        return {
            "kind": "course",
            "course_code": normalized,
            "title": clean_copied_status_text(record.course_title) or catalog_title_by_code.get(normalized),
            "status": status,
            "raw_status": record.status,
            "shared_majors": shared_map.get(normalized, []),
        }
    return {
        "kind": "course",
        "course_code": normalized,
        "title": catalog_title_by_code.get(normalized),
        "status": default_status,
        "raw_status": None,
        "shared_majors": shared_map.get(normalized, []),
    }


def _sort_cells(cells: list[dict]) -> list[dict]:
    rank = {"now": 0, "done": 1, "need": 2, "choice": 3, "more": 4}
    return sorted(cells, key=lambda cell: (rank.get(cell["status"], 9), cell.get("course_code") or cell.get("label") or ""))


def _requirement_target(group: CurriculumRequirementGroup, codes: list[str]) -> int:
    rule = group.rule or {}
    min_courses = group.min_courses or 0
    required_values = []
    for key in ("courses", "required_courses"):
        value = rule.get(key)
        if isinstance(value, list):
            required_values.extend(value)
    if required_values:
        return max(min_courses, len(required_values))
    if min_courses and any(isinstance(rule.get(key), list) for key in ("choices", "electives")):
        return min_courses
    return max(min_courses, len(codes))


def _section_label(kind: str, required_count: int | None, total_count: int, min_credits: int | None = None) -> tuple[str, str]:
    if kind == "required":
        return f"Required {total_count} courses", f"必修 {total_count} 门"
    if kind == "choice":
        if required_count:
            return f"Choose {required_count} of {total_count}", f"{total_count} 选 {required_count}"
        return f"Choose from {total_count}", f"{total_count} 门中选择"
    if min_credits:
        return f"At least {required_count or 0} courses / {min_credits} credits", f"至少 {required_count or 0} 门 / {min_credits} 学分"
    return f"At least {required_count or 0} courses", f"至少 {required_count or 0} 门"


def _requirement_section(
    *,
    key: str,
    kind: str,
    codes: list[str],
    records_by_code: dict[str, UserCourseRecord],
    catalog_title_by_code: dict[str, str],
    shared_map: dict[str, list[str]],
    required_count: int | None = None,
    min_credits: int | None = None,
) -> dict:
    default_status = "need" if kind == "required" else "choice"
    cells = _sort_cells([
        _cell_for_code(code, records_by_code, catalog_title_by_code, shared_map, default_status=default_status)
        for code in codes
    ])
    completed = len([cell for cell in cells if cell["status"] in {"now", "done"}])
    total_count = len(cells)
    label_en, label_zh = _section_label(kind, required_count, total_count, min_credits)
    return {
        "key": key,
        "kind": kind,
        "label_en": label_en,
        "label_zh": label_zh,
        "required_count": required_count,
        "total_count": total_count,
        "completed_count": completed,
        "min_credits": min_credits,
        "progress_label": f"{completed} / {required_count}" if required_count else "",
        "cells": cells,
    }


def _codes_for_rule_key(rule: dict, key: str) -> list[str]:
    values = rule.get(key)
    if not isinstance(values, list):
        return []
    codes = []
    for item in values:
        if isinstance(item, str):
            code = _normalized_code(item)
        elif isinstance(item, dict):
            code = _normalized_code(item.get("course_code"))
        else:
            code = ""
        if code:
            codes.append(code)
    return codes


def _requirement_sections(
    group: CurriculumRequirementGroup,
    records_by_code: dict[str, UserCourseRecord],
    catalog_title_by_code: dict[str, str],
    shared_map: dict[str, list[str]],
) -> list[dict]:
    rule = group.rule or {}
    sections = []
    required_codes = _codes_for_rule_key(rule, "required_courses") + _codes_for_rule_key(rule, "courses")
    choice_codes = _codes_for_rule_key(rule, "choices")
    elective_codes = _codes_for_rule_key(rule, "electives")

    if required_codes:
        sections.append(_requirement_section(
            key=f"{group.key}:required",
            kind="required",
            codes=required_codes,
            records_by_code=records_by_code,
            catalog_title_by_code=catalog_title_by_code,
            shared_map=shared_map,
            required_count=len(required_codes),
        ))

    if choice_codes:
        remaining_required = (group.min_courses or 0) - len(required_codes)
        choice_target = remaining_required if required_codes else group.min_courses
        sections.append(_requirement_section(
            key=f"{group.key}:choices",
            kind="choice",
            codes=choice_codes,
            records_by_code=records_by_code,
            catalog_title_by_code=catalog_title_by_code,
            shared_map=shared_map,
            required_count=max(choice_target or 0, 0) or None,
        ))

    if elective_codes:
        sections.append(_requirement_section(
            key=f"{group.key}:electives",
            kind="elective",
            codes=elective_codes,
            records_by_code=records_by_code,
            catalog_title_by_code=catalog_title_by_code,
            shared_map=shared_map,
            required_count=group.min_courses,
            min_credits=group.min_credits,
        ))

    return sections


def _legacy_cell_status(cell: dict) -> str:
    if cell.get("record_status") == UserCourseRecord.STATUS_COMPLETED:
        return "done"
    if cell.get("record_status") in {UserCourseRecord.STATUS_IN_PROGRESS, UserCourseRecord.STATUS_PLANNED}:
        return "now"
    return "choice"


def _legacy_row_aliases(evaluated: dict) -> dict:
    all_cells = []
    seen = set()
    for section in evaluated["sections"]:
        for cell in section["cells"]:
            code = cell["course_code"]
            if code in seen:
                continue
            seen.add(code)
            all_cells.append({**cell, "status": _legacy_cell_status(cell)})
    all_cells = _sort_cells(all_cells)
    visible_cells = all_cells[:4]
    if len(all_cells) > 4:
        visible_cells.append({
            "kind": "more",
            "label": f"+{len(all_cells) - 4} more",
            "status": "more",
            "hidden_count": len(all_cells) - 4,
        })
    current = evaluated["current"]
    return {
        "progress_label": f"{current['counted_courses']} / {current['required_courses'] or '-'}",
        "all_cells": all_cells,
        "visible_cells": visible_cells,
    }


def _build_requirement_matrix(programs: list[CurriculumProgram], records: list[UserCourseRecord]) -> list[dict]:
    records_by_code = _record_by_code(records)
    catalog_by_code = _catalog_by_code()
    shared_map = _shared_major_map(programs)
    evaluation_courses = _evaluation_courses(records_by_code, catalog_by_code, shared_map)
    matrices = []
    for program in programs:
        rows = []
        groups = program.requirement_groups.order_by(CurriculumRequirementGroup.sort_order.asc()).all()
        evaluated_groups = evaluate_requirement_program(
            [
                {
                    "key": group.key,
                    "rule": group.rule or {},
                    "min_courses": group.min_courses,
                    "min_credits": group.min_credits,
                }
                for group in groups
            ],
            evaluation_courses,
        )
        for group, evaluated in zip(groups, evaluated_groups):
            rows.append({
                "key": group.key,
                "name_en": group.name_en,
                "name_zh": group.name_zh,
                "category": group.category,
                "current": evaluated["current"],
                "projected": evaluated["projected"],
                "sections": evaluated["sections"],
                "warnings": evaluated["warnings"],
                **_legacy_row_aliases(evaluated),
                "detail": {
                    "min_courses": group.min_courses,
                    "min_credits": group.min_credits,
                    "rule": group.rule or {},
                },
            })
        matrices.append({"program_code": program.code, "program": program.to_dict(), "rows": rows})
    return matrices


@lru_cache(maxsize=1)
def _load_prerequisite_data() -> dict:
    path = Path(__file__).resolve().parents[1] / "data" / "course_prerequisites.json"
    if not path.exists():
        return {"courses": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _database_prerequisite_items() -> list[dict]:
    items = []
    requirements = CourseCatalogRequirement.query.filter_by(relation_type="prerequisite").all()
    for requirement in requirements:
        version = requirement.catalog_version
        course = version.course if version else None
        expression = requirement.expression_json
        if not course or not isinstance(expression, dict) or not expression:
            continue
        items.append({
            "course_code": course.normalized_code or _normalized_code(course.code),
            "course_title": version.title or course.canonical_title or course.name,
            "prerequisite_expression": expression,
        })
    return items


def _build_prerequisite_metrics(records: list[UserCourseRecord]) -> dict:
    completed_codes = {
        _normalized_code(record.course_code)
        for record in records
        if record.status in ACTIVE_STATUSES
    }
    unlocked = 0
    blockers = []
    prerequisite_items = _database_prerequisite_items()
    if not prerequisite_items:
        prerequisite_items = _load_prerequisite_data().get("courses", [])
    for item in prerequisite_items:
        target_code = _normalized_code(item.get("course_code"))
        if not target_code or target_code in completed_codes:
            continue
        expression = item.get("prerequisite_expression")
        if not isinstance(expression, dict):
            continue
        missing = expression_missing_courses(expression, completed_codes)
        if missing:
            blockers.append({
                "course_code": target_code,
                "course_title": item.get("course_title"),
                "missing": missing,
            })
        else:
            unlocked += 1
    return {
        "unlocked_count": unlocked,
        "blocked_count": len(blockers),
        "blockers": blockers[:8],
    }


def build_academic_map_summary(user_id: int) -> dict:
    profile = UserAcademicProfile.get_or_create_for_user(user_id)
    records = _domain_records_for_user(user_id)
    programs = _programs_for_profile(profile)
    primary_program = programs[0] if programs else None

    completed_records = [record for record in records if record.status == UserCourseRecord.STATUS_COMPLETED]
    active_records = [record for record in records if record.status in ACTIVE_STATUSES]
    total_completed = sum(_units(record) for record in completed_records)
    total_active = sum(_units(record) for record in active_records)
    total_minimum = primary_program.total_min_credits if primary_program else 120
    grade_uploaded = any(record.keep_grade and record.grade for record in records)
    ocga = _average_grade_points(records)
    mcga = _average_grade_points(_major_grade_records(records, primary_program))
    if not grade_uploaded:
        ocga = {"status": "not_uploaded", "value": None, "included_courses": 0, "excluded_courses": 0}
        mcga = {"status": "not_uploaded", "value": None, "included_courses": 0, "excluded_courses": 0}

    return {
        "profile": profile.to_dict(),
        "programs": [program.to_dict() for program in programs],
        "credits": {
            "total_completed": total_completed,
            "total_active": total_active,
            "total_minimum": total_minimum,
            "over_minimum": total_completed > total_minimum,
            "common_core_minimum": primary_program.common_core_min_credits if primary_program else 30,
            "major_minimum": primary_program.major_min_credits if primary_program else None,
        },
        "grade_metrics": {
            "ocga": ocga,
            "mcga": {
                **mcga,
                "program_code": primary_program.code if primary_program else None,
            },
        },
        "prerequisite_metrics": _build_prerequisite_metrics(records),
        "requirement_matrix": _build_requirement_matrix(programs, records),
        "course_counts": {
            "imported": len(records),
            "completed": len(completed_records),
            "in_progress": len([record for record in records if record.status == UserCourseRecord.STATUS_IN_PROGRESS]),
            "planned": len([record for record in records if record.status == UserCourseRecord.STATUS_PLANNED]),
            "needs_review": len([record for record in records if record.needs_review]),
        },
        "records": [record.to_dict(include_grade=True) for record in records],
        "map_completeness": _map_completeness(profile, records),
    }


def _map_completeness(profile: UserAcademicProfile, records: list[UserCourseRecord]) -> dict:
    score = 0
    items = []
    if profile.cohort:
        score += 20
        items.append({"key": "cohort", "complete": True})
    else:
        items.append({"key": "cohort", "complete": False})
    if profile.target_majors:
        score += 25
        items.append({"key": "target_majors", "complete": True})
    else:
        items.append({"key": "target_majors", "complete": False})
    if records:
        score += 35
        items.append({"key": "course_history", "complete": True})
    else:
        items.append({"key": "course_history", "complete": False})
    if any(record.status == UserCourseRecord.STATUS_PLANNED for record in records):
        score += 10
        items.append({"key": "planned_courses", "complete": True})
    else:
        items.append({"key": "planned_courses", "complete": False})
    if any(record.keep_grade and record.grade for record in records):
        score += 10
        items.append({"key": "private_grades", "complete": True})
    else:
        items.append({"key": "private_grades", "complete": False})
    return {"score": min(score, 100), "items": items}
