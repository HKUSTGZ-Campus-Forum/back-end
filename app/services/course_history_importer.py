from __future__ import annotations

import re
from typing import Any

from app.utils.academic_map_import_text import clean_copied_status_text, status_from_text_fragment

COURSE_CODE_RE = re.compile(r"\b([A-Z]{4})\s*([0-9]{4}[A-Z]?)\b")
TERM_RE = re.compile(r"\b(20[0-9]{2})\s*-\s*([0-9]{2}|20[0-9]{2})\s+(Spring|Summer|Fall|Winter)\b", re.IGNORECASE)
GRADE_TOKEN_PATTERN = r"A\+|A-|A|B\+|B-|B|C\+|C-|C|D|F|P|PA|PP|DI|W|AU|I"
GRADE_RE = re.compile(rf"^({GRADE_TOKEN_PATTERN})$", re.IGNORECASE)
TERM_SUFFIX_BY_SEASON = {
    "fall": "10",
    "winter": "20",
    "spring": "30",
    "summer": "40",
}


def _normalize_course_code(prefix: str, number: str) -> str:
    return f"{prefix.upper()} {number.upper()}"


def _status_from_grade(grade: str | None) -> tuple[str, bool, str | None]:
    if grade and grade.strip().upper() == "W":
        return "withdrawn", False, None
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


def _term_from_match(term_match: re.Match) -> tuple[str, str] | None:
    start_year = int(term_match.group(1))
    end_year = int(term_match.group(2)) if len(term_match.group(2)) == 4 else 2000 + int(term_match.group(2))
    if end_year != start_year + 1:
        return None
    season = term_match.group(3).title()
    suffix = TERM_SUFFIX_BY_SEASON.get(season.lower())
    if not suffix:
        return None
    term_code = f"{start_year % 100:02d}{suffix}"
    term_label = f"{start_year}-{end_year % 100:02d} {season}"
    return term_label, term_code


def _parse_tab_row(parts: list[str]) -> dict[str, Any] | None:
    joined = " ".join(parts)
    code_match = COURSE_CODE_RE.search(joined)
    term_match = TERM_RE.search(joined)
    if not term_match:
        return None
    term = _term_from_match(term_match)
    if term is None:
        return None

    course_code = _normalize_course_code(code_match.group(1), code_match.group(2)) if code_match else ""
    term_label, term_code = term
    grade = None
    units = None
    status_from_text = None
    for part in parts:
        clean = part.strip()
        if GRADE_RE.fullmatch(clean):
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
        "course_title": _extract_title(joined, course_code, term_match.group(0)),
        "term_label": term_label,
        "term_code": term_code,
        "grade": grade,
        "units": units,
        "status": status,
        "needs_review": needs_review,
        "review_reason": review_reason,
        "raw": joined,
    }


def _extract_title(line: str, course_code: str | None, term_label: str) -> str | None:
    if course_code:
        line = line.replace(course_code, " ", 1)
        compact_code = course_code.replace(" ", "")
        line = line.replace(compact_code, " ", 1)
    line = line.replace(term_label, " ", 1)
    line = re.sub(rf"(?<!\S)({GRADE_TOKEN_PATTERN})(?!\S)", " ", line, flags=re.IGNORECASE)
    line = re.sub(r"\b[0-9]+(?:\.[0-9]+)?\b", " ", line)
    return clean_copied_status_text(line)


def _parse_loose_line(line: str) -> dict[str, Any] | None:
    code_match = COURSE_CODE_RE.search(line)
    term_match = TERM_RE.search(line)
    if not term_match:
        return None
    term = _term_from_match(term_match)
    if term is None:
        return None

    course_code = _normalize_course_code(code_match.group(1), code_match.group(2)) if code_match else ""
    term_label, term_code = term
    after_term = line[term_match.end():].split()
    grade = None
    units = None
    status_from_text = None
    for token in after_term:
        if GRADE_RE.fullmatch(token):
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
        "course_title": _extract_title(line, course_code, term_match.group(0)),
        "term_label": term_label,
        "term_code": term_code,
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
