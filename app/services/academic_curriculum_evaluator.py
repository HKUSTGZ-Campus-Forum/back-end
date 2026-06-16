from __future__ import annotations

from copy import deepcopy
from itertools import combinations, islice
from typing import Any


CURRENT_STATUSES = {"completed", "in_progress"}
PROJECTED_STATUSES = CURRENT_STATUSES | {"planned"}
MAX_COMBINATIONS_PER_SIZE = 1024
MAX_OPTIONS_PER_LEAF = 8


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


def _section_labels(leaf: dict[str, Any], total_count: int) -> tuple[str, str]:
    required_count = _required_courses(leaf)
    min_credits = _required_credits(leaf)
    if leaf.get("type") == "required":
        return f"Required {total_count} courses", f"必修 {total_count} 门"
    if leaf.get("kind") != "elective":
        if required_count:
            return f"Choose {required_count} of {total_count}", f"{total_count} 选 {required_count}"
        return f"Choose from {total_count}", f"{total_count} 门中选择"
    if min_credits:
        return (
            f"At least {required_count} courses / {min_credits} credits",
            f"至少 {required_count} 门 / {min_credits} 学分",
        )
    return f"At least {required_count} courses", f"至少 {required_count} 门"


def _candidate_codes(leaf: dict[str, Any], courses_by_code: dict[str, dict]) -> list[str]:
    return sorted(
        {
            normalize_code(code)
            for code in leaf.get("courses", [])
            if normalize_code(code) in courses_by_code
            and _course_allowed_for_leaf(leaf, courses_by_code[normalize_code(code)])
        }
    )


def _course_allowed_for_leaf(leaf: dict[str, Any], course: dict[str, Any]) -> bool:
    course_area = course.get("area")
    for constraint in leaf.get("constraints", []):
        if constraint.get("type") == "course_area":
            required_area = normalize_code(constraint.get("value"))
            if normalize_code(course_area) != required_area:
                return False
        if constraint.get("type") == "exclude_course_areas":
            excluded = {normalize_code(value) for value in constraint.get("values", [])}
            if normalize_code(course_area) in excluded:
                return False
    return True


def _constraints_satisfied(
    leaf: dict[str, Any],
    selected: tuple[str, ...],
    courses_by_code: dict[str, dict],
) -> bool:
    if len(selected) < _required_courses(leaf):
        return False
    required_credits = _required_credits(leaf)
    credits = [courses_by_code[code].get("credits") for code in selected]
    if required_credits is not None and (
        any(value is None for value in credits) or sum(credits) < required_credits
    ):
        return False
    for constraint in leaf.get("constraints", []):
        if constraint.get("type") == "course_prefix":
            prefix = normalize_code(constraint.get("value"))
            count = len([code for code in selected if code.startswith(prefix)])
            if count < int(constraint.get("min_courses") or 0):
                return False
        if constraint.get("type") == "course_area":
            area = normalize_code(constraint.get("value"))
            area_courses = [
                code
                for code in selected
                if normalize_code(courses_by_code[code].get("area")) == area
            ]
            min_courses = constraint.get("min_courses")
            if min_courses is not None and len(area_courses) < int(min_courses or 0):
                return False
            min_credits = constraint.get("min_credits")
            if min_credits is not None:
                area_credits = [courses_by_code[code].get("credits") for code in area_courses]
                if any(value is None for value in area_credits) or sum(area_credits) < int(min_credits or 0):
                    return False
    return True


def _leaf_progress_score(
    leaf: dict[str, Any],
    selected: tuple[str, ...],
    courses_by_code: dict[str, dict],
) -> tuple[int, int, int]:
    required_courses = _required_courses(leaf)
    required_credits = _required_credits(leaf)
    known_credits = sum(courses_by_code[code].get("credits") or 0 for code in selected)
    prefix_progress = 0
    for constraint in leaf.get("constraints", []):
        if constraint.get("type") == "course_prefix":
            prefix = normalize_code(constraint.get("value"))
            required = int(constraint.get("min_courses") or 0)
            prefix_progress += min(len([code for code in selected if code.startswith(prefix)]), required)
        if constraint.get("type") == "course_area":
            area = normalize_code(constraint.get("value"))
            area_codes = [
                code
                for code in selected
                if normalize_code(courses_by_code[code].get("area")) == area
            ]
            required_courses = int(constraint.get("min_courses") or 0)
            prefix_progress += min(len(area_codes), required_courses)
    return (
        min(len(selected), required_courses) if required_courses else 0,
        min(known_credits, required_credits) if required_credits is not None else 0,
        prefix_progress,
    )


def _constraint_match_score(leaf: dict[str, Any], code: str, courses_by_code: dict[str, dict]) -> int:
    course = courses_by_code[code]
    score = 0
    for constraint in leaf.get("constraints", []):
        if constraint.get("type") == "course_prefix":
            if code.startswith(normalize_code(constraint.get("value"))):
                score += 1
        if constraint.get("type") == "course_area":
            if normalize_code(course.get("area")) == normalize_code(constraint.get("value")):
                score += 1
    return score


def _prioritized_codes(
    leaf: dict[str, Any],
    available_codes: list[str],
    courses_by_code: dict[str, dict],
) -> list[str]:
    return sorted(
        available_codes,
        key=lambda code: (
            -_constraint_match_score(leaf, code, courses_by_code),
            -(courses_by_code[code].get("credits") or 0),
            code,
        ),
    )


def _target_option_size(
    leaf: dict[str, Any],
    available_codes: list[str],
    courses_by_code: dict[str, dict],
) -> int:
    required_courses = _required_courses(leaf)
    required_credits = _required_credits(leaf)
    target = required_courses
    for constraint in leaf.get("constraints", []):
        target = max(target, int(constraint.get("min_courses") or 0))
    if required_credits is not None:
        credits = sorted(
            [
                courses_by_code[code].get("credits") or 0
                for code in available_codes
            ],
            reverse=True,
        )
        running = 0
        credit_target = len(available_codes)
        for index, credit in enumerate(credits, start=1):
            running += credit
            if index >= required_courses and running >= required_credits:
                credit_target = index
                break
        target = max(target, credit_target)
    if target <= 0:
        target = len(available_codes)
    return min(target, len(available_codes))


def _choose_options(
    leaf: dict[str, Any],
    available_codes: list[str],
    courses_by_code: dict[str, dict],
) -> list[tuple[str, ...]]:
    prioritized = _prioritized_codes(leaf, available_codes, courses_by_code)
    target_size = _target_option_size(leaf, prioritized, courses_by_code)
    option_set: set[tuple[str, ...]] = {()}
    sizes = {target_size}
    if target_size <= 4:
        sizes.update(range(1, target_size + 1))
    for size in sorted(size for size in sizes if size > 0):
        for option in islice(combinations(prioritized, size), MAX_COMBINATIONS_PER_SIZE):
            option_set.add(tuple(sorted(option)))
    options = sorted(
        option_set,
        key=lambda option: (
            _constraints_satisfied(leaf, option, courses_by_code),
            _leaf_progress_score(leaf, option, courses_by_code),
            -len(option),
        ),
        reverse=True,
    )
    if options and options[0] != ():
        empty = [option for option in options if option == ()]
        non_empty = [option for option in options if option != ()]
        return non_empty[:MAX_OPTIONS_PER_LEAF] + empty[:1]
    return options[:1]


def _expand_satisfied_allocations(
    leaves: list[dict[str, Any]],
    allocations: dict[str, tuple[str, ...]],
    courses_by_code: dict[str, dict],
) -> dict[str, tuple[str, ...]]:
    expanded = dict(allocations)
    allocated_codes = {
        code
        for leaf in leaves
        if not leaf.get("allow_reuse")
        for code in expanded.get(leaf["_allocation_key"], ())
    }
    for leaf in leaves:
        allocation_key = leaf["_allocation_key"]
        selected = tuple(expanded.get(allocation_key, ()))
        if not selected or not _constraints_satisfied(leaf, selected, courses_by_code):
            continue
        extras = []
        for code in _candidate_codes(leaf, courses_by_code):
            if code in selected:
                continue
            if not leaf.get("allow_reuse") and code in allocated_codes:
                continue
            extras.append(code)
        if extras:
            expanded[allocation_key] = tuple(sorted(set(selected) | set(extras)))
            if not leaf.get("allow_reuse"):
                allocated_codes.update(extras)
    return expanded


def _assignment_score(
    leaves: list[dict[str, Any]],
    allocations: dict[str, tuple[str, ...]],
    courses_by_code: dict[str, dict],
) -> tuple[Any, ...]:
    satisfied_by_leaf = {
        leaf["_allocation_key"]: _constraints_satisfied(
            leaf,
            allocations.get(leaf["_allocation_key"], ()),
            courses_by_code,
        )
        for leaf in leaves
    }
    satisfied = tuple(
        1 if satisfied_by_leaf[leaf["_allocation_key"]] else 0
        for leaf in leaves
    )
    progress = tuple(
        value
        for leaf in leaves
        for value in _leaf_progress_score(
            leaf,
            allocations.get(leaf["_allocation_key"], ()),
            courses_by_code,
        )
    )
    total_allocated_courses = sum(
        len(allocations.get(leaf["_allocation_key"], ()))
        for leaf in leaves
    )
    return (
        sum(satisfied),
        sum(
            1
            for leaf in leaves
            if leaf.get("constraints") and satisfied_by_leaf[leaf["_allocation_key"]]
        ),
        satisfied,
        progress,
        total_allocated_courses,
    )


def _evaluate_view(leaves: list[dict[str, Any]], courses_by_code: dict[str, dict]) -> dict[str, Any]:
    allocations: dict[str, tuple[str, ...]] = {}
    reserved: set[str] = set()
    warnings = sorted(
        {
            f"missing_credit:{code}"
            for leaf in leaves
            if _required_credits(leaf) is not None
            for code in _candidate_codes(leaf, courses_by_code)
            if courses_by_code[code].get("credits") is None
        }
    )

    required_leaves = [leaf for leaf in leaves if leaf.get("type") == "required"]
    choose_leaves = [leaf for leaf in leaves if leaf.get("type") == "choose"]
    for leaf in required_leaves:
        candidates = _candidate_codes(leaf, courses_by_code)
        selected = tuple(
            code
            for code in candidates
            if leaf.get("allow_reuse") or code not in reserved
        )
        allocations[leaf["_allocation_key"]] = selected
        if not leaf.get("allow_reuse"):
            reserved.update(selected)

    ordered_choose_leaves = sorted(
        enumerate(choose_leaves),
        key=lambda item: (
            len(_candidate_codes(item[1], courses_by_code)),
            -len(item[1].get("constraints", [])),
            item[0],
        ),
    )
    used_codes = set(reserved)
    for _original_index, leaf in ordered_choose_leaves:
        candidates = _candidate_codes(leaf, courses_by_code)
        available = [
            code
            for code in candidates
            if leaf.get("allow_reuse") or code not in used_codes
        ]
        options = _choose_options(leaf, available, courses_by_code)
        selected = options[0] if options else ()
        allocations[leaf["_allocation_key"]] = selected
        if not leaf.get("allow_reuse"):
            used_codes.update(selected)

    allocations = _expand_satisfied_allocations(leaves, allocations, courses_by_code)
    satisfied_by_leaf = {
        leaf["_allocation_key"]: _constraints_satisfied(
            leaf,
            allocations.get(leaf["_allocation_key"], ()),
            courses_by_code,
        )
        for leaf in leaves
    }
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
        allocated_elsewhere = {
            code: key.split(":", 1)[-1]
            for view in [current, projected]
            for key, codes in view["allocations"].items()
            if key != allocation_key
            for code in codes
        }
        cells = []
        for raw_code in leaf.get("courses", []):
            code = normalize_code(raw_code)
            course = courses_by_code.get(code, {})
            if code in current_codes:
                allocation_status = "counted"
            elif code in projected_codes:
                allocation_status = "planned" if course.get("record_status") == "planned" else "counted"
            elif code in allocated_elsewhere:
                allocation_status = "excluded_duplicate"
            elif course and course.get("credits") is None:
                allocation_status = "missing_credit"
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
                    "counted_toward": allocated_elsewhere.get(code),
                    "credits": course.get("credits"),
                    "credit_source": course.get("credit_source"),
                    "area": course.get("area"),
                    "section_area": leaf.get("area"),
                    "shared_majors": course.get("shared_majors", []),
                }
            )
        current_progress = _view_for_group(current, [leaf])
        projected_progress = _view_for_group(projected, [leaf])
        default_label_en, default_label_zh = _section_labels(leaf, len(cells))
        sections.append(
            {
                "key": leaf["key"],
                "kind": "required" if leaf.get("type") == "required" else leaf.get("kind", "choice"),
                "label_en": leaf.get("label_en") or default_label_en,
                "label_zh": leaf.get("label_zh") or default_label_zh,
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
