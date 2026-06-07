import re
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import func

from app.models.course import Course
from app.models.course_domain import (
    CourseCatalogVersion,
    CourseOffering,
    UserCourseAttempt,
    UserCourseState,
)


GRADE_POINTS = {
    "A+": 4.0,
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


def normalize_course_code(value: Any) -> str:
    return "".join(str(value or "").split()).upper()


def display_course_code(value: Any) -> str:
    compact = normalize_course_code(value)
    prefix = "".join(ch for ch in compact if ch.isalpha())
    suffix = compact[len(prefix):]
    return f"{prefix} {suffix}".strip() if prefix and suffix else compact


def subject_for_code(value: Any) -> str | None:
    match = re.match(r"^([A-Z]{4})", normalize_course_code(value))
    return match.group(1) if match else None


def catalog_number_for_code(value: Any) -> str | None:
    compact = normalize_course_code(value)
    subject = subject_for_code(compact)
    return compact[len(subject):] if subject else None


def grade_points_for_letter(value: Any) -> float | None:
    key = str(value or "").strip().upper()
    return GRADE_POINTS.get(key)


def expression_missing_courses(expression: dict | None, completed_codes: set[str]) -> list[str]:
    completed = {normalize_course_code(code) for code in completed_codes}
    return _expression_missing_courses(expression, completed)


def _expression_missing_courses(expression: dict | None, completed_codes: set[str]) -> list[str]:
    if not isinstance(expression, dict) or not expression:
        return []

    code_value = expression.get("course_code") or expression.get("normalized_code")
    if code_value:
        code = normalize_course_code(code_value)
        return [] if code in completed_codes else [code]

    op = str(expression.get("op", "")).upper()
    items = [item for item in expression.get("items", []) if isinstance(item, dict)]
    if op == "AND":
        missing = []
        for item in items:
            missing.extend(_expression_missing_courses(item, completed_codes))
        return sorted(set(missing))

    if op == "OR":
        item_missing = [_expression_missing_courses(item, completed_codes) for item in items]
        if any(len(missing) == 0 for missing in item_missing):
            return []
        return sorted(set(min(item_missing, key=len))) if item_missing else []

    return []


def find_course_by_code(code: Any) -> Course | None:
    normalized = normalize_course_code(code)
    if not normalized:
        return None

    course = Course.query.filter(
        Course.normalized_code == normalized,
        Course.is_deleted == False,
    ).first()
    if course:
        return course

    return Course.query.filter(
        func.upper(func.replace(Course.code, " ", "")) == normalized,
        Course.is_deleted == False,
    ).first()


def current_catalog_version(course: Course | int) -> CourseCatalogVersion | None:
    course_id = course if isinstance(course, int) else course.id
    return (
        CourseCatalogVersion.query
        .filter_by(course_id=course_id)
        .order_by(
            CourseCatalogVersion.effective_from_semester_id.desc().nullslast(),
            CourseCatalogVersion.imported_at.desc(),
            CourseCatalogVersion.id.desc(),
        )
        .first()
    )


def find_offering(course_or_id: Course | int, semester_id: Any) -> CourseOffering | None:
    course_id = course_or_id if isinstance(course_or_id, int) else course_or_id.id
    semester = str(semester_id or "").strip()
    if not semester:
        return None
    return CourseOffering.query.filter_by(course_id=course_id, semester_id=semester).first()


def _attempt_grade_points(attempt: UserCourseAttempt) -> float:
    if attempt.grade_points is None:
        return -1.0
    if isinstance(attempt.grade_points, Decimal):
        return float(attempt.grade_points)
    return float(attempt.grade_points)


def best_completed_attempt(attempts: Iterable[UserCourseAttempt]) -> UserCourseAttempt | None:
    completed = [attempt for attempt in attempts if attempt.status == "completed"]
    if not completed:
        return None
    return max(completed, key=lambda attempt: (_attempt_grade_points(attempt), attempt.id or 0))


def derive_user_course_state(user_id: int, course_id: int) -> dict:
    attempts = UserCourseAttempt.query.filter_by(user_id=user_id, course_id=course_id).all()
    best = best_completed_attempt(attempts)
    if best is not None:
        return {
            "user_id": user_id,
            "course_id": course_id,
            "status": "completed",
            "best_attempt_id": best.id,
            "best_grade_points": best.grade_points,
            "best_grade_letter": best.grade_letter,
            "source": "derived",
        }

    if any(attempt.status == "in_progress" for attempt in attempts):
        return {
            "user_id": user_id,
            "course_id": course_id,
            "status": "in_progress",
            "best_attempt_id": None,
            "best_grade_points": None,
            "best_grade_letter": None,
            "source": "derived",
        }

    manual = UserCourseState.query.filter_by(user_id=user_id, course_id=course_id).first()
    if manual is not None:
        return {
            "user_id": user_id,
            "course_id": course_id,
            "status": manual.status,
            "best_attempt_id": manual.best_attempt_id,
            "best_grade_points": manual.best_grade_points,
            "best_grade_letter": manual.best_grade_letter,
            "source": manual.source,
        }

    return {
        "user_id": user_id,
        "course_id": course_id,
        "status": "not_taken",
        "best_attempt_id": None,
        "best_grade_points": None,
        "best_grade_letter": None,
        "source": "derived",
    }
