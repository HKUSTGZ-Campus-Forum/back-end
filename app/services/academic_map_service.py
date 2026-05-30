from __future__ import annotations

from decimal import Decimal

from app.models.academic_map import CurriculumProgram, CurriculumRequirementGroup, UserAcademicProfile, UserCourseRecord


ACTIVE_STATUSES = {
    UserCourseRecord.STATUS_COMPLETED,
    UserCourseRecord.STATUS_IN_PROGRESS,
    UserCourseRecord.STATUS_PLANNED,
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
