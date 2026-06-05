# Academic Map Curriculum Rule Tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace flattened Academic Map curriculum choices with fully evaluated rule trees for every bundled program and cohort, including independent choice groups, credit thresholds, source constraints, duplicate prevention, and separate current versus projected progress.

**Architecture:** Store readable `rule_tree` JSON inside the existing `CurriculumRequirementGroup.rule` JSONB column. Add a focused pure evaluator module that normalizes legacy rules, resolves catalog credits, finds deterministic non-overlapping allocations, and returns frontend-ready evaluated rows. Keep `academic_map_service.py` responsible for database lookup and response assembly, then update the Vue matrix component to render evaluated leaf sections without parsing curriculum rules.

**Tech Stack:** Flask, SQLAlchemy, PostgreSQL JSONB, pytest, Nuxt 3, Vue 3, TypeScript, SCSS, vue-i18n.

---

## Working Rules

- The backend and frontend are separate Git repositories:
  - backend: `/Users/mount/Desktop/Programming/unikorn/back-end`
  - frontend: `/Users/mount/Desktop/Programming/unikorn/front-end`
- The approved design is:
  - `back-end/docs/superpowers/specs/2026-05-31-academic-map-curriculum-rule-tree-design.md`
- Use `superpowers:using-git-worktrees` before implementation if an isolated worktree is appropriate for either repository.
- Do not alter prerequisite parsing in `app/data/course_prerequisites.json`.
- Do not change the frontend backend target. Local frontend verification must use the deployed test backend at `https://dev.unikorn.axfff.com`.
- Run the local frontend on `localhost:3000`, then stop the dev server after verification.
- Push completed backend changes to backend `main` so the test backend deploys automatically.
- Do not publish frontend product updates. The user performs that step.

## File Map

Backend files:

- Create `app/services/academic_curriculum_evaluator.py`
  - Pure rule normalization, credit resolution, allocation solving, and evaluated-row serialization.
- Modify `app/services/academic_curriculum_sync.py`
  - Recursively normalize `rule_tree` without flattening nested structures.
- Modify `app/services/course_catalog_sync.py`
  - Distinguish unknown credit from valid zero credit.
- Modify `app/services/academic_map_service.py`
  - Query catalog facts and delegate row evaluation to the new evaluator.
- Modify `app/data/curriculum_requirements.json`
  - Replace bundled flattened rules with PDF-audited `rule_tree` entries.
- Modify `tests/test_course_catalog_sync.py`
  - Cover valid zero-credit catalog rows.
- Modify `tests/test_academic_curriculum_sync.py`
  - Cover recursive `rule_tree` normalization.
- Create `tests/test_academic_curriculum_evaluator.py`
  - Cover rule evaluation independently of Flask response assembly.
- Modify `tests/test_academic_map_summary.py`
  - Cover API row integration and legacy fallback.
- Create `tests/test_bundled_curriculum_requirements.py`
  - Snapshot-like assertions for all bundled PDF sources and known corrections.

Frontend files:

- Modify `types/academic-map.ts`
  - Replace flattened section progress types with evaluated current/projected contracts.
- Modify `components/academic-map/RequirementMatrix.vue`
  - Render evaluated leaf summaries, expanded cards, distinct planned state, and warnings.
- Modify `i18n/locales/zh.json`
  - Add Chinese progress, allocation, and warning copy.
- Modify `i18n/locales/en.json`
  - Add English progress, allocation, and warning copy.

Explicitly out of scope:

- `front-end/components/academic-map/RequirementDetailPanel.vue`
  - It is currently not mounted by `pages/academic-map/index.vue`. Do not expand scope by rewiring it during this change.

---

### Task 1: Preserve Valid Zero-Credit Catalog Courses

**Files:**
- Modify: `app/services/course_catalog_sync.py`
- Modify: `tests/test_course_catalog_sync.py`

- [ ] **Step 1: Write the failing zero-credit sync test**

Append:

```python
def test_sync_course_catalog_keeps_explicit_zero_credit_courses(app):
    from app.services.course_catalog_sync import sync_course_catalog_from_payload

    payload = {
        "courses": [
            {
                "course_code": "SEEN3000",
                "course_title": "Industrial Training",
                "credit": "0",
                "course_desc": "Zero-credit required training.",
            }
        ]
    }

    with app.app_context():
        result = sync_course_catalog_from_payload(payload)
        course = Course.query.filter_by(code="SEEN3000").one()

    assert result == {"upserted": 1, "skipped": 0}
    assert course.credits == 0
```

- [ ] **Step 2: Run the focused test and verify that it fails**

Run:

```bash
pytest tests/test_course_catalog_sync.py::test_sync_course_catalog_keeps_explicit_zero_credit_courses -v
```

Expected: fail because the current sync skips new rows when parsed credit is `0`.

- [ ] **Step 3: Distinguish unknown credit from zero credit**

Change the helper and its call sites to:

```python
def _credits(value: Any) -> int | None:
    matches = re.findall(r"\d+(?:\.\d+)?", str(value or ""))
    for match in matches:
        try:
            return int(float(match))
        except ValueError:
            continue
    return None
```

Use:

```python
        credits = _credits(item.get("credit"))
        matching_courses = courses_by_normalized_code.get(normalized_code, [])
        if not matching_courses:
            if credits is None:
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
            if credits is not None:
                matched_course.credits = credits
```

- [ ] **Step 4: Run catalog sync tests**

Run:

```bash
pytest tests/test_course_catalog_sync.py -v
```

Expected: all tests pass, including range parsing and zero-credit insertion.

- [ ] **Step 5: Commit the focused backend fix**

```bash
git add app/services/course_catalog_sync.py tests/test_course_catalog_sync.py
git commit -m "fix: preserve zero-credit catalog courses"
```

---

### Task 2: Normalize Nested Curriculum Rule Trees

**Files:**
- Modify: `app/services/academic_curriculum_sync.py`
- Modify: `tests/test_academic_curriculum_sync.py`

- [ ] **Step 1: Write the failing recursive normalization test**

Append:

```python
def test_sync_curriculum_requirements_preserves_nested_rule_tree(app):
    from app.services.academic_curriculum_sync import sync_curriculum_requirements_from_payload

    payload = {
        "programs": [
            {
                "code": "dsa",
                "cohort": "2025",
                "name_en": "Data Science and Big Data Technology",
                "requirement_groups": [
                    {
                        "key": "fundamental_courses",
                        "name_en": "Fundamental Courses",
                        "category": "fundamental",
                        "rule": {
                            "rule_tree": {
                                "type": "all_of",
                                "children": [
                                    {
                                        "type": "choose",
                                        "key": "calculus_i",
                                        "min_courses": 1,
                                        "courses": ["ufug 1102", "UFUG1105"],
                                    },
                                    {
                                        "type": "required",
                                        "key": "fixed",
                                        "courses": ["ufug 2104"],
                                    },
                                ],
                            }
                        },
                    }
                ],
            }
        ]
    }

    with app.app_context():
        sync_curriculum_requirements_from_payload(payload)
        program = CurriculumProgram.query.filter_by(code="DSA", cohort="2025").one()
        group = CurriculumRequirementGroup.query.filter_by(program_id=program.id, key="fundamental_courses").one()

    assert group.rule == {
        "rule_tree": {
            "type": "all_of",
            "children": [
                {
                    "type": "choose",
                    "key": "calculus_i",
                    "min_courses": 1,
                    "courses": ["UFUG1102", "UFUG1105"],
                },
                {
                    "type": "required",
                    "key": "fixed",
                    "courses": ["UFUG2104"],
                },
            ],
        }
    }
```

- [ ] **Step 2: Run the focused test and verify that it fails**

Run:

```bash
pytest tests/test_academic_curriculum_sync.py::test_sync_curriculum_requirements_preserves_nested_rule_tree -v
```

Expected: fail because `_normalize_rule()` currently only recurses through `items`.

- [ ] **Step 3: Generalize recursive normalization**

Replace `_normalize_rule()` with:

```python
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
```

- [ ] **Step 4: Run curriculum sync tests**

Run:

```bash
pytest tests/test_academic_curriculum_sync.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit nested rule sync support**

```bash
git add app/services/academic_curriculum_sync.py tests/test_academic_curriculum_sync.py
git commit -m "feat: sync nested academic curriculum rules"
```

---

### Task 3: Add the Pure Curriculum Evaluator

**Files:**
- Create: `app/services/academic_curriculum_evaluator.py`
- Create: `tests/test_academic_curriculum_evaluator.py`

- [ ] **Step 1: Add tests for independent choices and current/projected semantics**

Create `tests/test_academic_curriculum_evaluator.py` with:

```python
from app.services.academic_curriculum_evaluator import evaluate_requirement_group, evaluate_requirement_program


def _course(code, status, credits=3, source="catalog"):
    return {
        "course_code": code,
        "title": code,
        "record_status": status,
        "credits": credits,
        "credit_source": source,
        "shared_majors": [],
    }


def test_evaluator_keeps_independent_choice_groups_separate():
    rule = {
        "rule_tree": {
            "type": "all_of",
            "children": [
                {"type": "choose", "key": "calculus_i", "min_courses": 1, "courses": ["UFUG1102", "UFUG1105"]},
                {"type": "choose", "key": "calculus_ii", "min_courses": 1, "courses": ["UFUG1103", "UFUG1106"]},
                {
                    "type": "choose",
                    "key": "science",
                    "min_courses": 2,
                    "min_credits": 6,
                    "courses": ["UFUG1301", "UFUG1302", "UFUG1401", "UFUG1501"],
                },
            ],
        }
    }
    courses = {
        "UFUG1105": _course("UFUG1105", "completed"),
        "UFUG1106": _course("UFUG1106", "in_progress"),
        "UFUG1301": _course("UFUG1301", "completed"),
        "UFUG1501": _course("UFUG1501", "planned"),
    }

    result = evaluate_requirement_group(rule, courses)

    assert [section["key"] for section in result["sections"]] == ["calculus_i", "calculus_ii", "science"]
    assert result["current"]["satisfied"] is False
    assert result["projected"]["satisfied"] is True
    assert result["sections"][2]["current"]["counted_courses"] == 1
    assert result["sections"][2]["projected"]["counted_courses"] == 2


def test_evaluator_reports_planned_course_without_counting_it_as_current():
    rule = {
        "rule_tree": {
            "type": "choose",
            "key": "programming",
            "min_courses": 1,
            "courses": ["UFUG1601", "UFUG2601"],
        }
    }
    courses = {"UFUG2601": _course("UFUG2601", "planned", credits=4)}

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is False
    assert result["projected"]["satisfied"] is True
    cells = {cell["course_code"]: cell for cell in result["sections"][0]["cells"]}
    assert cells["UFUG2601"]["record_status"] == "planned"
    assert cells["UFUG2601"]["allocation_status"] == "planned"
```

- [ ] **Step 2: Run the evaluator tests and verify import failure**

Run:

```bash
pytest tests/test_academic_curriculum_evaluator.py -v
```

Expected: fail because `academic_curriculum_evaluator.py` does not exist.

- [ ] **Step 3: Add evaluator constants and legacy adapter**

Create `app/services/academic_curriculum_evaluator.py` with these public foundations:

```python
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
        children.append({
            "type": "choose",
            "key": "choices",
            "min_courses": max((min_courses or 0) - len(required), 0),
            "courses": choices,
        })
    if electives:
        children.append({
            "type": "choose",
            "key": "electives",
            "kind": "elective",
            "min_courses": min_courses,
            "min_credits": min_credits,
            "courses": electives,
        })
    return {"type": "all_of", "children": children}


def rule_tree_for(group: dict[str, Any]) -> dict[str, Any]:
    rule = group.get("rule") or {}
    tree = rule.get("rule_tree")
    return deepcopy(tree) if isinstance(tree, dict) else legacy_rule_tree(
        rule,
        min_courses=group.get("min_courses"),
        min_credits=group.get("min_credits"),
    )
```

- [ ] **Step 4: Add leaf flattening, progress views, and serialization**

Add focused private helpers:

```python
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
```

Implement `evaluate_requirement_group(rule, courses_by_code)` so that it:

```python
def evaluate_requirement_program(groups: list[dict[str, Any]], courses_by_code: dict[str, dict]) -> list[dict[str, Any]]:
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
    return [
        _serialize_group(group, leaves, courses_by_code, current, projected)
        for group in groups
    ]


def evaluate_requirement_group(rule: dict[str, Any], courses_by_code: dict[str, dict]) -> dict[str, Any]:
    return evaluate_requirement_program([{"key": "group", "rule": rule}], courses_by_code)[0]
```

Implement `_serialize_group()` so one group response contains:

```python
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
        "warnings": sorted({
            warning
            for warning in current["warnings"] + projected["warnings"]
            if warning.split(":", 1)[-1] in {
                normalize_code(code)
                for leaf in group_leaves
                for code in leaf.get("courses", [])
            }
        }),
    }
```

Add initial `_evaluate_view()` and `_merge_sections()` helpers. In this first
commit, fixed leaves count each listed eligible course and choose leaves take PDF
order until the minimum course count is reached. Task 4 replaces this local
selection with the global solver before integration:

```python
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
            selected = tuple(codes[:_required_courses(leaf)])
        allocations[allocation_key] = selected
        required_credits = _required_credits(leaf)
        credits = [courses_by_code[code].get("credits") for code in selected]
        if required_credits is not None:
            warnings.extend(f"missing_credit:{code}" for code in selected if courses_by_code[code].get("credits") is None)
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
            cells.append({
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
            })
        current_progress = _view_for_group(current, [leaf])
        projected_progress = _view_for_group(projected, [leaf])
        sections.append({
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
        })
    return sections
```

- [ ] **Step 5: Run evaluator tests**

Run:

```bash
pytest tests/test_academic_curriculum_evaluator.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit the evaluator foundation**

```bash
git add app/services/academic_curriculum_evaluator.py tests/test_academic_curriculum_evaluator.py
git commit -m "feat: evaluate academic curriculum rule trees"
```

---

### Task 4: Add Global Allocation, Credits, and Source Constraints

**Files:**
- Modify: `app/services/academic_curriculum_evaluator.py`
- Modify: `tests/test_academic_curriculum_evaluator.py`

- [ ] **Step 1: Add failing allocation and constraint tests**

Append:

```python
def test_evaluator_does_not_reuse_one_course_across_two_choice_leaves():
    rule = {
        "rule_tree": {
            "type": "all_of",
            "children": [
                {"type": "choose", "key": "strict", "min_courses": 1, "courses": ["AMAT2050"]},
                {"type": "choose", "key": "broad", "min_courses": 1, "courses": ["AMAT2050", "AMAT2320"]},
            ],
        }
    }
    courses = {"AMAT2050": _course("AMAT2050", "completed")}

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is False
    counted = [
        cell["course_code"]
        for section in result["sections"]
        for cell in section["cells"]
        if cell["allocation_status"] == "counted"
    ]
    assert counted == ["AMAT2050"]


def test_evaluator_uses_global_assignment_for_overlapping_pools():
    rule = {
        "rule_tree": {
            "type": "all_of",
            "children": [
                {"type": "choose", "key": "broad", "min_courses": 1, "courses": ["AMAT2050", "AMAT2320"]},
                {"type": "choose", "key": "strict", "min_courses": 1, "courses": ["AMAT2050"]},
            ],
        }
    }
    courses = {
        "AMAT2050": _course("AMAT2050", "completed"),
        "AMAT2320": _course("AMAT2320", "completed"),
    }

    result = evaluate_requirement_group(rule, courses)

    allocations = {
        section["key"]: [
            cell["course_code"]
            for cell in section["cells"]
            if cell["allocation_status"] == "counted"
        ]
        for section in result["sections"]
    }
    assert result["current"]["satisfied"] is True
    assert allocations == {"broad": ["AMAT2320"], "strict": ["AMAT2050"]}


def test_evaluator_enforces_credit_and_prefix_constraints():
    rule = {
        "rule_tree": {
            "type": "choose",
            "key": "major_electives",
            "min_courses": 5,
            "min_credits": 15,
            "courses": ["AMAT1510", "AMAT2050", "AMAT2320", "AMAT2380", "SEEN3020"],
            "constraints": [{"type": "course_prefix", "value": "AMAT", "min_courses": 4}],
        }
    }
    courses = {
        code: _course(code, "completed")
        for code in ["AMAT1510", "AMAT2050", "AMAT2320", "AMAT2380", "SEEN3020"]
    }

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is True
    assert result["current"]["counted_courses"] == 5
    assert result["current"]["counted_credits"] == 15


def test_evaluator_warns_when_credit_threshold_cannot_be_proven():
    rule = {
        "rule_tree": {
            "type": "choose",
            "key": "major_electives",
            "min_courses": 1,
            "min_credits": 3,
            "courses": ["MICS1010"],
        }
    }
    courses = {"MICS1010": _course("MICS1010", "completed", credits=None, source=None)}

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is False
    assert result["warnings"] == ["missing_credit:MICS1010"]


def test_evaluator_supports_credit_only_elective_thresholds():
    rule = {
        "rule_tree": {
            "type": "choose",
            "key": "major_electives",
            "kind": "elective",
            "min_credits": 6,
            "courses": ["SMMG2030", "SMMG2640"],
        }
    }
    courses = {
        "SMMG2030": _course("SMMG2030", "completed"),
        "SMMG2640": _course("SMMG2640", "completed"),
    }

    result = evaluate_requirement_group(rule, courses)

    assert result["current"]["satisfied"] is True
    assert result["current"]["required_courses"] is None
    assert result["current"]["counted_credits"] == 6
    assert result["current"]["required_credits"] == 6


def test_evaluator_prevents_reuse_across_required_and_elective_groups():
    groups = [
        {
            "key": "major_required",
            "rule": {
                "rule_tree": {
                    "type": "choose",
                    "key": "major_choice",
                    "min_courses": 1,
                    "courses": ["AIAA2205", "DSAA1001"],
                }
            },
        },
        {
            "key": "major_electives",
            "rule": {
                "rule_tree": {
                    "type": "choose",
                    "key": "major_electives",
                    "min_courses": 1,
                    "courses": ["AIAA2205"],
                }
            },
        },
    ]
    courses = {"AIAA2205": _course("AIAA2205", "completed")}

    result = evaluate_requirement_program(groups, courses)

    assert result[0]["current"]["satisfied"] is True
    assert result[1]["current"]["satisfied"] is False


def test_evaluator_allows_explicit_leaf_reuse():
    groups = [
        {
            "key": "major_required",
            "rule": {
                "rule_tree": {
                    "type": "choose",
                    "key": "major_choice",
                    "min_courses": 1,
                    "courses": ["AIAA2205"],
                }
            },
        },
        {
            "key": "major_electives",
            "rule": {
                "rule_tree": {
                    "type": "choose",
                    "key": "major_electives",
                    "allow_reuse": True,
                    "min_courses": 1,
                    "courses": ["AIAA2205"],
                }
            },
        },
    ]
    courses = {"AIAA2205": _course("AIAA2205", "completed")}

    result = evaluate_requirement_program(groups, courses)

    assert result[0]["current"]["satisfied"] is True
    assert result[1]["current"]["satisfied"] is True
```

- [ ] **Step 2: Run the seven focused tests and verify failure**

Run:

```bash
pytest tests/test_academic_curriculum_evaluator.py -v
```

Expected: at least the global assignment and missing-credit tests fail with the minimal evaluator.

- [ ] **Step 3: Replace per-leaf selection with deterministic backtracking**

Add:

```python
def _candidate_codes(leaf: dict[str, Any], courses_by_code: dict[str, dict]) -> list[str]:
    return [
        normalize_code(code)
        for code in leaf.get("courses", [])
        if normalize_code(code) in courses_by_code
    ]


def _constraints_satisfied(leaf: dict[str, Any], selected: tuple[str, ...], courses_by_code: dict[str, dict]) -> bool:
    if len(selected) < _required_courses(leaf):
        return False
    required_credits = _required_credits(leaf)
    credits = [courses_by_code[code].get("credits") for code in selected]
    if required_credits is not None and (any(value is None for value in credits) or sum(credits) < required_credits):
        return False
    for constraint in leaf.get("constraints", []):
        if constraint.get("type") == "course_prefix":
            prefix = normalize_code(constraint.get("value"))
            count = len([code for code in selected if code.startswith(prefix)])
            if count < int(constraint.get("min_courses") or 0):
                return False
    return True
```

Use `itertools.combinations()` to enumerate each choose leaf's possible subsets.
Allocate required leaves first, then recursively assign choose subsets across the
entire program without reusing reserved codes unless the leaf has
`allow_reuse: true`.

Generate subset sizes from `0` through the number of eligible candidates, not
only `min_courses`. This is required for credit-only leaves such as SMMG
electives and for mixed thresholds where extra low-credit courses may be needed.
Prune a branch when its remaining candidates cannot reach the missing course
count, credit total, or prefix constraint. Candidate pools come from the user's
eligible records, not from every PDF-listed course, so the search remains small.

Rank complete assignments by:

```python
(
    satisfied_leaf_count,
    constrained_leaf_satisfaction_count,
    tuple(1 if satisfied_by_leaf[leaf["_allocation_key"]] else 0 for leaf in leaves),
    satisfied_required_course_count,
    satisfied_credit_total,
)
```

Enumerate constrained leaves before broad leaves while retaining their original
PDF index for output. Keep the first allocation when scores are equal. Enumerate
candidate codes in normalized code order so equal-scoring output is stable.

- [ ] **Step 4: Mark duplicate candidates and missing credits**

When serializing cells:

```python
if code in selected_for_section:
    allocation_status = "planned" if course.get("record_status") == "planned" else "counted"
elif code in allocated_elsewhere:
    allocation_status = "excluded_duplicate"
elif course and course.get("credits") is None:
    allocation_status = "missing_credit"
else:
    allocation_status = "candidate"
```

Include:

```python
"counted_toward": allocated_elsewhere.get(code),
"credits": course.get("credits") if course else None,
"credit_source": course.get("credit_source") if course else None,
```

- [ ] **Step 5: Run evaluator tests**

Run:

```bash
pytest tests/test_academic_curriculum_evaluator.py -v
```

Expected: all evaluator tests pass.

- [ ] **Step 6: Commit the allocation solver**

```bash
git add app/services/academic_curriculum_evaluator.py tests/test_academic_curriculum_evaluator.py
git commit -m "feat: allocate curriculum courses without double counting"
```

---

### Task 5: Integrate Evaluated Rows into Academic Map Summary

**Files:**
- Modify: `app/services/academic_map_service.py`
- Modify: `tests/test_academic_map_summary.py`

- [ ] **Step 1: Add failing summary integration test**

Add beside the existing model imports:

```python
from app.models.course import Course
```

Append:

```python
def test_requirement_matrix_returns_current_and_projected_rule_tree_progress(app):
    with app.app_context():
        create_user(111, "matrix_rule_tree")
        program = CurriculumProgram(code="DSA", name_en="Data Science and Big Data Technology", cohort="2025", total_min_credits=120)
        db.session.add(program)
        db.session.flush()
        db.session.add(CurriculumRequirementGroup(
            program_id=program.id,
            key="fundamental_courses",
            name_en="Fundamental Courses",
            name_zh="基础课程",
            category="fundamental",
            min_courses=2,
            min_credits=6,
            rule={
                "rule_tree": {
                    "type": "all_of",
                    "children": [
                        {"type": "choose", "key": "calculus_i", "min_courses": 1, "courses": ["UFUG1102", "UFUG1105"]},
                        {"type": "choose", "key": "calculus_ii", "min_courses": 1, "courses": ["UFUG1103", "UFUG1106"]},
                    ],
                }
            },
            sort_order=1,
        ))
        db.session.add(UserAcademicProfile(user_id=111, cohort="2025", target_majors=["DSA"]))
        add_record(111, "UFUG1105", status="completed")
        add_record(111, "UFUG1106", status="planned")
        db.session.commit()

        summary = build_academic_map_summary(111)

    row = summary["requirement_matrix"][0]["rows"][0]
    assert row["current"]["satisfied"] is False
    assert row["projected"]["satisfied"] is True
    assert [section["key"] for section in row["sections"]] == ["calculus_i", "calculus_ii"]
    assert row["sections"][1]["projected"]["counted_courses"] == 1


def test_requirement_matrix_prefers_catalog_credit_over_imported_units(app):
    with app.app_context():
        create_user(112, "matrix_catalog_credit")
        program = CurriculumProgram(code="AI", name_en="Artificial Intelligence", cohort="2025", total_min_credits=120)
        db.session.add(program)
        db.session.flush()
        db.session.add(CurriculumRequirementGroup(
            program_id=program.id,
            key="major_electives",
            name_en="Major Electives",
            category="major_elective",
            rule={
                "rule_tree": {
                    "type": "choose",
                    "key": "major_electives",
                    "kind": "elective",
                    "min_courses": 1,
                    "min_credits": 4,
                    "courses": ["UFUG2601"],
                }
            },
        ))
        db.session.add(UserAcademicProfile(user_id=112, cohort="2025", target_majors=["AI"]))
        catalog_course = Course.query.filter_by(code="UFUG2601").one()
        catalog_course.credits = 4
        add_record(112, "UFUG2601", status="completed", units=3)
        db.session.commit()

        row = build_academic_map_summary(112)["requirement_matrix"][0]["rows"][0]

    cell = row["sections"][0]["cells"][0]
    assert row["current"]["counted_credits"] == 4
    assert cell["credits"] == 4
    assert cell["credit_source"] == "catalog"


def test_requirement_matrix_falls_back_to_imported_units_when_catalog_is_missing(app):
    with app.app_context():
        create_user(113, "matrix_record_credit")
        program = CurriculumProgram(code="AI", name_en="Artificial Intelligence", cohort="2025", total_min_credits=120)
        db.session.add(program)
        db.session.flush()
        db.session.add(CurriculumRequirementGroup(
            program_id=program.id,
            key="major_electives",
            name_en="Major Electives",
            category="major_elective",
            rule={
                "rule_tree": {
                    "type": "choose",
                    "key": "major_electives",
                    "kind": "elective",
                    "min_courses": 1,
                    "min_credits": 3,
                    "courses": ["TEST3000"],
                }
            },
        ))
        db.session.add(UserAcademicProfile(user_id=113, cohort="2025", target_majors=["AI"]))
        add_record(113, "TEST3000", status="completed", units=3)
        db.session.commit()

        row = build_academic_map_summary(113)["requirement_matrix"][0]["rows"][0]

    cell = row["sections"][0]["cells"][0]
    assert row["current"]["counted_credits"] == 3
    assert cell["credits"] == 3
    assert cell["credit_source"] == "record"
```

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```bash
pytest tests/test_academic_map_summary.py::test_requirement_matrix_returns_current_and_projected_rule_tree_progress -v
```

Expected: fail because response rows do not expose `current` or `projected`.

- [ ] **Step 3: Query normalized catalog facts**

In `academic_map_service.py`, replace the title-only catalog helper with:

```python
def _catalog_by_code() -> dict[str, dict]:
    return {
        _normalized_code(course.code): {
            "title": course.name,
            "credits": float(course.credits) if course.credits is not None else None,
        }
        for course in Course.query.filter(Course.is_deleted == False).all()
    }
```

Add:

```python
from app.services.academic_curriculum_evaluator import evaluate_requirement_program
```

- [ ] **Step 4: Extend recursive course-code collection**

Replace `_codes_from_rule()` with:

```python
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
    for key in ("items", "children", "constraints"):
        for item in rule.get(key, []) if isinstance(rule.get(key), list) else []:
            if isinstance(item, dict):
                codes.update(_codes_from_rule(item))
    if isinstance(rule.get("rule_tree"), dict):
        codes.update(_codes_from_rule(rule["rule_tree"]))
    return {code for code in codes if code}
```

This keeps MCGA candidate lookup and shared-major tags working for `rule_tree`.

- [ ] **Step 5: Build evaluator course inputs**

Add:

```python
def _evaluation_courses(
    records_by_code: dict[str, UserCourseRecord],
    catalog_by_code: dict[str, dict],
    shared_map: dict[str, list[str]],
) -> dict[str, dict]:
    codes = set(catalog_by_code) | set(records_by_code)
    result = {}
    for code in codes:
        record = records_by_code.get(code)
        catalog = catalog_by_code.get(code, {})
        if catalog.get("credits") is not None:
            credits = catalog["credits"]
            credit_source = "catalog"
        elif record and record.units is not None:
            credits = _units(record)
            credit_source = "record"
        else:
            credits = None
            credit_source = None
        result[code] = {
            "course_code": code,
            "title": (clean_copied_status_text(record.course_title) if record else None) or catalog.get("title"),
            "record_status": record.status if record else None,
            "credits": credits,
            "credit_source": credit_source,
            "shared_majors": shared_map.get(code, []),
        }
    return result
```

- [ ] **Step 6: Delegate matrix rows to evaluator**

In `_build_requirement_matrix()`:

```python
    records_by_code = _record_by_code(records)
    catalog_by_code = _catalog_by_code()
    shared_map = _shared_major_map(programs)
    evaluation_courses = _evaluation_courses(records_by_code, catalog_by_code, shared_map)
```

Before iterating over rows, evaluate all groups together:

```python
        groups = program.requirement_groups.order_by(CurriculumRequirementGroup.sort_order.asc()).all()
        evaluated_groups = evaluate_requirement_program(
            [
                {
                    "key": group.key,
                    "rule": group.rule or {},
                    "min_courses": group.min_courses,
                    "min_credits": group.min_credits,
                }
                for group in groups
            ],
            evaluation_courses,
        )
```

For each paired group result:

```python
        for group, evaluated in zip(groups, evaluated_groups):
            rows.append({
                "key": group.key,
                "name_en": group.name_en,
                "name_zh": group.name_zh,
                "category": group.category,
                "current": evaluated["current"],
                "projected": evaluated["projected"],
                "sections": evaluated["sections"],
                "warnings": evaluated["warnings"],
                **_legacy_row_aliases(evaluated),
                "detail": {
                    "min_courses": group.min_courses,
                    "min_credits": group.min_credits,
                    "rule": group.rule or {},
                },
            })
```

Keep compatibility aliases so the deployed test backend remains consumable by
the current frontend during rollout. Add:

```python
def _legacy_cell_status(cell: dict) -> str:
    if cell.get("record_status") == UserCourseRecord.STATUS_COMPLETED:
        return "done"
    if cell.get("record_status") in {UserCourseRecord.STATUS_IN_PROGRESS, UserCourseRecord.STATUS_PLANNED}:
        return "now"
    return "choice"


def _legacy_row_aliases(evaluated: dict) -> dict:
    all_cells = []
    seen = set()
    for section in evaluated["sections"]:
        for cell in section["cells"]:
            code = cell["course_code"]
            if code in seen:
                continue
            seen.add(code)
            all_cells.append({**cell, "status": _legacy_cell_status(cell)})
    visible_cells = all_cells[:4]
    if len(all_cells) > 4:
        visible_cells.append({
            "kind": "more",
            "label": f"+{len(all_cells) - 4} more",
            "status": "more",
            "hidden_count": len(all_cells) - 4,
        })
    current = evaluated["current"]
    return {
        "progress_label": f"{current['counted_courses']} / {current['required_courses'] or '-'}",
        "all_cells": all_cells,
        "visible_cells": visible_cells,
    }
```

These aliases can remain after the frontend upgrade; they are compatibility
fields, not the source of truth.

- [ ] **Step 7: Keep legacy rule fallback covered**

Retain existing tests for flattened `required_courses`, `choices`, and `electives`. Update assertions only where the richer response intentionally changes shape. The old payload must still produce meaningful sections during rollout.

- [ ] **Step 8: Run summary tests**

Run:

```bash
pytest tests/test_academic_map_summary.py -v
```

Expected: all tests pass, including old legacy tests and the new rule-tree integration test.

- [ ] **Step 9: Commit service integration**

```bash
git add app/services/academic_map_service.py tests/test_academic_map_summary.py
git commit -m "feat: expose evaluated academic requirement progress"
```

---

### Task 6: Migrate and Validate Every Bundled PDF Rule

**Files:**
- Modify: `app/data/curriculum_requirements.json`
- Create: `tests/test_bundled_curriculum_requirements.py`

- [ ] **Step 1: Add JSON audit helpers and known-correction assertions**

Create:

```python
import json
from pathlib import Path


PAYLOAD = json.loads(
    (Path(__file__).resolve().parents[1] / "app" / "data" / "curriculum_requirements.json").read_text(encoding="utf-8")
)


def _program(code, cohort):
    for program in PAYLOAD["programs"]:
        cohorts = program.get("cohorts") or [program.get("cohort")]
        if program["code"] == code and cohort in cohorts:
            return program
    raise AssertionError(f"missing program {code} {cohort}")


def _group(code, cohort, key):
    program = _program(code, cohort)
    return next(group for group in program["requirement_groups"] if group["key"] == key)


def _leaf(group, key):
    def walk(node):
        if node.get("key") == key:
            return node
        for child in node.get("children", []):
            found = walk(child)
            if found:
                return found
        return None

    found = walk(group["rule"]["rule_tree"])
    assert found is not None, f"missing leaf {key}"
    return found


def test_ai_2025_linear_algebra_is_not_fixed():
    group = _group("AI", "2025", "fundamental_courses")
    fixed = _leaf(group, "fixed_courses")
    linear_algebra = _leaf(group, "linear_algebra")
    assert "UFUG2103" not in fixed["courses"]
    assert linear_algebra["courses"] == ["UFUG2102", "UFUG2103"]


def test_amat_2024_chemistry_rule_matches_pdf():
    group = _group("AMAT", "2024", "fundamental_courses")
    fixed = _leaf(group, "fixed_courses")
    chemistry = _leaf(group, "chemistry")
    assert "UFUG1303" in fixed["courses"]
    assert chemistry["courses"] == ["UFUG1301", "UFUG1302"]


def test_mics_2025_major_electives_require_eleven_courses_and_31_credits():
    electives = _leaf(_group("MICS", "2025", "major_electives"), "major_electives")
    assert electives["min_courses"] == 11
    assert electives["min_credits"] == 31


def test_amat_2024_major_electives_require_four_amat_courses():
    electives = _leaf(_group("AMAT", "2024", "major_electives"), "major_electives")
    assert electives["constraints"] == [{"type": "course_prefix", "value": "AMAT", "min_courses": 4}]
```

- [ ] **Step 2: Add source inventory coverage**

Append:

```python
def test_bundled_payload_covers_every_audited_pdf_source():
    assert {program["source_file"] for program in PAYLOAD["programs"]} == {
        "AI 23 24.pdf",
        "AI 25.pdf",
        "Curriculum Requirements - DSBD - 2023 cohort.pdf",
        "DSA 24.pdf",
        "DSA 25.pdf",
        "AMAT Curriculum Updated.pdf",
        "Curriculum Requirement - MSE - 2024 cohort - updated on May 19.pdf",
        "SMMG 23 24.pdf",
        "SMMG 25.pdf",
        "FTEC 25.pdf",
        "ROAS 25.pdf",
        "MICS 25.pdf",
        "SEEN 26.pdf",
    }


def test_bundled_payload_uses_rule_trees_without_flattened_rule_keys():
    for program in PAYLOAD["programs"]:
        for group in program["requirement_groups"]:
            assert set(group["rule"]) == {"rule_tree"}
```

- [ ] **Step 3: Run the payload tests and verify failure**

Run:

```bash
pytest tests/test_bundled_curriculum_requirements.py -v
```

Expected: fail because bundled groups still use flattened rules and the AI group key is still split incorrectly.

- [ ] **Step 4: Rebuild every fundamental group from the PDF audit**

In `app/data/curriculum_requirements.json`, replace flattened fundamental rules with `rule_tree`. Use these exact leaf groups:

Every migrated leaf must include stable `key`, `label_en`, and `label_zh`
properties. Use concise bilingual labels such as `Calculus I` / `微积分 I`,
`Physics I` / `物理 I`, `Fixed courses` / `固定课程`, and `Major electives` /
`专业选修`.

| Program | Cohort | Leaves |
| --- | --- | --- |
| AI | 2023-24 | fixed `UFUG1601, UFUG2104`; programming `UFUG2601 / UFUG2602`; calculus I; calculus II; linear algebra; science `UFUG1301, UFUG1302, UFUG1401, UFUG1501, UFUG1503` |
| AI | 2025-26 | fixed `UFUG1601, UFUG2602, UFUG2104`; calculus I; calculus II; linear algebra; science `UFUG1301, UFUG1302, UFUG1401, UFUG1501, UFUG1503, UFUG2106` |
| DSA | 2023 | fixed `UFUG1601, UFUG2601, UFUG2103, UFUG2104`; calculus I; calculus II; science `UFUG1301, UFUG1302, UFUG1401, UFUG1501, UFUG1502, UFUG1503, UFUG1504` min `2`; interdisciplinary `UFUG1701, UFUG1801, UFUG1811`; discrete math `UFUG2106 / DSAA2088` |
| DSA | 2024 | fixed `UFUG2104, UFUG2601`; calculus I; calculus II; science `UFUG1301, UFUG1302, UFUG1303, UFUG1401, UFUG1402, UFUG1403, UFUG1501, UFUG1502, UFUG1503, UFUG1504` min `2`; intro CS `UFUG1601 / UFUG1603`; interdisciplinary `UFUG1701, UFUG1801, UFUG1811`; discrete math `UFUG2106 / DSAA2088`; linear algebra `UFUG2102 / UFUG2103` |
| DSA | 2025-26 | fixed `UFUG2104, UFUG2601, UFUG2602`; calculus I; calculus II; science same as DSA 2024 min `2`; intro CS `UFUG1601 / UFUG1603`; discrete math `UFUG2106 / DSAA2088`; linear algebra `UFUG2102 / UFUG2103` |
| AMAT | 2024 | fixed `UFUG1303, UFUG1401, UFUG2101, UFUG2104`; programming `UFUG1601, UFUG1603, UFUG2601`; calculus I; calculus II; chemistry `UFUG1301 / UFUG1302`; physics I; physics II; linear algebra |
| AMAT | 2025 | fixed `UFUG1302, UFUG1303, UFUG1401, UFUG2101, UFUG2104`; programming `UFUG1601, UFUG1602, UFUG2601`; calculus I; calculus II; physics I; physics II; linear algebra |
| SMMG | 2023-24 | fixed `UFUG2101, UFUG1403, UFUG1801`; programming `UFUG1601, UFUG1602, UFUG2601`; chemistry `UFUG1301 / UFUG1302`; physics I; physics II; calculus I; calculus II; linear algebra |
| SMMG | 2025-26 | fixed `UFUG1801`; intro programming `UFUG1601, UFUG1602, UFUG1603, UFUG2601`; advanced programming `UFUG2602, UFUG2603, UFUG2101`; science `UFUG1301, UFUG1302, UFUG1401, UFUG1403`; physics I; physics II; calculus I; calculus II; linear algebra |
| FTEC | 2025 | fixed `UFUG1601, UFUG2101, UFUG2103, UFUG2105, UFUG2106`; calculus I; calculus II; programming `UFUG1602 / UFUG2601` |
| ROAS | 2025 | fixed `UFUG1601, UFUG2101, UFUG2104`; programming `UFUG2601 / UFUG2602`; physics I; physics II; calculus I; calculus II; linear algebra |
| MICS | 2025 | fixed `UFUG2601`; calculus I; calculus II; mathematics `UFUG2101, UFUG2102, UFUG2103, UFUG2104` min `2`; physics I; physics II |
| SEE | 2026 | fixed `UFUG1301, UFUG2101, UFUG2102, UFUG2104`; programming `UFUG1601 / UFUG2601`; physics I; physics II; calculus I; calculus II |

Use these common pools:

```text
calculus I: UFUG1102, UFUG1105
calculus II: UFUG1103, UFUG1106
physics I: UFUG1501, UFUG1503
physics II: UFUG1502, UFUG1504
linear algebra: UFUG2102, UFUG2103
```

Every leaf defaults to `min_courses: 1` unless the table states another minimum.

- [ ] **Step 5: Rebuild major-required choice groups**

Use:

| Program | Cohort | Choice leaves |
| --- | --- | --- |
| DSA | all bundled | intro major `DSAA1001 / AIAA2205`; machine learning `DSAA2011 / AIAA3111`; fixed courses remain separate |
| AMAT | 2024-25 | capstone `AMAT4901 / AMAT4095`; fixed courses remain separate |
| SMMG | 2023-24 | training `SMMG3000 / SMMG3010`; capstone `SMMG4901 / SMMG4960`; fixed courses remain separate |
| SMMG | 2025-26 | training `SMMG3000 / SMMG3010`; probability `DSAA1085 / UFUG2104`; capstone `SMMG4901 / SMMG4960`; fixed courses remain separate |
| SEE | 2026 | materials `SMMG2640 / AMAT3060`; fixed courses remain separate |

Programs not listed have one fixed required leaf.

- [ ] **Step 6: Rebuild major-elective leaves**

For each existing PDF-listed elective pool, create one `choose` leaf named
`major_electives` with `"kind": "elective"`. Preserve PDF course order and
configure:

| Program | Cohort | Threshold |
| --- | --- | --- |
| AI | all bundled | `min_courses: 8`, `min_credits: 24` |
| DSA | all bundled | `min_courses: 8`, `min_credits: 24` |
| AMAT | 2025 | `min_courses: 5`, `min_credits: 15` |
| AMAT | 2024 | `min_courses: 5`, `min_credits: 15`, plus `course_prefix AMAT min_courses: 4` |
| SMMG | all bundled | `min_credits: 18` |
| FTEC | 2025 | `min_courses: 5`, `min_credits: 15` |
| ROAS | 2025 | `min_courses: 7`, `min_credits: 21` |
| MICS | 2025 | `min_courses: 11`, `min_credits: 31` |
| SEE | 2026 | `min_courses: 7`, `min_credits: 21` |

The default non-reuse allocator prevents DSA courses already allocated as
required from being counted again as electives.

Merge AI 2025-26's old `fundamental_required` and `fundamental_choices` payload
rows into one `fundamental_courses` row. Do not leave bundled legacy rows behind;
the legacy evaluator exists only for database rollout compatibility.

- [ ] **Step 7: Add structural assertions for every program/cohort**

Append parameterized tests that assert leaf keys and thresholds for:

```python
[
    ("AI", "2023"), ("AI", "2024"), ("AI", "2025"), ("AI", "2026"),
    ("DSA", "2023"), ("DSA", "2024"), ("DSA", "2025"), ("DSA", "2026"),
    ("AMAT", "2024"), ("AMAT", "2025"),
    ("SMMG", "2023"), ("SMMG", "2024"), ("SMMG", "2025"), ("SMMG", "2026"),
    ("FTEC", "2025"), ("ROAS", "2025"), ("MICS", "2025"), ("SEE", "2026"),
]
```

Use explicit expected leaf-key lists per cohort family. Do not use a generated
snapshot that could bless an incorrect migration.

- [ ] **Step 8: Run payload and sync tests**

Run:

```bash
pytest tests/test_bundled_curriculum_requirements.py tests/test_academic_curriculum_sync.py -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit the audited payload**

```bash
git add app/data/curriculum_requirements.json tests/test_bundled_curriculum_requirements.py
git commit -m "data: migrate academic curricula to audited rule trees"
```

---

### Task 7: Complete Backend Regression Verification and Deploy Test Backend

**Files:**
- No planned edits. If verification reveals a scoped bug, add a failing
  regression test beside the affected subsystem before applying the fix.

- [ ] **Step 1: Run the focused Academic Map suite**

Run:

```bash
pytest tests/test_academic_curriculum_evaluator.py tests/test_academic_curriculum_sync.py tests/test_bundled_curriculum_requirements.py tests/test_academic_map_summary.py tests/test_academic_map_routes.py tests/test_academic_map_models.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run the full backend suite**

Run:

```bash
pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Confirm backend worktree contents**

Run:

```bash
git status --short
git log --oneline -n 8
```

Expected: no uncommitted backend files; recent commits include Tasks 1 through 6.

- [ ] **Step 4: Push backend main**

Run:

```bash
git push origin main
```

Expected: push succeeds. Wait for the automatic deployment of
`https://dev.unikorn.axfff.com`.

- [ ] **Step 5: Smoke-check the deployed backend**

Use an authenticated existing local frontend session during Task 10, or use the
project's established authenticated API workflow. Confirm `/api/academic-map/summary`
returns rows containing `current`, `projected`, `sections`, and `warnings`.

---

### Task 8: Update Frontend Types and Bilingual Copy

**Files:**
- Modify: `types/academic-map.ts`
- Modify: `i18n/locales/zh.json`
- Modify: `i18n/locales/en.json`

- [ ] **Step 1: Replace flattened requirement types**

In `types/academic-map.ts`, define:

```typescript
export type AcademicAllocationStatus =
  | 'counted'
  | 'candidate'
  | 'planned'
  | 'excluded_duplicate'
  | 'missing_credit'

export interface AcademicRequirementProgress {
  satisfied: boolean
  counted_courses: number
  required_courses?: number | null
  counted_credits: number
  required_credits?: number | null
}

export interface AcademicRequirementCell {
  kind: 'course'
  course_code: string
  title?: string | null
  record_status?: AcademicCourseStatus | null
  allocation_status: AcademicAllocationStatus
  counted_toward?: string | null
  credits?: number | null
  credit_source?: 'catalog' | 'record' | null
  shared_majors?: string[]
}

export type AcademicRequirementSectionKind = 'required' | 'choice' | 'elective'

export interface AcademicRequirementSection {
  key: string
  kind: AcademicRequirementSectionKind
  label_en: string
  label_zh?: string | null
  current: AcademicRequirementProgress
  projected: AcademicRequirementProgress
  cells: AcademicRequirementCell[]
}

export interface AcademicRequirementRow {
  key: string
  name_en: string
  name_zh?: string | null
  category: string
  current: AcademicRequirementProgress
  projected: AcademicRequirementProgress
  sections: AcademicRequirementSection[]
  warnings: string[]
  detail: {
    min_courses?: number | null
    min_credits?: number | null
    rule: Record<string, unknown>
  }
}
```

- [ ] **Step 2: Add bilingual requirement progress copy**

Add under `academicMap.requirements` in `zh.json`:

```json
"currentProgress": "当前 {courses} / {requiredCourses} 门 · {credits} / {requiredCredits} 学分",
"currentCourseProgress": "当前 {courses} / {requiredCourses} 门",
"projectedProgress": "计划后 {courses} / {requiredCourses} 门 · {credits} / {requiredCredits} 学分",
"projectedCourseProgress": "计划后 {courses} / {requiredCourses} 门",
"creditProgress": "当前 {credits} / {requiredCredits} 学分",
"projectedCreditProgress": "计划后 {credits} / {requiredCredits} 学分",
"moreSections": "+{count} 更多分组",
"creditToConfirm": "学分待核对",
"countedToward": "已计入：{section}",
"sectionKinds": {
  "required": "必修",
  "choice": "选项",
  "elective": "选修"
},
"status": {
  "completed": "已修",
  "in_progress": "在修",
  "planned": "计划",
  "candidate": "候选",
  "excluded_duplicate": "已计入其他要求",
  "missing_credit": "学分待核对"
}
```

Add the matching keys in `en.json`:

```json
"currentProgress": "Current {courses} / {requiredCourses} courses · {credits} / {requiredCredits} credits",
"currentCourseProgress": "Current {courses} / {requiredCourses} courses",
"projectedProgress": "With plan {courses} / {requiredCourses} courses · {credits} / {requiredCredits} credits",
"projectedCourseProgress": "With plan {courses} / {requiredCourses} courses",
"creditProgress": "Current {credits} / {requiredCredits} credits",
"projectedCreditProgress": "With plan {credits} / {requiredCredits} credits",
"moreSections": "+{count} more groups",
"creditToConfirm": "Credit to confirm",
"countedToward": "Counted toward: {section}",
"sectionKinds": {
  "required": "Required",
  "choice": "Choice",
  "elective": "Elective"
},
"status": {
  "completed": "Completed",
  "in_progress": "In progress",
  "planned": "Planned",
  "candidate": "Candidate",
  "excluded_duplicate": "Counted elsewhere",
  "missing_credit": "Credit to confirm"
}
```

- [ ] **Step 3: Run i18n checks**

Run from `/Users/mount/Desktop/Programming/unikorn/front-end`:

```bash
npm run i18n:check
```

Expected: pass.

- [ ] **Step 4: Commit frontend contract and copy**

```bash
git add types/academic-map.ts i18n/locales/zh.json i18n/locales/en.json
git commit -m "feat: type evaluated academic requirement progress"
```

---

### Task 9: Render Rule-Tree Progress in the Requirement Matrix

**Files:**
- Modify: `components/academic-map/RequirementMatrix.vue`

- [ ] **Step 1: Replace flattened helper functions**

Remove fallback synthesis based on `visible_cells`, `all_cells`, and
`progress_label`. Add:

```typescript
const cellLabel = (cell: AcademicRequirementCell) => cell.course_code

const rowSections = (row: AcademicRequirementRow) => row.sections || []

const visibleSectionSummaries = (row: AcademicRequirementRow) => rowSections(row).slice(0, 3)

const hiddenSectionCount = (row: AcademicRequirementRow) => Math.max(rowSections(row).length - 3, 0)

const hasProjectedChange = (current: AcademicRequirementProgress, projected: AcademicRequirementProgress) => {
  return current.satisfied !== projected.satisfied
    || current.counted_courses !== projected.counted_courses
    || current.counted_credits !== projected.counted_credits
}

const progressLabel = (progress: AcademicRequirementProgress, projected = false) => {
  if (progress.required_courses && progress.required_credits) {
    return t(projected ? 'academicMap.requirements.projectedProgress' : 'academicMap.requirements.currentProgress', {
      courses: progress.counted_courses,
      requiredCourses: progress.required_courses,
      credits: progress.counted_credits,
      requiredCredits: progress.required_credits,
    })
  }
  if (progress.required_courses) {
    return t(projected ? 'academicMap.requirements.projectedCourseProgress' : 'academicMap.requirements.currentCourseProgress', {
      courses: progress.counted_courses,
      requiredCourses: progress.required_courses,
    })
  }
  return t(projected ? 'academicMap.requirements.projectedCreditProgress' : 'academicMap.requirements.creditProgress', {
    credits: progress.counted_credits,
    requiredCredits: progress.required_credits || '-',
  })
}

const cellState = (cell: AcademicRequirementCell) => {
  if (cell.allocation_status === 'counted' && cell.record_status) return cell.record_status
  return cell.allocation_status
}
```

Import `AcademicRequirementProgress` in the existing type import. Remove the old
`cellLabel()` branch for `kind === 'more'` and remove `sectionProgress()`.

- [ ] **Step 2: Replace collapsed-row rendering**

Render the row copy with:

```vue
<div class="am-row-copy">
  <span class="am-category">{{ row.category }}</span>
  <h3>{{ rowTitle(row) }}</h3>
  <small>{{ progressLabel(row.current) }}</small>
  <small v-if="hasProjectedChange(row.current, row.projected)" class="am-projected-copy">
    {{ progressLabel(row.projected, true) }}
  </small>
</div>
```

In the drawer header, replace `row.progress_label` with:

```vue
<span>{{ progressLabel(row.current) }}</span>
```

Render chips with:

```vue
<div class="am-section-strip">
  <span
    v-for="section in visibleSectionSummaries(row)"
    :key="section.key"
    :class="['am-section-chip', `is-${section.kind}`]"
  >
    <strong>{{ sectionLabel(section) }}</strong>
    <small>{{ section.current.counted_courses }} / {{ section.current.required_courses || '-' }}</small>
  </span>
  <span v-if="hiddenSectionCount(row)" class="am-section-chip is-more">
    <strong>{{ t('academicMap.requirements.moreSections', { count: hiddenSectionCount(row) }) }}</strong>
  </span>
</div>
```

Remove the collapsed flattened course lane. Keep the right-side current summary
pill:

```vue
<div class="am-progress-pill">
  {{ row.current.satisfied ? t('academicMap.requirements.satisfied') : progressLabel(row.current) }}
</div>
```

Add bilingual `satisfied` text:

```json
"satisfied": "已满足"
```

```json
"satisfied": "Satisfied"
```

- [ ] **Step 3: Replace expanded leaf rendering**

In each expanded section header:

```vue
<small>{{ progressLabel(section.current) }}</small>
<small v-if="hasProjectedChange(section.current, section.projected)" class="am-projected-copy">
  {{ progressLabel(section.projected, true) }}
</small>
```

Render cells with:

```vue
<span
  v-for="cell in section.cells"
  :key="`${section.key}-${cell.course_code}`"
  :class="['am-expanded-cell', `is-${cellState(cell)}`]"
>
  <strong>{{ cellLabel(cell) }}</strong>
  <small>{{ cellTitle(cell) }}</small>
  <small class="am-cell-status">{{ t(`academicMap.requirements.status.${cellState(cell)}`) }}</small>
  <small v-if="cell.allocation_status === 'excluded_duplicate' && cell.counted_toward">
    {{ t('academicMap.requirements.countedToward', { section: cell.counted_toward }) }}
  </small>
  <em v-if="cell.shared_majors && cell.shared_majors.length > 1">{{ cell.shared_majors.join('+') }}</em>
</span>
```

Render row warnings below the leaf list:

```vue
<div v-if="row.warnings.length" class="am-warning-list">
  <span v-for="warning in row.warnings" :key="warning">
    {{ warning.startsWith('missing_credit:') ? t('academicMap.requirements.creditToConfirm') : warning }}
  </span>
</div>
```

- [ ] **Step 4: Add planned, duplicate, warning, and projected styles**

Use existing theme variables:

```scss
.am-projected-copy {
  color: var(--interactive-active) !important;
  display: block;
  margin-top: 3px;
}

.am-cell-status {
  color: var(--text-secondary);
  font-weight: 750;
}

.is-in_progress {
  background: color-mix(in srgb, var(--semantic-info) 11%, transparent);
  border-color: color-mix(in srgb, var(--semantic-info) 38%, transparent);
}

.is-completed {
  background: color-mix(in srgb, var(--semantic-success) 10%, transparent);
  border-color: color-mix(in srgb, var(--semantic-success) 36%, transparent);
}

.is-planned {
  background: color-mix(in srgb, var(--interactive-primary) 8%, transparent);
  border-color: color-mix(in srgb, var(--interactive-primary) 30%, transparent);
}

.is-candidate {
  background: var(--surface-primary);
  border-color: var(--border-primary);
}

.is-excluded_duplicate,
.is-missing_credit {
  background: color-mix(in srgb, var(--semantic-warning) 10%, transparent);
  border-color: color-mix(in srgb, var(--semantic-warning) 35%, transparent);
}

.am-warning-list {
  display: grid;
  gap: 4px;

  span {
    color: var(--semantic-warning);
    font-size: 0.76rem;
  }
}
```

- [ ] **Step 5: Run frontend checks**

Run:

```bash
npm run i18n:check
npm run build
```

Expected: both commands pass.

- [ ] **Step 6: Commit frontend rendering**

```bash
git add components/academic-map/RequirementMatrix.vue i18n/locales/zh.json i18n/locales/en.json
git commit -m "feat: render grouped academic requirement progress"
```

---

### Task 10: Verify the Full Story on localhost:3000

**Files:**
- No planned edits. If browser verification reveals a scoped bug, add the
  smallest targeted fix and rerun the checks from Tasks 7 through 10.

- [ ] **Step 1: Confirm the test backend deployment is live**

Use the authenticated local frontend session after the backend push. Ensure the
Academic Map summary request succeeds against `https://dev.unikorn.axfff.com`.

- [ ] **Step 2: Start the frontend on the required port**

Run from `/Users/mount/Desktop/Programming/unikorn/front-end`:

```bash
npm run dev -- --host 127.0.0.1 --port 3000
```

Expected: Nuxt starts on `http://127.0.0.1:3000`.

- [ ] **Step 3: Use Browser plugin verification**

Open:

```text
http://127.0.0.1:3000/academic-map
```

Verify:

- rows no longer show flattened labels such as `12 choose 4`;
- DSA 2025 fundamental courses show independent groups including `2 choose 1`,
  `2 choose 1`, and `10 choose 2`;
- completed and in-progress courses count toward current progress;
- planned courses only affect projected progress;
- expanded rows show separate planned styling;
- duplicate elective candidates show their counted destination;
- missing-credit warnings are readable;
- Chinese and English locale views both render correctly;
- no browser console errors appear.

- [ ] **Step 4: Stop the frontend dev server**

Stop the `npm run dev` process with `Ctrl-C`.

Expected: port `3000` is free for the user.

- [ ] **Step 5: Confirm frontend repository status**

Run:

```bash
git status --short
git log --oneline -n 5
```

Expected: only intended frontend commits remain, with no generated output or
running local server.

---

## Final Verification Checklist

Run from `/Users/mount/Desktop/Programming/unikorn/back-end`:

```bash
pytest -q
git status --short
```

Run from `/Users/mount/Desktop/Programming/unikorn/front-end`:

```bash
npm run i18n:check
npm run build
git status --short
```

Confirm:

- all 13 PDF sources are represented;
- all 18 program/cohort combinations are covered;
- current progress excludes planned-only courses;
- projected progress includes planned courses;
- one course cannot satisfy two leaves in the same program by default;
- AMAT 2024 prefix constraint is enforced;
- MICS 2025 uses `11 courses / 31 credits`;
- DSA required courses are excluded from elective reuse;
- backend is pushed to `main` and deployed to the test backend;
- frontend verification is complete on `localhost:3000`;
- the local dev server is stopped.
