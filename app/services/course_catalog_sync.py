from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.extensions import db
from app.models.course import Course


def _normalize_code(value: Any) -> str:
    return "".join(str(value or "").split()).upper()


def _credits(value: Any) -> int:
    matches = re.findall(r"\d+(?:\.\d+)?", str(value or ""))
    for match in matches:
        try:
            credits = int(float(match))
        except ValueError:
            continue
        if credits > 0:
            return credits
    return 0


def sync_course_catalog_from_payload(payload: dict[str, Any]) -> dict[str, int]:
    courses = payload.get("courses") if isinstance(payload.get("courses"), list) else []
    courses_by_normalized_code: dict[str, list[Course]] = {}
    for course in Course.query.filter_by(is_deleted=False).all():
        courses_by_normalized_code.setdefault(_normalize_code(course.code), []).append(course)

    upserted = 0
    skipped = 0
    for item in courses:
        code = str(item.get("course_code") or "").strip().upper()
        normalized_code = _normalize_code(code)
        name = str(item.get("course_title") or "").strip()
        if not normalized_code or not name:
            skipped += 1
            continue

        credits = _credits(item.get("credit"))
        matching_courses = courses_by_normalized_code.get(normalized_code, [])
        if not matching_courses:
            if credits <= 0:
                skipped += 1
                continue
            course = Course(code=code, name=name, credits=credits, is_active=True, is_deleted=False)
            db.session.add(course)
            courses_by_normalized_code.setdefault(normalized_code, []).append(course)
        else:
            exact_match = next((course for course in matching_courses if course.code == code), None)
            course = exact_match or matching_courses[0]

        for matched_course in matching_courses or [course]:
            matched_course.name = name
            if credits > 0:
                matched_course.credits = credits
            matched_course.is_active = True
            matched_course.is_deleted = False
            matched_course.deleted_at = None
            matched_course.description = item.get("course_desc")
        upserted += 1

    db.session.commit()
    return {"upserted": upserted, "skipped": skipped}


def sync_course_catalog_from_file(path: Path | None = None) -> dict[str, int]:
    catalog_path = path or Path(__file__).resolve().parents[1] / "data" / "course_catalog.json"
    if not catalog_path.exists():
        return {"upserted": 0, "skipped": 0}
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    return sync_course_catalog_from_payload(payload)
