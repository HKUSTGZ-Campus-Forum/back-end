from __future__ import annotations

from decimal import Decimal

from app.models.academic_map import CurriculumProgram, UserAcademicProfile, UserCourseRecord


ACTIVE_STATUSES = {
    UserCourseRecord.STATUS_COMPLETED,
    UserCourseRecord.STATUS_IN_PROGRESS,
    UserCourseRecord.STATUS_PLANNED,
}


def _units(record: UserCourseRecord) -> float:
    if record.units is None:
        return 0.0
    if isinstance(record.units, Decimal):
        return float(record.units)
    return float(record.units)


def _programs_for_profile(profile: UserAcademicProfile) -> list[CurriculumProgram]:
    if not profile.cohort or not profile.target_majors:
        return []
    return CurriculumProgram.query.filter(
        CurriculumProgram.cohort == profile.cohort,
        CurriculumProgram.code.in_(profile.target_majors),
        CurriculumProgram.is_active == True,
    ).all()


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
