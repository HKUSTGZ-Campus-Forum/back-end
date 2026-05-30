from __future__ import annotations

import re
from typing import Any

from app.utils.academic_map_import_text import clean_copied_status_text, status_from_text_fragment

COURSE_CODE_RE = re.compile(r"\b([A-Z]{4})\s*([0-9]{4}[A-Z]?)\b")
TERM_RE = re.compile(r"\b(20[0-9]{2}-[0-9]{2})\s+(Spring|Summer|Fall|Winter)\b", re.IGNORECASE)
GRADE_RE = re.compile(r"^(A\+|A-|A|B\+|B-|B|C\+|C-|C|D|F|P|PA|PP|DI|W|AU|I)$", re.IGNORECASE)


def _normalize_course_code(prefix: str, number: str) -> str:
    return f"{prefix.upper()} {number.upper()}"


def _status_from_grade(grade: str | None) -> tuple[str, bool, str | None]:
    if grade:
        return "completed", False, None
    return "planned", True, "Missing grade; confirm whether this course is in progress or planned."


def _parse_units(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_tab_row(parts: list[str]) -> dict[str, Any] | None:
    joined = " ".join(parts)
    code_match = COURSE_CODE_RE.search(joined)
    term_match = TERM_RE.search(joined)
    if not code_match or not term_match:
        return None

    course_code = _normalize_course_code(code_match.group(1), code_match.group(2))
    term_label = f"{term_match.group(1)} {term_match.group(2).title()}"
    grade = None
    units = None
    status_from_text = None
    for part in parts:
        clean = part.strip()
        if GRADE_RE.match(clean):
            grade = clean.upper()
        elif re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", clean):
            units = _parse_units(clean)
        else:
            status_from_text = status_from_text or status_from_text_fragment(clean)

    status, needs_review, review_reason = _status_from_grade(grade)
    if status_from_text is not None:
        status = status_from_text
        if status != "planned":
            needs_review = False
            review_reason = None
    return {
        "course_code": course_code,
        "course_title": _extract_title(joined, course_code, term_label),
        "term_label": term_label,
        "term_code": None,
        "grade": grade,
        "units": units,
        "status": status,
        "needs_review": needs_review,
        "review_reason": review_reason,
        "raw": joined,
    }


def _extract_title(line: str, course_code: str, term_label: str) -> str | None:
    line = line.replace(course_code, " ", 1)
    compact_code = course_code.replace(" ", "")
    line = line.replace(compact_code, " ", 1)
    line = line.replace(term_label, " ", 1)
    line = re.sub(r"\b(A\+|A-|A|B\+|B-|B|C\+|C-|C|D|F|P|PA|PP|DI|W|AU|I)\b", " ", line)
    line = re.sub(r"\b[0-9]+(?:\.[0-9]+)?\b", " ", line)
    return clean_copied_status_text(line)


def _parse_loose_line(line: str) -> dict[str, Any] | None:
    code_match = COURSE_CODE_RE.search(line)
    term_match = TERM_RE.search(line)
    if not code_match or not term_match:
        return None

    course_code = _normalize_course_code(code_match.group(1), code_match.group(2))
    term_label = f"{term_match.group(1)} {term_match.group(2).title()}"
    after_term = line[term_match.end():].split()
    grade = None
    units = None
    status_from_text = None
    for token in after_term:
        if GRADE_RE.match(token):
            grade = token.upper()
        elif re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", token):
            units = _parse_units(token)
    status_from_text = status_from_text_fragment(" ".join(after_term))

    status, needs_review, review_reason = _status_from_grade(grade)
    if status_from_text is not None:
        status = status_from_text
        if status != "planned":
            needs_review = False
            review_reason = None
    return {
        "course_code": course_code,
        "course_title": _extract_title(line, course_code, term_label),
        "term_label": term_label,
        "term_code": None,
        "grade": grade,
        "units": units,
        "status": status,
        "needs_review": needs_review,
        "review_reason": review_reason,
        "raw": line,
    }


def parse_course_history_text(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("course"):
            continue

        parsed = None
        if "\t" in line:
            parsed = _parse_tab_row([part.strip() for part in line.split("\t") if part.strip()])
        if parsed is None:
            parsed = _parse_loose_line(line)
        if parsed is not None:
            rows.append(parsed)
    return rows
