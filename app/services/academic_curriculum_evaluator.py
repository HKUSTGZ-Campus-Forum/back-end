from __future__ import annotations

from copy import deepcopy
from typing import Any


CURRENT_STATUSES = {"completed", "in_progress"}
PROJECTED_STATUSES = CURRENT_STATUSES | {"planned"}


def normalize_code(value: Any) -> str:
    return "".join(str(value or "").split()).upper()


def legacy_rule_tree(
    rule: dict[str, Any],
    min_courses: int | None = None,
    min_credits: int | None = None,
) -> dict[str, Any]:
    children = []
    required = list(rule.get("required_courses") or []) + list(rule.get("courses") or [])
    choices = list(rule.get("choices") or [])
    electives = list(rule.get("electives") or [])
    if required:
        children.append({"type": "required", "key": "required", "courses": required})
    if choices:
        children.append(
            {
                "type": "choose",
                "key": "choices",
                "min_courses": max((min_courses or 0) - len(required), 0),
                "courses": choices,
            }
        )
    if electives:
        children.append(
            {
                "type": "choose",
                "key": "electives",
                "kind": "elective",
                "min_courses": min_courses,
                "min_credits": min_credits,
                "courses": electives,
            }
        )
    return {"type": "all_of", "children": children}


def rule_tree_for(group: dict[str, Any]) -> dict[str, Any]:
    rule = group.get("rule") or {}
    tree = rule.get("rule_tree")
    return (
        deepcopy(tree)
        if isinstance(tree, dict)
        else legacy_rule_tree(
            rule,
            min_courses=group.get("min_courses"),
            min_credits=group.get("min_credits"),
        )
    )


def _flatten_leaves(node: dict[str, Any]) -> list[dict[str, Any]]:
    if node.get("type") == "all_of":
        leaves = []
        for child in node.get("children", []):
            if isinstance(child, dict):
                leaves.extend(_flatten_leaves(child))
        return leaves
    if node.get("type") in {"required", "choose"}:
        return [node]
    return []


def _eligible_courses(courses_by_code: dict[str, dict], statuses: set[str]) -> dict[str, dict]:
    return {
        normalize_code(code): course
        for code, course in courses_by_code.items()
        if course.get("record_status") in statuses
    }


def _required_courses(leaf: dict[str, Any]) -> int:
    if leaf.get("type") == "required":
        return len(leaf.get("courses", []))
    return int(leaf.get("min_courses") or 0)


def _required_credits(leaf: dict[str, Any]) -> int | None:
    value = leaf.get("min_credits")
    return int(value) if value is not None else None


def _evaluate_view(leaves: list[dict[str, Any]], courses_by_code: dict[str, dict]) -> dict[str, Any]:
    allocations = {}
    satisfied_by_leaf = {}
    warnings = []
    for leaf in leaves:
        allocation_key = leaf["_allocation_key"]
        codes = [
            normalize_code(code)
            for code in leaf.get("courses", [])
            if normalize_code(code) in courses_by_code
        ]
        if leaf.get("type") == "required":
            selected = tuple(codes)
        else:
            selected = tuple(codes[: _required_courses(leaf)])
        allocations[allocation_key] = selected
        required_credits = _required_credits(leaf)
        credits = [courses_by_code[code].get("credits") for code in selected]
        if required_credits is not None:
            warnings.extend(
                f"missing_credit:{code}"
                for code in selected
                if courses_by_code[code].get("credits") is None
            )
        satisfied_by_leaf[allocation_key] = (
            len(selected) >= _required_courses(leaf)
            and (
                required_credits is None
                or (all(value is not None for value in credits) and sum(credits) >= required_credits)
            )
        )
    return {
        "allocations": allocations,
        "satisfied_by_leaf": satisfied_by_leaf,
        "warnings": warnings,
        "courses_by_code": courses_by_code,
    }


def _leaves_for_group(leaves: list[dict[str, Any]], group_key: str) -> list[dict[str, Any]]:
    return [leaf for leaf in leaves if leaf["_group_key"] == group_key]


def _view_for_group(view: dict[str, Any], group_leaves: list[dict[str, Any]]) -> dict[str, Any]:
    keys = {leaf["_allocation_key"] for leaf in group_leaves}
    allocations = {key: value for key, value in view["allocations"].items() if key in keys}
    counted_codes = sorted({code for codes in allocations.values() for code in codes})
    required_courses = sum(_required_courses(leaf) for leaf in group_leaves)
    required_credits = sum(_required_credits(leaf) or 0 for leaf in group_leaves) or None
    counted_credits = sum(view["courses_by_code"][code].get("credits") or 0 for code in counted_codes)
    return {
        "satisfied": all(view["satisfied_by_leaf"].get(key, False) for key in keys),
        "counted_courses": len(counted_codes),
        "required_courses": required_courses or None,
        "counted_credits": counted_credits,
        "required_credits": required_credits,
    }


def _merge_sections(
    leaves: list[dict[str, Any]],
    courses_by_code: dict[str, dict],
    current: dict[str, Any],
    projected: dict[str, Any],
) -> list[dict[str, Any]]:
    sections = []
    for leaf in leaves:
        allocation_key = leaf["_allocation_key"]
        current_codes = set(current["allocations"].get(allocation_key, ()))
        projected_codes = set(projected["allocations"].get(allocation_key, ()))
        cells = []
        for raw_code in leaf.get("courses", []):
            code = normalize_code(raw_code)
            course = courses_by_code.get(code, {})
            if code in current_codes:
                allocation_status = "counted"
            elif code in projected_codes:
                allocation_status = "planned"
            else:
                allocation_status = "candidate"
            cells.append(
                {
                    "kind": "course",
                    "course_code": code,
                    "title": course.get("title"),
                    "record_status": course.get("record_status"),
                    "allocation_status": allocation_status,
                    "status": (
                        "done"
                        if course.get("record_status") == "completed"
                        else "now"
                        if course.get("record_status") in {"in_progress", "planned"}
                        else "choice"
                    ),
                    "counted_toward": None,
                    "credits": course.get("credits"),
                    "credit_source": course.get("credit_source"),
                    "shared_majors": course.get("shared_majors", []),
                }
            )
        current_progress = _view_for_group(current, [leaf])
        projected_progress = _view_for_group(projected, [leaf])
        sections.append(
            {
                "key": leaf["key"],
                "kind": "required" if leaf.get("type") == "required" else leaf.get("kind", "choice"),
                "label_en": leaf.get("label_en") or leaf["key"],
                "label_zh": leaf.get("label_zh"),
                "current": current_progress,
                "projected": projected_progress,
                "cells": cells,
                "required_count": current_progress["required_courses"],
                "total_count": len(cells),
                "completed_count": current_progress["counted_courses"],
                "min_credits": current_progress["required_credits"],
                "progress_label": f"{current_progress['counted_courses']} / {current_progress['required_courses'] or '-'}",
            }
        )
    return sections


def _serialize_group(
    group: dict[str, Any],
    leaves: list[dict[str, Any]],
    courses_by_code: dict[str, dict],
    current: dict[str, Any],
    projected: dict[str, Any],
) -> dict[str, Any]:
    group_leaves = _leaves_for_group(leaves, group["key"])
    return {
        "current": _view_for_group(current, group_leaves),
        "projected": _view_for_group(projected, group_leaves),
        "sections": _merge_sections(group_leaves, courses_by_code, current, projected),
        "warnings": sorted(
            {
                warning
                for warning in current["warnings"] + projected["warnings"]
                if warning.split(":", 1)[-1]
                in {
                    normalize_code(code)
                    for leaf in group_leaves
                    for code in leaf.get("courses", [])
                }
            }
        ),
    }


def evaluate_requirement_program(
    groups: list[dict[str, Any]], courses_by_code: dict[str, dict]
) -> list[dict[str, Any]]:
    leaves = [
        {
            **leaf,
            "_group_key": group["key"],
            "_allocation_key": f"{group['key']}:{leaf['key']}",
        }
        for group in groups
        for leaf in _flatten_leaves(rule_tree_for(group))
    ]
    current = _evaluate_view(leaves, _eligible_courses(courses_by_code, CURRENT_STATUSES))
    projected = _evaluate_view(leaves, _eligible_courses(courses_by_code, PROJECTED_STATUSES))
    return [_serialize_group(group, leaves, courses_by_code, current, projected) for group in groups]


def evaluate_requirement_group(rule: dict[str, Any], courses_by_code: dict[str, dict]) -> dict[str, Any]:
    return evaluate_requirement_program([{"key": "group", "rule": rule}], courses_by_code)[0]
