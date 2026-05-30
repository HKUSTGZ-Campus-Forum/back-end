from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from app.models.academic_map import CurriculumProgram, CurriculumRequirementGroup, UserAcademicProfile, UserCourseRecord
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


def _units(record: UserCourseRecord) -> float:
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


def _average_grade_points(records: list[UserCourseRecord]) -> dict:
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
    for item in rule.get("items", []) if isinstance(rule.get("items"), list) else []:
        if isinstance(item, dict):
            codes.update(_codes_from_rule(item))
    return {code for code in codes if code}


def _major_grade_records(records: list[UserCourseRecord], program: CurriculumProgram | None) -> list[UserCourseRecord]:
    if program is None:
        return []
    required_codes: set[str] = set()
    for group in program.requirement_groups.order_by(CurriculumRequirementGroup.sort_order.asc()).all():
        required_codes.update(_codes_from_rule(group.rule or {}))
    if not required_codes:
        return []
    return [record for record in records if _normalized_code(record.course_code) in required_codes]


def _record_by_code(records: list[UserCourseRecord]) -> dict[str, UserCourseRecord]:
    return {_normalized_code(record.course_code): record for record in records}


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
            "title": clean_copied_status_text(record.course_title),
            "status": status,
            "raw_status": record.status,
            "shared_majors": shared_map.get(normalized, []),
        }
    return {
        "kind": "course",
        "course_code": normalized,
        "title": None,
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
    shared_map: dict[str, list[str]],
    required_count: int | None = None,
    min_credits: int | None = None,
) -> dict:
    default_status = "need" if kind == "required" else "choice"
    cells = _sort_cells([
        _cell_for_code(code, records_by_code, shared_map, default_status=default_status)
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
            shared_map=shared_map,
            required_count=max(choice_target or 0, 0) or None,
        ))

    if elective_codes:
        sections.append(_requirement_section(
            key=f"{group.key}:electives",
            kind="elective",
            codes=elective_codes,
            records_by_code=records_by_code,
            shared_map=shared_map,
            required_count=group.min_courses,
            min_credits=group.min_credits,
        ))

    return sections


def _build_requirement_matrix(programs: list[CurriculumProgram], records: list[UserCourseRecord]) -> list[dict]:
    records_by_code = _record_by_code(records)
    shared_map = _shared_major_map(programs)
    matrices = []
    for program in programs:
        rows = []
        for group in program.requirement_groups.order_by(CurriculumRequirementGroup.sort_order.asc()).all():
            sections = _requirement_sections(group, records_by_code, shared_map)
            cells = _sort_cells([cell for section in sections for cell in section["cells"]])
            codes = [cell["course_code"] for cell in cells if cell.get("course_code")]
            satisfied = len([cell for cell in cells if cell["status"] in {"now", "done"}])
            target = _requirement_target(group, codes)
            visible = cells[:4]
            if len(cells) > 4:
                visible.append({
                    "kind": "more",
                    "label": f"+{len(cells) - 4} more",
                    "status": "more",
                    "hidden_count": len(cells) - 4,
                })
            rows.append({
                "key": group.key,
                "name_en": group.name_en,
                "name_zh": group.name_zh,
                "category": group.category,
                "progress_label": f"{satisfied} / {target}" if target else "",
                "visible_cells": visible,
                "all_cells": cells,
                "sections": sections,
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


def _missing_prerequisites(expression: dict | None, completed_codes: set[str]) -> list[str]:
    if not expression:
        return []
    if expression.get("course_code"):
        code = _normalized_code(expression.get("course_code"))
        return [] if code in completed_codes else [code]
    op = str(expression.get("op", "")).upper()
    items = [item for item in expression.get("items", []) if isinstance(item, dict)]
    if op == "AND":
        missing: list[str] = []
        for item in items:
            missing.extend(_missing_prerequisites(item, completed_codes))
        return sorted(set(missing))
    if op == "OR":
        item_missing = [_missing_prerequisites(item, completed_codes) for item in items]
        if any(len(missing) == 0 for missing in item_missing):
            return []
        shortest = min(item_missing, key=len) if item_missing else []
        return sorted(set(shortest))
    return []


def _build_prerequisite_metrics(records: list[UserCourseRecord]) -> dict:
    completed_codes = {
        _normalized_code(record.course_code)
        for record in records
        if record.status in ACTIVE_STATUSES
    }
    unlocked = 0
    blockers = []
    for item in _load_prerequisite_data().get("courses", []):
        target_code = _normalized_code(item.get("course_code"))
        if not target_code or target_code in completed_codes:
            continue
        expression = item.get("prerequisite_expression")
        if not isinstance(expression, dict):
            continue
        missing = _missing_prerequisites(expression, completed_codes)
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
    records = UserCourseRecord.query.filter_by(user_id=user_id).order_by(UserCourseRecord.created_at.asc()).all()
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
