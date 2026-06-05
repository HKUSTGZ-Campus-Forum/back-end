from __future__ import annotations

import re
from decimal import Decimal
from difflib import SequenceMatcher
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


def _normalized_title(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _title_similarity(left: str | None, right: str | None) -> float:
    left_normalized = _normalized_title(left)
    right_normalized = _normalized_title(right)
    if not left_normalized or not right_normalized:
        return 0.0
    sequence_score = SequenceMatcher(None, left_normalized, right_normalized).ratio()
    left_tokens = set(left_normalized.split())
    right_tokens = set(right_normalized.split())
    token_score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens) if left_tokens and right_tokens else 0.0
    return max(sequence_score, token_score)


def find_course_by_title(course_title: str | None) -> Course | None:
    if not course_title:
        return None
    best_course = None
    best_score = 0.0
    for course in Course.query.filter(Course.is_deleted == False).all():
        score = _title_similarity(course_title, course.name)
        if score > best_score:
            best_course = course
            best_score = score
    return best_course if best_score >= 0.68 else None


def enrich_import_row_with_catalog(row: dict[str, Any]) -> dict[str, Any]:
    course = find_course_by_normalized_code(row.get("course_code"))
    if course is None:
        course = find_course_by_title(row.get("course_title") or row.get("raw"))
    if course is None:
        return {**row, "matched_course_code": None, "matched_course_title": None}

    units = row.get("units")
    if units is None and course.credits is not None:
        units = course.credits
    if isinstance(units, Decimal):
        units = float(units)

    return {
        **row,
        "course_code": course.code,
        "course_title": course.name,
        "units": units,
        "matched_course_code": course.code,
        "matched_course_title": course.name,
        "matched_course_id": course.id,
    }
