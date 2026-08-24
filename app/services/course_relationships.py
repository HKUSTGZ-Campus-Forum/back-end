from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.course import Course
from app.models.course_domain import (
    CourseCatalogRequirement,
    CourseCatalogVersion,
    CourseRequirementEdge,
)
from app.services.course_domain import display_course_code, normalize_course_code


PARSER_VERSION = "20260824.1"
RELATION_FIELDS = {
    "prerequisite": "pre_requirement_raw",
    "corequisite": "co_requirement_raw",
    "exclusion": "exclusion_raw",
}
RELATION_CATEGORIES = {
    "prerequisite": 1,
    "corequisite": 2,
    "exclusion": 3,
}
RULE_SOURCE_PRIORITY = {
    "sis_course_catalog": 300,
    "course_catalog.json": 200,
    "course_prerequisites.json": 190,
    "legacy_course_row": 100,
    # Offering snapshots are intentionally not authoritative for catalog rules.
    "sisn": 10,
    "scheduler_offerings": 10,
}
COURSE_CODE_RE = re.compile(r"\b([A-Za-z]{4})\s*([0-9]{4}[A-Za-z]?)\b")
TOKEN_RE = re.compile(
    r"\b[A-Za-z]{4}\s*[0-9]{4}[A-Za-z]?\b|\bAND\b|\bOR\b|[()]",
    re.IGNORECASE,
)
LEADING_LABEL_RE = re.compile(
    r"^\s*(?:pre-?requisites?|co-?requisites?|exclusions?)\s*:\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedRequirement:
    raw_text: str | None
    normalized_text: str | None
    requirement_kind: str
    expression_json: dict[str, Any]
    course_codes: tuple[str, ...]


def extract_course_codes(value: Any) -> list[str]:
    result: list[str] = []
    for prefix, number in COURSE_CODE_RE.findall(str(value or "")):
        code = normalize_course_code(f"{prefix}{number}")
        if code and code not in result:
            result.append(code)
    return result


class _ExpressionParser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.index = 0

    def parse(self) -> dict[str, Any]:
        result = self._parse_or()
        if self.index != len(self.tokens):
            raise ValueError("unconsumed requirement tokens")
        return result

    def _parse_or(self) -> dict[str, Any]:
        items = [self._parse_and()]
        while self._peek("OR"):
            self.index += 1
            items.append(self._parse_and())
        return _join_expression("OR", items)

    def _parse_and(self) -> dict[str, Any]:
        items = [self._parse_atom()]
        while self._peek("AND"):
            self.index += 1
            items.append(self._parse_atom())
        return _join_expression("AND", items)

    def _parse_atom(self) -> dict[str, Any]:
        if self.index >= len(self.tokens):
            raise ValueError("missing requirement operand")
        token = self.tokens[self.index]
        if token == "(":
            self.index += 1
            result = self._parse_or()
            if self.index >= len(self.tokens) or self.tokens[self.index] != ")":
                raise ValueError("unclosed requirement group")
            self.index += 1
            return result
        if token in {"AND", "OR", ")"}:
            raise ValueError("unexpected requirement operator")
        self.index += 1
        return {"course_code": normalize_course_code(token)}

    def _peek(self, value: str) -> bool:
        return self.index < len(self.tokens) and self.tokens[self.index] == value


def _join_expression(op: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        if item.get("op") == op:
            flattened.extend(item.get("items", []))
        else:
            flattened.append(item)
    return flattened[0] if len(flattened) == 1 else {"op": op, "items": flattened}


def parse_requirement(value: Any) -> ParsedRequirement:
    raw_text = str(value).strip() if value is not None else ""
    if not raw_text:
        return ParsedRequirement(None, None, "empty", {}, ())

    expression_text = LEADING_LABEL_RE.sub("", raw_text)
    token_matches = list(TOKEN_RE.finditer(expression_text))
    codes = extract_course_codes(expression_text)
    if not codes:
        return ParsedRequirement(raw_text, " ".join(raw_text.split()), "non_course", {}, ())

    leftovers: list[str] = []
    cursor = 0
    for match in token_matches:
        leftovers.append(expression_text[cursor:match.start()])
        cursor = match.end()
    leftovers.append(expression_text[cursor:])
    remaining = re.sub(r"[\s,;/&+.-]+", "", "".join(leftovers))

    expression: dict[str, Any] = {}
    kind = "mixed" if remaining else "course"
    if kind == "course":
        tokens = []
        for match in token_matches:
            token = match.group(0).strip()
            upper = token.upper()
            tokens.append(upper if upper in {"AND", "OR", "(", ")"} else normalize_course_code(token))
        try:
            expression = _ExpressionParser(tokens).parse()
        except ValueError:
            kind = "mixed"

    return ParsedRequirement(
        raw_text=raw_text,
        normalized_text=" ".join(raw_text.split()),
        requirement_kind=kind,
        expression_json=expression,
        course_codes=tuple(codes),
    )


def _version_order(version: CourseCatalogVersion) -> tuple[Any, ...]:
    imported = version.imported_at
    if isinstance(imported, datetime):
        imported_value = imported.isoformat()
    else:
        imported_value = str(imported or "")
    return (
        RULE_SOURCE_PRIORITY.get(version.source, 50),
        str(version.effective_from_semester_id or ""),
        imported_value,
        version.id or 0,
    )


def current_rule_catalog_version(course: Course | int) -> CourseCatalogVersion | None:
    course_id = course if isinstance(course, int) else course.id
    versions = CourseCatalogVersion.query.filter_by(course_id=course_id).all()
    eligible = [version for version in versions if RULE_SOURCE_PRIORITY.get(version.source, 50) > 10]
    return max(eligible, key=_version_order) if eligible else None


def _canonical_course_map(courses: Iterable[Course]) -> dict[str, Course]:
    groups: dict[str, list[Course]] = {}
    for course in courses:
        code = normalize_course_code(course.normalized_code or course.code)
        if code:
            groups.setdefault(code, []).append(course)
    return {
        code: max(
            matches,
            key=lambda course: (
                normalize_course_code(course.normalized_code) == code,
                normalize_course_code(course.code) == code and " " not in str(course.code or ""),
                bool(course.subject and course.catalog_number),
                course.id or 0,
            ),
        )
        for code, matches in groups.items()
    }


def replace_catalog_version_requirements(
    version: CourseCatalogVersion,
    *,
    course_by_code: dict[str, Course] | None = None,
) -> dict[str, Any]:
    existing_requirement_ids = [
        requirement_id
        for (requirement_id,) in db.session.query(CourseCatalogRequirement.id)
        .filter_by(catalog_version_id=version.id)
        .all()
    ]
    if existing_requirement_ids:
        CourseRequirementEdge.query.filter(
            CourseRequirementEdge.requirement_id.in_(existing_requirement_ids)
        ).delete(synchronize_session=False)
    CourseCatalogRequirement.query.filter_by(catalog_version_id=version.id).delete(
        synchronize_session=False,
    )
    target = db.session.get(Course, version.course_id)
    if target is None:
        return {"requirements": 0, "edges": 0, "unresolved": 0, "unresolved_codes": []}

    if course_by_code is None:
        course_by_code = _canonical_course_map(
            Course.query.filter_by(is_deleted=False).all()
        )

    counts: dict[str, Any] = {
        "requirements": 0,
        "edges": 0,
        "unresolved": 0,
        "unresolved_codes": [],
    }
    for relation_type, field_name in RELATION_FIELDS.items():
        parsed = parse_requirement(getattr(version, field_name))
        requirement = CourseCatalogRequirement(
            catalog_version_id=version.id,
            relation_type=relation_type,
            raw_text=parsed.raw_text,
            normalized_text=parsed.normalized_text,
            requirement_kind=parsed.requirement_kind,
            expression_json=parsed.expression_json,
            parser_version=PARSER_VERSION,
            source=version.source,
        )
        db.session.add(requirement)
        db.session.flush()
        counts["requirements"] += 1
        for code in parsed.course_codes:
            prerequisite = course_by_code.get(code)
            if prerequisite is None:
                counts["unresolved"] += 1
                if code not in counts["unresolved_codes"]:
                    counts["unresolved_codes"].append(code)
                continue
            db.session.add(CourseRequirementEdge(
                requirement_id=requirement.id,
                from_course_id=prerequisite.id,
                to_course_id=target.id,
                relation_type=relation_type,
                edge_role="expression" if parsed.expression_json else "reference",
            ))
            counts["edges"] += 1
    return counts


def _base_course(course: Course, course_by_code: dict[str, Course]) -> Course | None:
    code = normalize_course_code(course.normalized_code or course.code)
    match = re.fullmatch(r"([A-Z]{4}[0-9]{4})[A-Z]", code)
    return course_by_code.get(match.group(1)) if match else None


def _course_payload(course: Course) -> dict[str, Any]:
    return {
        "id": course.id,
        "code": normalize_course_code(course.normalized_code or course.code),
        "display_code": display_course_code(course.normalized_code or course.code),
        "title": course.canonical_title or course.name,
    }


def _relationship_records() -> tuple[dict[int, list[dict[str, Any]]], dict[str, Course]]:
    courses = Course.query.filter_by(is_deleted=False).all()
    course_by_code = _canonical_course_map(courses)
    versions_by_course: dict[int, list[CourseCatalogVersion]] = {}
    for version in CourseCatalogVersion.query.all():
        if RULE_SOURCE_PRIORITY.get(version.source, 50) > 10:
            versions_by_course.setdefault(version.course_id, []).append(version)
    requirements_by_version: dict[int, dict[str, CourseCatalogRequirement]] = {}
    for requirement in CourseCatalogRequirement.query.all():
        requirements_by_version.setdefault(requirement.catalog_version_id, {})[
            requirement.relation_type
        ] = requirement
    edges_by_requirement: dict[int, list[CourseRequirementEdge]] = {}
    for edge in CourseRequirementEdge.query.options(
        joinedload(CourseRequirementEdge.from_course)
    ).all():
        edges_by_requirement.setdefault(edge.requirement_id, []).append(edge)

    records_by_course: dict[int, list[dict[str, Any]]] = {}
    for course in courses:
        source_course = course
        versions = versions_by_course.get(course.id, [])
        if not versions:
            base = _base_course(course, course_by_code)
            if base is not None:
                source_course = base
                versions = versions_by_course.get(base.id, [])
        version = max(versions, key=_version_order) if versions else None
        relation_records: list[dict[str, Any]] = []
        for relation_type, field_name in RELATION_FIELDS.items():
            stored = requirements_by_version.get(version.id, {}).get(relation_type) if version else None
            if stored:
                parsed = ParsedRequirement(
                    raw_text=stored.raw_text,
                    normalized_text=stored.normalized_text,
                    requirement_kind=stored.requirement_kind,
                    expression_json=stored.expression_json or {},
                    course_codes=tuple(
                        normalize_course_code(edge.from_course.normalized_code or edge.from_course.code)
                        for edge in edges_by_requirement.get(stored.id, [])
                    ),
                )
            else:
                raw = getattr(version, field_name) if version else getattr(source_course, {
                    "pre_requirement_raw": "pre_requirement",
                    "co_requirement_raw": "co_requirement",
                    "exclusion_raw": "exclusion",
                }[field_name])
                parsed = parse_requirement(raw)
            related = [course_by_code[code] for code in parsed.course_codes if code in course_by_code]
            relation_records.append({
                "relation_type": relation_type,
                "raw_text": parsed.raw_text,
                "normalized_text": parsed.normalized_text,
                "requirement_kind": parsed.requirement_kind,
                "expression": parsed.expression_json,
                "courses": [_course_payload(item) for item in related],
                "course_codes": [normalize_course_code(item.normalized_code or item.code) for item in related],
                "source": version.source if version else "legacy_course_row",
                "source_version": version.source_version if version else None,
                "effective_from_semester_id": version.effective_from_semester_id if version else None,
                "imported_at": version.imported_at.isoformat() if version and version.imported_at else None,
                "is_fallback": version is None or version.source != "sis_course_catalog" or source_course.id != course.id,
            })
        records_by_course[course.id] = relation_records
    return records_by_course, course_by_code


def relationship_summary(course: Course) -> dict[str, Any]:
    records_by_course, course_by_code = _relationship_records()
    course_by_id = {item.id: item for item in course_by_code.values()}
    requirements = records_by_course.get(course.id, [])
    target_code = normalize_course_code(course.normalized_code or course.code)
    downstream: list[dict[str, Any]] = []
    for target_id, records in records_by_course.items():
        prerequisite = next((item for item in records if item["relation_type"] == "prerequisite"), None)
        if not prerequisite or target_code not in prerequisite["course_codes"]:
            continue
        target = course_by_id.get(target_id)
        if target is None:
            continue
        downstream.append({
            **_course_payload(target),
            "requirement": prerequisite["raw_text"],
            "source": prerequisite["source"],
            "source_version": prerequisite["source_version"],
            "is_fallback": prerequisite["is_fallback"],
        })
    downstream.sort(key=lambda item: item["code"])
    sources = {
        (item["source"], item["source_version"], item["imported_at"], item["is_fallback"])
        for item in requirements
    }
    source, source_version, imported_at, is_fallback = next(iter(sources)) if len(sources) == 1 else (
        "mixed", None, None, any(item["is_fallback"] for item in requirements)
    )
    return {
        "requirements": requirements,
        "downstream": downstream,
        "provenance": {
            "source": source,
            "source_version": source_version,
            "imported_at": imported_at,
            "is_fallback": is_fallback,
        },
    }


def _expression_lines(
    expression: dict[str, Any],
    *,
    target_id: str,
    relation_type: str,
    components: dict[str, dict[str, Any]],
    lines: list[dict[str, Any]],
    logic_counter: list[int],
) -> None:
    category = RELATION_CATEGORIES[relation_type]

    def connect(node: dict[str, Any], destination: str) -> None:
        code = normalize_course_code(node.get("course_code"))
        if code:
            if code in components:
                lines.append({"start_id": code, "end_id": destination, "category": category})
            return
        op = str(node.get("op") or "").upper()
        items = [item for item in node.get("items", []) if isinstance(item, dict)]
        if op not in {"AND", "OR"} or not items:
            return
        logic_counter[0] += 1
        logic_id = f"logic-{relation_type}-{logic_counter[0]}"
        components[logic_id] = {
            "id": logic_id,
            "node_type": op == "OR",
            "x_coordinate": 0,
            "y_coordinate": 0,
            "category": category,
        }
        for item in items:
            connect(item, logic_id)
        lines.append({"start_id": logic_id, "end_id": destination, "category": category})

    connect(expression, target_id)


def _layout_logic_components(
    components: dict[str, dict[str, Any]],
    lines: Iterable[dict[str, Any]],
) -> None:
    incoming: dict[str, list[str]] = {}
    outgoing: dict[str, list[str]] = {}
    for line in lines:
        incoming.setdefault(line["end_id"], []).append(line["start_id"])
        outgoing.setdefault(line["start_id"], []).append(line["end_id"])
    # Repeated passes also settle nested boolean groups.
    for _pass in range(4):
        for component_id, component in components.items():
            if component["category"] == 0:
                continue
            source_points = [
                components[source_id]
                for source_id in incoming.get(component_id, [])
                if source_id in components
            ]
            target_points = [
                components[target_id]
                for target_id in outgoing.get(component_id, [])
                if target_id in components
            ]
            if not source_points and not target_points:
                continue
            source_x = sum(point["x_coordinate"] for point in source_points) / len(source_points) if source_points else 0
            source_y = sum(point["y_coordinate"] for point in source_points) / len(source_points) if source_points else 0
            target_x = sum(point["x_coordinate"] for point in target_points) / len(target_points) if target_points else source_x
            target_y = sum(point["y_coordinate"] for point in target_points) / len(target_points) if target_points else source_y
            component["x_coordinate"] = round((source_x + target_x) / 2)
            component["y_coordinate"] = round((source_y + target_y) / 2)


def build_relationship_graph() -> dict[str, Any]:
    records_by_course, course_by_code = _relationship_records()
    active_courses = sorted(
        (course for course in course_by_code.values() if course.is_active),
        key=lambda course: normalize_course_code(course.normalized_code or course.code),
    )
    components: dict[str, dict[str, Any]] = {}
    courses_payload: list[dict[str, Any]] = []
    by_level: dict[int, list[Course]] = {}
    for course in active_courses:
        code = normalize_course_code(course.normalized_code or course.code)
        number_match = re.search(r"([0-9])", code[4:])
        level = int(number_match.group(1)) if number_match else 0
        by_level.setdefault(level, []).append(course)
        courses_payload.append({
            "course_code": code,
            "course_title_abbr": course.course_title_abbr,
            "course_title": course.canonical_title or course.name,
            "subject": course.subject,
        })
    for column, level in enumerate(sorted(by_level)):
        for row, course in enumerate(by_level[level]):
            code = normalize_course_code(course.normalized_code or course.code)
            components[code] = {
                "id": code,
                "node_type": None,
                "x_coordinate": column * 320,
                "y_coordinate": row * 132,
                "category": 0,
            }

    raw_lines: list[dict[str, Any]] = []
    logic_counter = [0]
    for course in active_courses:
        target_code = normalize_course_code(course.normalized_code or course.code)
        for record in records_by_course.get(course.id, []):
            if not record["course_codes"]:
                continue
            expression = record["expression"]
            if expression:
                _expression_lines(
                    expression,
                    target_id=target_code,
                    relation_type=record["relation_type"],
                    components=components,
                    lines=raw_lines,
                    logic_counter=logic_counter,
                )
            else:
                for code in record["course_codes"]:
                    if code in components:
                        raw_lines.append({
                            "start_id": code,
                            "end_id": target_code,
                            "category": RELATION_CATEGORIES[record["relation_type"]],
                        })

    deduplicated = {
        (line["start_id"], line["end_id"], line["category"]): line
        for line in raw_lines
        if line["start_id"] in components and line["end_id"] in components
    }
    _layout_logic_components(components, deduplicated.values())
    lines = []
    for line_id, key in enumerate(sorted(deduplicated), start=1):
        start_id, end_id, category = key
        start = components[start_id]
        end = components[end_id]
        lines.append({
            "id": line_id,
            "start_id": start_id,
            "end_id": end_id,
            "line_type": None,
            "x_coordinate": round((start["x_coordinate"] + end["x_coordinate"]) / 2),
            "category": category,
        })

    official_versions = [
        version for version in CourseCatalogVersion.query.filter_by(source="sis_course_catalog").all()
        if version.course_id in records_by_course
    ]
    latest = max(official_versions, key=_version_order) if official_versions else None
    fallback_relationship_count = sum(
        1
        for records in records_by_course.values()
        for record in records
        if record["raw_text"] and record["is_fallback"]
    )
    return {
        "components": list(components.values()),
        "lines": lines,
        "courses": courses_payload,
        "metadata": {
            "source": (
                "sis_course_catalog_with_fallback"
                if latest and fallback_relationship_count
                else "sis_course_catalog" if latest else "legacy_course_catalog"
            ),
            "source_version": latest.source_version if latest else None,
            "effective_from_semester_id": latest.effective_from_semester_id if latest else None,
            "imported_at": latest.imported_at.isoformat() if latest and latest.imported_at else None,
            "is_fallback": latest is None or fallback_relationship_count > 0,
            "course_count": len(courses_payload),
            "relationship_count": len(lines),
            "fallback_relationship_count": fallback_relationship_count,
        },
    }
