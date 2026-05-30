from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func

from app.models.course import Course


def normalize_course_code(value: str | None) -> str:
    return (value or "").replace(" ", "").upper()


def find_course_by_normalized_code(course_code: str | None) -> Course | None:
    normalized = normalize_course_code(course_code)
    if not normalized:
        return None
    return Course.query.filter(
        func.upper(func.replace(Course.code, " ", "")) == normalized,
        Course.is_deleted == False,
    ).first()


def enrich_import_row_with_catalog(row: dict[str, Any]) -> dict[str, Any]:
    course = find_course_by_normalized_code(row.get("course_code"))
    if course is None:
        return {**row, "matched_course_code": None, "matched_course_title": None}

    units = row.get("units")
    if units is None and course.credits is not None:
        units = course.credits
    if isinstance(units, Decimal):
        units = float(units)

    return {
        **row,
        "course_title": course.name,
        "units": units,
        "matched_course_code": course.code,
        "matched_course_title": course.name,
        "matched_course_id": course.id,
    }
