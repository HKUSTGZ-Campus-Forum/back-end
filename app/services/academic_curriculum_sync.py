from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.extensions import db
from app.models.academic_map import CurriculumProgram, CurriculumRequirementGroup


MAJOR_CODE_ALIASES = {
    "DSBD": "DSA",
    "SEEN": "SEE",
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _program_code(value: Any) -> str:
    code = _clean_text(value).replace(" ", "").upper()
    return MAJOR_CODE_ALIASES.get(code, code)


def _course_code(value: Any) -> str:
    return _clean_text(value).replace(" ", "").upper()


def _integer(value: Any, default: int | None = None) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize_rule(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, Any] = {}
    for key, raw in value.items():
        if key in {"courses", "required_courses", "choices", "electives"} and isinstance(raw, list):
            normalized[key] = [_course_code(item) for item in raw if _course_code(item)]
        elif key in {"items", "children", "constraints"} and isinstance(raw, list):
            normalized[key] = [
                _normalize_rule(item)
                for item in raw
                if isinstance(item, dict)
            ]
        elif key == "rule_tree" and isinstance(raw, dict):
            normalized[key] = _normalize_rule(raw)
        else:
            normalized[key] = raw
    return normalized


def sync_curriculum_requirements_from_payload(payload: dict[str, Any]) -> dict[str, int]:
    programs = payload.get("programs") if isinstance(payload.get("programs"), list) else []
    result = {"programs_upserted": 0, "groups_upserted": 0, "groups_removed": 0, "programs_skipped": 0}

    for item in programs:
        if not isinstance(item, dict):
            result["programs_skipped"] += 1
            continue

        code = _program_code(item.get("code"))
        name_en = _clean_text(item.get("name_en"))
        cohorts = [_clean_text(cohort) for cohort in item.get("cohorts", []) if _clean_text(cohort)] if isinstance(item.get("cohorts"), list) else []
        cohort = _clean_text(item.get("cohort"))
        if cohort:
            cohorts = [cohort]
        if not code or not cohorts or not name_en:
            result["programs_skipped"] += 1
            continue

        for cohort in cohorts:
            program = CurriculumProgram.query.filter_by(code=code, cohort=cohort).first()
            if program is None:
                program = CurriculumProgram(code=code, cohort=cohort, name_en=name_en)
                db.session.add(program)

            program.name_en = name_en
            program.name_zh = _clean_text(item.get("name_zh")) or None
            program.total_min_credits = _integer(item.get("total_min_credits"), 120) or 120
            program.common_core_min_credits = _integer(item.get("common_core_min_credits"), 30) or 30
            program.major_min_credits = _integer(item.get("major_min_credits"))
            program.home_areas = item.get("home_areas") if isinstance(item.get("home_areas"), list) else []
            program.is_active = bool(item.get("is_active", True))
            db.session.flush()
            result["programs_upserted"] += 1

            incoming_keys: set[str] = set()
            groups = item.get("requirement_groups") if isinstance(item.get("requirement_groups"), list) else []
            for group_item in groups:
                if not isinstance(group_item, dict):
                    continue
                key = _clean_text(group_item.get("key"))
                name = _clean_text(group_item.get("name_en"))
                category = _clean_text(group_item.get("category")) or "major"
                if not key or not name:
                    continue
                incoming_keys.add(key)

                group = CurriculumRequirementGroup.query.filter_by(program_id=program.id, key=key).first()
                if group is None:
                    group = CurriculumRequirementGroup(program_id=program.id, key=key, name_en=name, category=category)
                    db.session.add(group)

                group.name_en = name
                group.name_zh = _clean_text(group_item.get("name_zh")) or None
                group.category = category
                group.min_credits = _integer(group_item.get("min_credits"))
                group.min_courses = _integer(group_item.get("min_courses"))
                group.rule = _normalize_rule(group_item.get("rule"))
                group.sort_order = _integer(group_item.get("sort_order"), 0) or 0
                result["groups_upserted"] += 1

            stale_groups = CurriculumRequirementGroup.query.filter(
                CurriculumRequirementGroup.program_id == program.id,
                ~CurriculumRequirementGroup.key.in_(incoming_keys),
            ).all()
            for group in stale_groups:
                db.session.delete(group)
                result["groups_removed"] += 1

    db.session.commit()
    return result


def sync_curriculum_requirements_from_file(path: Path | None = None) -> dict[str, int]:
    curriculum_path = path or Path(__file__).resolve().parents[1] / "data" / "curriculum_requirements.json"
    if not curriculum_path.exists():
        return {"programs_upserted": 0, "groups_upserted": 0, "groups_removed": 0, "programs_skipped": 0}
    payload = json.loads(curriculum_path.read_text(encoding="utf-8"))
    return sync_curriculum_requirements_from_payload(payload)
