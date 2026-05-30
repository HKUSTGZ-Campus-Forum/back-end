from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.extensions import db
from app.models.course import Course


def _credits(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def sync_course_catalog_from_payload(payload: dict[str, Any]) -> dict[str, int]:
    courses = payload.get("courses") if isinstance(payload.get("courses"), list) else []
    upserted = 0
    skipped = 0
    for item in courses:
        code = str(item.get("course_code") or "").strip().upper()
        name = str(item.get("course_title") or "").strip()
        if not code or not name:
            skipped += 1
            continue

        course = Course.query.filter_by(code=code).first()
        if course is None:
            course = Course(code=code, name=name, credits=_credits(item.get("credit")), is_active=True, is_deleted=False)
            db.session.add(course)
        else:
            course.name = name
            course.credits = _credits(item.get("credit"))
            course.is_active = True
            course.is_deleted = False
            course.deleted_at = None

        course.description = item.get("course_desc")
        upserted += 1

    db.session.commit()
    return {"upserted": upserted, "skipped": skipped}


def sync_course_catalog_from_file(path: Path | None = None) -> dict[str, int]:
    catalog_path = path or Path(__file__).resolve().parents[1] / "data" / "course_catalog.json"
    if not catalog_path.exists():
        return {"upserted": 0, "skipped": 0}
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    return sync_course_catalog_from_payload(payload)
