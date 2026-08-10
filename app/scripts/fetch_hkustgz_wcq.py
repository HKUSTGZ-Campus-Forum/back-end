"""Prepare a reviewable HKUST-GZ WCQ scheduler snapshot.

This command only reads the official Class Schedule & Quota (WCQ) pages and
writes JSON under ``app/data/pending``.  It never opens an application context,
connects to a database, imports data, or deploys anything.

Live usage uses the reviewed 2610 control totals by default::

    python -m app.scripts.fetch_hkustgz_wcq --output /tmp/hkustgz-wcq-2610-candidate.json

Offline usage with previously downloaded pages is also supported.  The
directory must contain one file per advertised subject, named ``AIAA.html``,
``AMAT.html``, etc.  The term index may itself serve as the subject file shown
on that index::

    python -m app.scripts.fetch_hkustgz_wcq \
        --index-file tmp/wcq-2610-index.html \
        --subject-dir tmp/wcq-2610-subjects \
        --output app/data/pending/scheduler_offerings/26-27fall.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import urlencode, urljoin, urlparse

import requests


DEFAULT_TERM = "2610"
DEFAULT_START_DATE = "2026-09-01"
DEFAULT_EXPECTED_SUBJECTS = 29
DEFAULT_EXPECTED_COURSES = 383
DEFAULT_EXPECTED_OFFERED_COURSES = 383
DEFAULT_EXPECTED_SECTIONS = 801
DEFAULT_EXPECTED_LECTURES = 820
DEFAULT_EXPECTED_UNSCHEDULED_SECTIONS = 2
DEFAULT_BASE_URL = "https://w5.hkust-gz.edu.cn/wcq/cgi-bin/"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "pending"
    / "scheduler_offerings"
    / "26-27fall.json"
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0 Safari/537.36 "
    "campusForum-scheduler-data-preparation/1.0"
)

COURSE_HEADING_RE = re.compile(
    r"^(?P<subject>[A-Z][A-Z0-9]*)\s+"
    r"(?P<number>[A-Z0-9]+)\s+-\s+"
    r"(?P<title>.+?)\s+\((?P<credit>\d+)\s+units?\)$"
)
SECTION_RE = re.compile(r"^(?P<type>[A-Z]+)(?P<number>\d+)[A-Z]*$")
SECTION_CELL_RE = re.compile(
    r"^(?P<name>[A-Z]+\d+[A-Z]*)\s*\((?P<section_id>[^()]+)\)$"
)
UNSCHEDULED_SECTION_CELL_RE = re.compile(
    r"^(?P<name>TBA)\s*\((?P<section_id>[^()]+)\)$"
)
MEETING_RE = re.compile(
    r"(?P<days>(?:(?:Mo|Tu|We|Th|Fr|Sa|Su))+)[ \t]+"
    r"(?P<start>\d{1,2}:\d{2}(?:AM|PM))[ \t]*-[ \t]*"
    r"(?P<end>\d{1,2}:\d{2}(?:AM|PM))",
    re.IGNORECASE,
)
DATE_RANGE_RE = re.compile(
    r"\b\d{2}-[A-Z]{3}-\d{4}\s*-\s*\d{2}-[A-Z]{3}-\d{4}\b",
    re.IGNORECASE,
)
CANCELLED_RE = re.compile(r"\bcancel(?:led|ed)\b", re.IGNORECASE)
DAY_RE = re.compile(r"Mo|Tu|We|Th|Fr|Sa|Su", re.IGNORECASE)
DAY_NUMBER = {
    "mo": 1,
    "tu": 2,
    "we": 3,
    "th": 4,
    "fr": 5,
    "sa": 6,
    "su": 7,
}
SECTION_TYPE_DISPLAY = {
    "L": "Lecture",
    "T": "Tutorial",
    "LA": "Lab",
    "R": "Research",
}
REVIEWED_UNSCHEDULED_SECTIONS: dict[
    tuple[str, str, str], dict[str, object]
] = {
    ("2610", "UFUG1301", "6951"): {
        "matching": "[Matching between Lecture & Lab required]",
        "remarks": (
            "> If the lab sessions listed in the course schedule are not "
            "suitable for your availability, or if you were unable to enroll "
            "in a session, you can contact the instructor to request access "
            'to the "TBA" section. The lab class schedule will be coordinated '
            "later based on the availability of students who select "
            '"TBA." Please pay attention to further notifications.',
            "Instructor Consent Required",
        ),
        "review_basis": (
            "The source note explicitly identifies this as an opt-in lab "
            "fallback whose meeting will be coordinated later."
        ),
    },
    ("2610", "UFUG1302", "6952"): {
        "matching": "[Matching between Lecture & Tutorial required]",
        "remarks": ("Instructor Consent Required",),
        "review_basis": (
            "The source supplies no component type or meeting and only marks "
            "the row as requiring instructor consent."
        ),
    },
}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class WCQPreparationError(RuntimeError):
    """Raised when source retrieval, parsing, or completeness validation fails."""


@dataclass
class HtmlNode:
    tag: str
    attrs: dict[str, str]
    children: list["HtmlNode | str"] = field(default_factory=list)


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("document", {})
        self._stack = [self.root]

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        normalized_tag = tag.lower()
        node = HtmlNode(
            normalized_tag,
            {name.lower(): value or "" for name, value in attrs},
        )
        self._stack[-1].children.append(node)
        if normalized_tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        node = HtmlNode(
            tag.lower(),
            {name.lower(): value or "" for name, value in attrs},
        )
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == normalized_tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


@dataclass(frozen=True)
class SubjectReference:
    code: str
    classification: str
    url: str


@dataclass(frozen=True)
class ParsedPage:
    subject: str
    courses: list[dict[str, object]]
    unscheduled_sections: list[dict[str, object]]


@dataclass(frozen=True)
class ParsedSections:
    schedulable: list[dict[str, object]]
    unscheduled: list[dict[str, object]]


def _parse_tree(html: str) -> HtmlNode:
    parser = _TreeParser()
    parser.feed(html)
    parser.close()
    return parser.root


def _classes(node: HtmlNode) -> set[str]:
    return set(node.attrs.get("class", "").split())


def _walk(node: HtmlNode) -> Iterable[HtmlNode]:
    for child in node.children:
        if isinstance(child, HtmlNode):
            yield child
            yield from _walk(child)


def _find_all(
    node: HtmlNode,
    tag: str | None = None,
    *,
    class_name: str | None = None,
    element_id: str | None = None,
) -> list[HtmlNode]:
    matches = []
    for candidate in _walk(node):
        if tag is not None and candidate.tag != tag:
            continue
        if class_name is not None and class_name not in _classes(candidate):
            continue
        if element_id is not None and candidate.attrs.get("id") != element_id:
            continue
        matches.append(candidate)
    return matches


def _direct_children(node: HtmlNode, tag: str) -> list[HtmlNode]:
    return [
        child
        for child in node.children
        if isinstance(child, HtmlNode) and child.tag == tag
    ]


def _text(node: HtmlNode, *, preserve_breaks: bool = False) -> str:
    pieces: list[str] = []

    def visit(candidate: HtmlNode) -> None:
        for child in candidate.children:
            if isinstance(child, str):
                pieces.append(child)
            elif child.tag == "br" and preserve_breaks:
                pieces.append("\n")
            else:
                visit(child)

    visit(node)
    raw = "".join(pieces).replace("\xa0", " ")
    if not preserve_breaks:
        return " ".join(raw.split())
    lines = [" ".join(line.split()) for line in raw.splitlines()]
    return "\n".join(line for line in lines if line)


def _first_descendant(node: HtmlNode, tag: str) -> HtmlNode | None:
    return next(iter(_find_all(node, tag)), None)


def _normalize_info_label(value: str) -> str:
    return " ".join(value.upper().replace("\xa0", " ").split())


def parse_index(
    html: str,
    *,
    term: str = DEFAULT_TERM,
    term_url: str | None = None,
) -> tuple[str, list[SubjectReference]]:
    """Return the displayed term name and every advertised subject link."""

    root = _parse_tree(html)
    departments = _find_all(root, "div", class_name="depts")
    if len(departments) != 1:
        raise WCQPreparationError(
            f"expected one WCQ subject navigator, found {len(departments)}"
        )

    expected_path = f"/wcq/cgi-bin/{term}/"
    displayed_term = ""
    for anchor in _find_all(root, "a"):
        if anchor.attrs.get("href") == expected_path:
            displayed_term = _text(anchor)
            if displayed_term:
                break
    if not displayed_term:
        raise WCQPreparationError(f"WCQ index does not identify term {term}")

    base = term_url or urljoin(DEFAULT_BASE_URL, f"{term}/")
    references: list[SubjectReference] = []
    seen_codes: set[str] = set()
    for anchor in _find_all(departments[0], "a"):
        code = _text(anchor).upper()
        href = anchor.attrs.get("href", "")
        if not re.fullmatch(r"[A-Z][A-Z0-9]*", code):
            raise WCQPreparationError(f"invalid advertised subject code {code!r}")
        if code in seen_codes:
            raise WCQPreparationError(f"duplicate advertised subject {code}")
        classification = "ug" if "ug" in _classes(anchor) else "pg"
        advertised_url = urljoin(base, href)
        parsed_url = urlparse(advertised_url)
        if parsed_url.scheme != "https" or not (
            parsed_url.hostname == "hkust-gz.edu.cn"
            or (parsed_url.hostname or "").endswith(".hkust-gz.edu.cn")
        ):
            raise WCQPreparationError(
                f"advertised subject {code} has a non-official URL: {advertised_url}"
            )
        expected_suffix = f"/{term}/subject/{code}"
        if not parsed_url.path.endswith(expected_suffix):
            raise WCQPreparationError(
                f"advertised subject {code} has unexpected URL path: {advertised_url}"
            )
        # The navigator currently advertises clean paths, but the production
        # front controller is the reliably fetchable endpoint.  Keep the
        # actual response URL in provenance so the snapshot can be reproduced.
        url = urljoin(base, "../index.php") + "?" + urlencode(
            {"term": term, "subject": code}
        )
        references.append(SubjectReference(code, classification, url))
        seen_codes.add(code)

    if not references:
        raise WCQPreparationError("WCQ index advertises no subjects")
    return displayed_term, sorted(references, key=lambda item: item.code)


def page_subject(html: str) -> str:
    root = _parse_tree(html)
    titles = _find_all(root, "title")
    if not titles:
        raise WCQPreparationError("WCQ page has no title")
    title = _text(titles[0])
    match = re.match(r"^([A-Z][A-Z0-9]*)\s+-\s+HKUST Class Schedule", title)
    if not match:
        raise WCQPreparationError(f"unexpected WCQ page title: {title!r}")
    return match.group(1)


def _parse_clock(value: str) -> str:
    try:
        parsed = datetime.strptime(value.upper(), "%I:%M%p")
    except ValueError as exc:
        raise WCQPreparationError(f"invalid WCQ clock value {value!r}") from exc
    return parsed.strftime("%H%M")


def _meeting_rows(
    date_time: str,
    room: str,
    instructor: str,
) -> list[dict[str, object]]:
    if date_time.strip().upper() in {"", "TBA"}:
        return []

    meetings: list[dict[str, object]] = []
    for match in MEETING_RE.finditer(date_time):
        days = DAY_RE.findall(match.group("days"))
        if not days:
            continue
        start = _parse_clock(match.group("start"))
        end = _parse_clock(match.group("end"))
        if int(start) >= int(end):
            raise WCQPreparationError(
                f"meeting start must precede end: {date_time!r}"
            )
        for day in days:
            meetings.append(
                {
                    "day": DAY_NUMBER[day.lower()],
                    "start_time": start,
                    "end_time": end,
                    "room": room,
                    "instructor": instructor,
                    "source_date_times": [date_time],
                }
            )

    if not meetings:
        raise WCQPreparationError(f"unrecognized WCQ date/time value {date_time!r}")
    return meetings


def _cell_lines(cell: HtmlNode) -> list[str]:
    value = _text(cell, preserve_breaks=True)
    return [line for line in value.splitlines() if line]


def _room(cell: HtmlNode) -> str:
    return " & ".join(_cell_lines(cell))


def _instructor(cell: HtmlNode) -> str:
    linked_names = [_text(anchor) for anchor in _find_all(cell, "a")]
    linked_names = [name for name in linked_names if name]
    if linked_names:
        return " & ".join(linked_names)
    return " & ".join(_cell_lines(cell))


def _integer_cell(
    cell: HtmlNode,
    context: str,
    *,
    minimum: int | None = None,
) -> int:
    spans = _find_all(cell, "span")
    candidates = [_text(span) for span in spans] + [_text(cell)]
    for candidate in candidates:
        match = re.match(r"^\s*([+-]?\d+)", candidate)
        if match:
            value = int(match.group(1))
            if minimum is not None and value < minimum:
                raise WCQPreparationError(
                    f"{context}: expected an integer >= {minimum}, got {value}"
                )
            return value
    raise WCQPreparationError(f"{context}: expected an integer")


def _popup_details(cell: HtmlNode) -> list[str]:
    return [
        detail
        for detail in (
            _text(node)
            for node in _find_all(cell, "div", class_name="popupdetail")
        )
        if detail
    ]


def _reviewed_unscheduled_section(
    row: HtmlNode,
    cells: list[HtmlNode],
    *,
    course_code: str,
    semester_id: str,
    matching: str,
    section_label: str,
    section_id: str,
) -> dict[str, object]:
    identity = (semester_id, course_code, section_id)
    policy = REVIEWED_UNSCHEDULED_SECTIONS.get(identity)
    if policy is None:
        raise WCQPreparationError(
            f"{course_code}: unreviewed unscheduled section {section_label!r}; "
            "review its semantics before excluding it from the scheduler"
        )
    if len(cells) != 9:
        raise WCQPreparationError(
            f"{course_code}/{section_label}: reviewed unscheduled section changed "
            f"from 9 source cells to {len(cells)}"
        )

    date_time = _text(cells[1], preserve_breaks=True)
    room = _room(cells[2])
    instructor = _instructor(cells[3])
    quota = _integer_cell(
        cells[4], f"{course_code}/{section_label} quota", minimum=0
    )
    enrol = _integer_cell(
        cells[5], f"{course_code}/{section_label} enrol", minimum=0
    )
    avail = _integer_cell(cells[6], f"{course_code}/{section_label} avail")
    wait = _integer_cell(
        cells[7], f"{course_code}/{section_label} wait", minimum=-1
    )
    remarks = _popup_details(cells[8])
    reviewed_values: dict[str, object] = {
        "matching": matching,
        "date_time": date_time,
        "room": room,
        "instructor": instructor,
        "quota": quota,
        "remarks": tuple(remarks),
    }
    expected_values: dict[str, object] = {
        "matching": policy["matching"],
        "date_time": "TBA",
        "room": "TBA",
        "instructor": "TBA",
        "quota": 5,
        "remarks": policy["remarks"],
    }
    changed = [
        name
        for name, expected in expected_values.items()
        if reviewed_values[name] != expected
    ]
    if changed:
        raise WCQPreparationError(
            f"{course_code}/{section_label}: reviewed unscheduled section changed "
            f"({', '.join(changed)}); review it before scheduler omission"
        )

    return {
        "course_code": course_code,
        "semester_id": semester_id,
        "source_name": "TBA",
        "source_label": section_label,
        "section_id": section_id,
        "date_time": date_time,
        "room": room,
        "instructor": instructor,
        "quota": quota,
        "enrol": enrol,
        "avail": avail,
        "wait": wait,
        "remarks": remarks,
        "review_basis": policy["review_basis"],
        "source_row_classes": sorted(_classes(row)),
        "source_cell_values": [
            _text(cell, preserve_breaks=True) for cell in cells
        ],
    }


def _joined_distinct(existing: str, incoming: str) -> str:
    values: list[str] = []
    for raw_value in (existing, incoming):
        for value in raw_value.split(" & "):
            normalized = value.strip()
            if normalized and normalized not in values:
                values.append(normalized)
    return " & ".join(values)


def _merge_repeated_meetings(
    lectures: list[dict[str, object]],
) -> list[dict[str, object]]:
    merged: dict[tuple[int, str, str], dict[str, object]] = {}
    order: list[tuple[int, str, str]] = []
    for lecture in lectures:
        key = (
            int(lecture["day"]),
            str(lecture["start_time"]),
            str(lecture["end_time"]),
        )
        if key not in merged:
            merged[key] = dict(lecture)
            order.append(key)
            continue
        current = merged[key]
        current["room"] = _joined_distinct(
            str(current["room"]), str(lecture["room"])
        )
        current["instructor"] = _joined_distinct(
            str(current["instructor"]), str(lecture["instructor"])
        )
        current_source_values = current.get("source_date_times", [])
        incoming_source_values = lecture.get("source_date_times", [])
        if not isinstance(current_source_values, list) or not isinstance(
            incoming_source_values, list
        ):
            raise WCQPreparationError("meeting source_date_times must be lists")
        for value in incoming_source_values:
            if value not in current_source_values:
                current_source_values.append(value)
        current["source_date_times"] = current_source_values
    return [merged[key] for key in order]


def _parse_course_info(course_node: HtmlNode) -> dict[str, str]:
    containers = _find_all(course_node, "div", class_name="courseinfo")
    if len(containers) > 1:
        raise WCQPreparationError("course contains multiple course-info blocks")
    if not containers:
        return {}

    info: dict[str, str] = {}
    for row in _find_all(containers[0], "tr"):
        headings = _direct_children(row, "th")
        cells = _direct_children(row, "td")
        if not headings or not cells:
            continue
        label = _normalize_info_label(_text(headings[0]))
        value = _text(cells[0])
        if label in info:
            raise WCQPreparationError(f"duplicate course-info label {label!r}")
        info[label] = value
    return info


def _parse_sections(
    course_node: HtmlNode,
    *,
    course_code: str,
    semester_id: str,
    matching: str,
) -> ParsedSections:
    tables = _find_all(course_node, "table", class_name="sections")
    if len(tables) != 1:
        raise WCQPreparationError(
            f"{course_code}: expected one sections table, found {len(tables)}"
        )

    sections: list[dict[str, object]] = []
    unscheduled_sections: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    skipping_cancelled_section = False
    skipped_unscheduled_section: str | None = None
    for row in _find_all(tables[0], "tr"):
        cells = _direct_children(row, "td")
        if not cells:
            continue

        if "newsect" in _classes(row):
            if len(cells) < 8:
                raise WCQPreparationError(
                    f"{course_code}: new section row has only {len(cells)} cells"
                )
            section_label = _text(cells[0])
            row_status = " ".join(_classes(row)) + " " + _text(cells[-1])
            if CANCELLED_RE.search(row_status):
                current = None
                skipping_cancelled_section = True
                skipped_unscheduled_section = None
                continue
            skipping_cancelled_section = False
            skipped_unscheduled_section = None
            unscheduled_match = UNSCHEDULED_SECTION_CELL_RE.fullmatch(section_label)
            if unscheduled_match:
                unscheduled_sections.append(
                    _reviewed_unscheduled_section(
                        row,
                        cells,
                        course_code=course_code,
                        semester_id=semester_id,
                        matching=matching,
                        section_label=section_label,
                        section_id=unscheduled_match.group("section_id").strip(),
                    )
                )
                current = None
                skipped_unscheduled_section = section_label
                continue
            section_match = SECTION_CELL_RE.fullmatch(section_label)
            if not section_match:
                raise WCQPreparationError(
                    f"{course_code}: invalid section label {section_label!r}"
                )
            name = section_match.group("name")
            name_match = SECTION_RE.fullmatch(name)
            if not name_match:
                raise WCQPreparationError(
                    f"{course_code}: cannot derive type/bundle from section {name!r}"
                )
            current = {
                "course_code": course_code,
                "section_type": name_match.group("type"),
                "name": name,
                "bundle": int(name_match.group("number")),
                "semester_id": semester_id,
                "section_id": section_match.group("section_id").strip(),
                "quota": _integer_cell(
                    cells[4], f"{course_code}/{name} quota", minimum=0
                ),
                "enrol": _integer_cell(
                    cells[5], f"{course_code}/{name} enrol", minimum=0
                ),
                # WCQ uses negative availability for over-enrolled sections
                # and -1 wait values when a waitlist is unavailable.
                "avail": _integer_cell(cells[6], f"{course_code}/{name} avail"),
                "wait": _integer_cell(
                    cells[7], f"{course_code}/{name} wait", minimum=-1
                ),
                "lectures": [],
            }
            sections.append(current)
            meeting_cells = cells[1:4]
        else:
            if skipping_cancelled_section:
                continue
            if skipped_unscheduled_section is not None:
                raise WCQPreparationError(
                    f"{course_code}/{skipped_unscheduled_section}: reviewed "
                    "unscheduled section unexpectedly has a continuation row"
                )
            if current is None:
                raise WCQPreparationError(
                    f"{course_code}: continuation row precedes its section"
                )
            if len(cells) < 3:
                raise WCQPreparationError(
                    f"{course_code}/{current['name']}: continuation row has "
                    f"only {len(cells)} cells"
                )
            meeting_cells = cells[:3]

        current_lectures = current["lectures"]
        assert isinstance(current_lectures, list)
        current_lectures.extend(
            _meeting_rows(
                _text(meeting_cells[0], preserve_breaks=True),
                _room(meeting_cells[1]),
                _instructor(meeting_cells[2]),
            )
        )

    if not sections:
        # A course with only explicitly cancelled or reviewed-unscheduled rows
        # remains useful catalog data but is intentionally not offered here.
        return ParsedSections([], unscheduled_sections)

    type_order: list[str] = []
    for section in sections:
        section_type = str(section["section_type"])
        if section_type not in type_order:
            type_order.append(section_type)
    # Preserve the legacy scheduler grouping rule.  Current WCQ also exposes
    # research-only (R) courses that the old crawler could not parse, so use
    # the first type as the safe main-type fallback when neither L nor T exists.
    main_type = "L" if "L" in type_order else ("T" if "T" in type_order else type_order[0])
    ordered_types = [main_type] + [kind for kind in type_order if kind != main_type]
    main_display = SECTION_TYPE_DISPLAY.get(main_type, main_type)
    if matching and main_display not in matching:
        raise WCQPreparationError(
            f"{course_code}: matching notice omits main type {main_display!r}"
        )
    layer_by_type = {main_type: 0}
    next_layer = 1
    approximated_types: set[str] = set()
    sections_by_type = {
        section_type: [
            section
            for section in sections
            if str(section["section_type"]) == section_type
        ]
        for section_type in ordered_types
    }
    for section_type in ordered_types[1:]:
        display_name = SECTION_TYPE_DISPLAY.get(section_type, section_type)
        source_bundles = [
            int(section["bundle"]) for section in sections_by_type[section_type]
        ]
        has_duplicate_source_bundles = len(source_bundles) != len(set(source_bundles))
        if matching and display_name in matching and has_duplicate_source_bundles:
            # Names such as LA2A/LA2B are alternatives with the same numeric
            # lecture pairing.  The current schema cannot express both pairing
            # and alternatives.  Give the type an independent layer and unique
            # bundles so the solver never co-selects both alternatives.
            layer_by_type[section_type] = next_layer
            next_layer += 1
            approximated_types.add(section_type)
        elif matching and display_name in matching:
            layer_by_type[section_type] = 0
        else:
            layer_by_type[section_type] = next_layer
            next_layer += 1
    for section_type in approximated_types:
        ordered_sections = sorted(
            sections_by_type[section_type],
            key=lambda section: (
                int(section["bundle"]),
                str(section["name"]),
                str(section["section_id"]),
            ),
        )
        for unique_bundle, section in enumerate(ordered_sections, start=1):
            section["source_bundle"] = section["bundle"]
            section["bundle"] = unique_bundle
    for section in sections:
        section_type = str(section["section_type"])
        section["is_main"] = section_type == main_type
        section["layer"] = layer_by_type[section_type]
        lectures = section["lectures"]
        assert isinstance(lectures, list)
        lectures = _merge_repeated_meetings(lectures)
        section["lectures"] = lectures
        lectures.sort(
            key=lambda lecture: (
                int(lecture["day"]),
                str(lecture["start_time"]),
                str(lecture["end_time"]),
                str(lecture["room"]),
                str(lecture["instructor"]),
            )
        )

    sections.sort(
        key=lambda item: (
            str(item["section_type"]),
            int(item["bundle"]),
            str(item["name"]),
        )
    )
    return ParsedSections(sections, unscheduled_sections)


def parse_subject_page(
    html: str,
    *,
    expected_subject: str,
    semester_id: str = DEFAULT_TERM,
) -> ParsedPage:
    """Parse one complete WCQ subject page into scheduler snapshot records."""

    actual_subject = page_subject(html)
    if actual_subject != expected_subject:
        raise WCQPreparationError(
            f"expected subject {expected_subject}, page contains {actual_subject}"
        )

    root = _parse_tree(html)
    classes = _find_all(root, "div", element_id="classes")
    if len(classes) != 1:
        raise WCQPreparationError(
            f"{expected_subject}: expected one classes container, found {len(classes)}"
        )
    course_nodes = [
        child
        for child in classes[0].children
        if isinstance(child, HtmlNode)
        and child.tag == "div"
        and "course" in _classes(child)
    ]
    if not course_nodes:
        raise WCQPreparationError(f"{expected_subject}: page contains no courses")

    courses: list[dict[str, object]] = []
    unscheduled_sections: list[dict[str, object]] = []
    for course_node in course_nodes:
        headings = _find_all(course_node, "h2")
        if len(headings) != 1:
            raise WCQPreparationError(
                f"{expected_subject}: course block has {len(headings)} headings"
            )
        heading = _text(headings[0])
        match = COURSE_HEADING_RE.fullmatch(heading)
        if not match:
            raise WCQPreparationError(f"invalid WCQ course heading {heading!r}")
        subject = match.group("subject")
        if subject != expected_subject:
            raise WCQPreparationError(
                f"{expected_subject}: contains cross-subject course heading {heading!r}"
            )

        catalog_number = match.group("number")
        course_code = f"{subject}{catalog_number}"
        info = _parse_course_info(course_node)
        matching_nodes = _find_all(course_node, "div", class_name="matching")
        matching = _text(matching_nodes[0]) if matching_nodes else ""
        if len(matching_nodes) > 1:
            raise WCQPreparationError(f"{course_code}: multiple matching notices")

        normalized_info: dict[str, str] = {}
        for label, value in sorted(info.items()):
            normalized_info[label] = value
        leading_number = re.match(r"\d+", catalog_number)
        numeric_catalog = int(leading_number.group(0)) if leading_number else 0
        parsed_sections = _parse_sections(
            course_node,
            course_code=course_code,
            semester_id=semester_id,
            matching=matching,
        )
        unscheduled_sections.extend(parsed_sections.unscheduled)
        course: dict[str, object] = {
            "course_code": course_code,
            "sections": parsed_sections.schedulable,
            "subject": subject,
            "catalog_number": catalog_number,
            "course_title": match.group("title"),
            "course_desc": info.get("DESCRIPTION", ""),
            "credit": int(match.group("credit")),
            "pre_requirement": info.get("PRE-REQUISITE"),
            "co_requirement": info.get("CO-REQUISITE"),
            "exclusion": info.get("EXCLUSION"),
            "pg_course": numeric_catalog >= 5000,
            "klms_course": False,
            "vector": info.get("VECTOR"),
            "matching": matching,
            "course_info": normalized_info,
        }
        courses.append(course)

    courses.sort(key=lambda item: str(item["course_code"]))
    unscheduled_sections.sort(
        key=lambda item: (
            str(item["course_code"]),
            str(item["section_id"]),
        )
    )
    return ParsedPage(
        subject=actual_subject,
        courses=courses,
        unscheduled_sections=unscheduled_sections,
    )


def _fetch_text(url: str, *, timeout: float) -> str:
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        response.raise_for_status()
        content_type_header = response.headers.get("Content-Type", "")
        content_type = content_type_header.split(";", 1)[0].strip().lower()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise WCQPreparationError(
                f"{url}: expected HTML, received {content_type or 'unknown'}"
            )
        charset_match = re.search(
            r'''(?:^|;)\s*charset\s*=\s*(?:"([^"]+)"|'([^']+)'|([^;\s]+))''',
            content_type_header,
            re.IGNORECASE,
        )
        charset = (
            next(value for value in charset_match.groups() if value)
            if charset_match
            else "utf-8"
        )
        return response.content.decode(charset, errors="strict")
    except (requests.RequestException, LookupError, UnicodeError) as exc:
        raise WCQPreparationError(f"failed to fetch {url}: {exc}") from exc


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise WCQPreparationError(f"failed to read {path}: {exc}") from exc


def _subject_file(directory: Path, code: str) -> Path:
    candidates = [directory / f"{code}.html", directory / f"{code.lower()}.html"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise WCQPreparationError(
        f"missing offline subject page {code}; expected {candidates[0]}"
    )


def _iso_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WCQPreparationError(
            "--retrieved-at must be an ISO-8601 timestamp with a timezone"
        ) from exc
    if parsed.tzinfo is None:
        raise WCQPreparationError("--retrieved-at must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def snapshot_counts(snapshot: dict[str, object]) -> dict[str, int]:
    courses = snapshot["courses"]
    assert isinstance(courses, list)
    provenance = snapshot["provenance"]
    assert isinstance(provenance, dict)
    unscheduled_sections = provenance["unscheduled_sections"]
    assert isinstance(unscheduled_sections, list)
    return {
        "subjects": len(provenance["subjects"]),  # type: ignore[arg-type]
        "courses": len(courses),
        "offered_courses": sum(
            bool(course["sections"]) for course in courses  # type: ignore[index]
        ),
        "sections": sum(len(course["sections"]) for course in courses),  # type: ignore[index]
        "lectures": sum(
            len(section["lectures"])
            for course in courses
            for section in course["sections"]  # type: ignore[index]
        ),
        "unscheduled_sections": len(unscheduled_sections),
    }


def validate_snapshot(
    snapshot: dict[str, object],
    *,
    expected_subjects: int | None = None,
    expected_courses: int | None = None,
    expected_offered_courses: int | None = None,
    expected_sections: int | None = None,
    expected_lectures: int | None = None,
    expected_unscheduled_sections: int | None = None,
) -> dict[str, int]:
    """Validate identities and optional independently reviewed control totals."""

    courses = snapshot.get("courses")
    if not isinstance(courses, list) or not courses:
        raise WCQPreparationError("snapshot must contain at least one course")

    seen_courses: set[str] = set()
    seen_sections: set[tuple[str, str]] = set()
    for course in courses:
        if not isinstance(course, dict):
            raise WCQPreparationError("snapshot course must be an object")
        code = str(course.get("course_code", ""))
        if code in seen_courses:
            raise WCQPreparationError(f"duplicate course code {code}")
        seen_courses.add(code)
        sections = course.get("sections")
        if not isinstance(sections, list):
            raise WCQPreparationError(f"{code}: sections must be a list")
        for section in sections:
            if not isinstance(section, dict):
                raise WCQPreparationError(f"{code}: section must be an object")
            key = (str(section.get("semester_id", "")), str(section.get("section_id", "")))
            if not all(key):
                raise WCQPreparationError(f"{code}: section identity is incomplete")
            if key in seen_sections:
                raise WCQPreparationError(
                    f"duplicate section identity {key[0]}/{key[1]}"
                )
            seen_sections.add(key)

    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict):
        raise WCQPreparationError("snapshot provenance must be an object")
    unscheduled_sections = provenance.get("unscheduled_sections")
    if not isinstance(unscheduled_sections, list):
        raise WCQPreparationError(
            "snapshot provenance.unscheduled_sections must be a list"
        )
    seen_unscheduled: set[tuple[str, str]] = set()
    for source_section in unscheduled_sections:
        if not isinstance(source_section, dict):
            raise WCQPreparationError("unscheduled source section must be an object")
        key = (
            str(source_section.get("semester_id", "")),
            str(source_section.get("section_id", "")),
        )
        if not all(key):
            raise WCQPreparationError(
                "unscheduled source section identity is incomplete"
            )
        if key in seen_sections:
            raise WCQPreparationError(
                f"unscheduled source section {key[0]}/{key[1]} is also schedulable"
            )
        if key in seen_unscheduled:
            raise WCQPreparationError(
                f"duplicate unscheduled source section {key[0]}/{key[1]}"
            )
        seen_unscheduled.add(key)

    counts = snapshot_counts(snapshot)
    expectations = {
        "subjects": expected_subjects,
        "courses": expected_courses,
        "offered_courses": expected_offered_courses,
        "sections": expected_sections,
        "lectures": expected_lectures,
        "unscheduled_sections": expected_unscheduled_sections,
    }
    mismatches = [
        f"{name}={counts[name]} (expected {expected})"
        for name, expected in expectations.items()
        if expected is not None and counts[name] != expected
    ]
    if mismatches:
        raise WCQPreparationError(
            "snapshot does not match reviewed control totals: " + "; ".join(mismatches)
        )
    return counts


def build_snapshot(
    *,
    index_html: str,
    term: str,
    term_url: str,
    retrieved_at: str,
    semester_start_date: str,
    subject_html: dict[str, str],
) -> dict[str, object]:
    term_name, references = parse_index(index_html, term=term, term_url=term_url)
    expected_codes = {reference.code for reference in references}
    actual_codes = set(subject_html)
    if actual_codes != expected_codes:
        missing = sorted(expected_codes - actual_codes)
        unexpected = sorted(actual_codes - expected_codes)
        raise WCQPreparationError(
            "subject page set does not match index"
            + (f"; missing={','.join(missing)}" if missing else "")
            + (f"; unexpected={','.join(unexpected)}" if unexpected else "")
        )

    courses: list[dict[str, object]] = []
    unscheduled_sections: list[dict[str, object]] = []
    for reference in references:
        page = parse_subject_page(
            subject_html[reference.code],
            expected_subject=reference.code,
            semester_id=term,
        )
        courses.extend(page.courses)
        unscheduled_sections.extend(page.unscheduled_sections)
    courses.sort(key=lambda item: str(item["course_code"]))
    unscheduled_sections.sort(
        key=lambda item: (
            str(item["course_code"]),
            str(item["section_id"]),
        )
    )

    approximations: list[dict[str, object]] = []
    for course in courses:
        for section_type in sorted(
            {
                str(section["section_type"])
                for section in course["sections"]  # type: ignore[index]
                if "source_bundle" in section
            }
        ):
            approximations.append(
                {
                    "kind": "matched_alphanumeric_section_bundles",
                    "course_code": str(course["course_code"]),
                    "section_type": section_type,
                    "source_sections": [
                        {
                            "name": str(section["name"]),
                            "section_id": str(section["section_id"]),
                            "source_bundle": int(section["source_bundle"]),
                            "prepared_bundle": int(section["bundle"]),
                        }
                        for section in course["sections"]  # type: ignore[index]
                        if str(section["section_type"]) == section_type
                    ],
                    "explanation": (
                        "The source uses duplicate numeric bundle suffixes for "
                        "A/B alternatives. The current (layer,bundle) schema "
                        "cannot preserve both lecture-to-section pairing and "
                        "alternative choice, so this type uses an independent "
                        "layer with unique bundles; cross-pair choices may be "
                        "generated. Original names and section IDs are retained."
                    ),
                }
            )

    for source_section in unscheduled_sections:
        approximations.append(
            {
                "kind": "unscheduled_source_section_omitted",
                "course_code": str(source_section["course_code"]),
                "semester_id": str(source_section["semester_id"]),
                "section_id": str(source_section["section_id"]),
                "source_label": str(source_section["source_label"]),
                "retained_at": "provenance.unscheduled_sections",
                "review_basis": str(source_section["review_basis"]),
                "explanation": (
                    "The official row has no component type or meeting time. "
                    "Mapping it to a scheduler section would invent a type, "
                    "layer, and bundle that could incorrectly make the row a "
                    "required choice. It is omitted from schedulable sections; "
                    "all source fields remain in provenance.unscheduled_sections."
                ),
            }
        )

    date_range_meetings = []
    for course in courses:
        for section in course["sections"]:  # type: ignore[index]
            for lecture in section["lectures"]:
                source_values = lecture.get("source_date_times", [])
                if any(DATE_RANGE_RE.search(str(value)) for value in source_values):
                    date_range_meetings.append(lecture)
    if date_range_meetings:
        approximations.append(
            {
                "kind": "date_ranges_collapsed_to_weekly_meetings",
                "affected_canonical_meetings": len(date_range_meetings),
                "retained_date_range_values": sum(
                    sum(
                        bool(DATE_RANGE_RE.search(str(value)))
                        for value in lecture["source_date_times"]
                    )
                    for lecture in date_range_meetings
                ),
                "explanation": (
                    "The current meeting schema stores weekday and time but no "
                    "effective date range. Date-specific WCQ rows are therefore "
                    "collapsed to canonical weekly slots. Every original date/time "
                    "cell is retained in source_date_times for audit and a future "
                    "date-aware schema migration."
                ),
            }
        )

    snapshot: dict[str, object] = {
        "semester_id": term,
        "semester_name": term_name,
        "semester_start_date": semester_start_date,
        "provenance": {
            "source_name": "HKUST-GZ Class Schedule & Quota",
            "term_url": term_url,
            "retrieved_at": retrieved_at,
            "term_index_sha256": hashlib.sha256(index_html.encode("utf-8")).hexdigest(),
            "approximations": approximations,
            "unscheduled_sections": unscheduled_sections,
            "subjects": [
                {
                    "code": reference.code,
                    "classification": reference.classification,
                    "url": reference.url,
                    "sha256": hashlib.sha256(
                        subject_html[reference.code].encode("utf-8")
                    ).hexdigest(),
                }
                for reference in references
            ],
        },
        "courses": courses,
    }
    validate_snapshot(snapshot)
    return snapshot


def _atomic_write_json(path: Path, snapshot: dict[str, object], *, force: bool) -> None:
    if path.exists() and not force:
        raise WCQPreparationError(
            f"output already exists: {path}; pass --force only after reviewing the target"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch/parse official HKUST-GZ WCQ pages into a pending JSON file. "
            "This command never connects to the DB and never imports or deploys data."
        )
    )
    parser.add_argument("--term", default=DEFAULT_TERM, help="WCQ term id (default: 2610)")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"official WCQ CGI base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--index-file",
        type=Path,
        help="read a saved term index instead of fetching it",
    )
    parser.add_argument(
        "--subject-dir",
        type=Path,
        help="directory of saved SUBJECT.html pages (requires --index-file)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"pending JSON destination (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--semester-start-date",
        default=DEFAULT_START_DATE,
        help=f"ISO semester start date metadata (default: {DEFAULT_START_DATE})",
    )
    parser.add_argument(
        "--retrieved-at",
        help="fixed ISO-8601 retrieval timestamp for reproducible output",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
    parser.add_argument(
        "--expected-subjects", type=int, default=DEFAULT_EXPECTED_SUBJECTS
    )
    parser.add_argument(
        "--expected-courses", type=int, default=DEFAULT_EXPECTED_COURSES
    )
    parser.add_argument(
        "--expected-offered-courses",
        type=int,
        default=DEFAULT_EXPECTED_OFFERED_COURSES,
    )
    parser.add_argument(
        "--expected-sections", type=int, default=DEFAULT_EXPECTED_SECTIONS
    )
    parser.add_argument(
        "--expected-lectures", type=int, default=DEFAULT_EXPECTED_LECTURES
    )
    parser.add_argument(
        "--expected-unscheduled-sections",
        type=int,
        default=DEFAULT_EXPECTED_UNSCHEDULED_SECTIONS,
        help=(
            "reviewed source rows intentionally retained only as provenance "
            f"(default: {DEFAULT_EXPECTED_UNSCHEDULED_SECTIONS})"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing pending JSON file after explicit review",
    )
    return parser


def run(args: argparse.Namespace) -> tuple[Path, dict[str, int]]:
    if not re.fullmatch(r"\d{4}", args.term):
        raise WCQPreparationError("--term must contain exactly four digits")
    try:
        datetime.strptime(args.semester_start_date, "%Y-%m-%d")
    except ValueError as exc:
        raise WCQPreparationError(
            "--semester-start-date must use YYYY-MM-DD"
        ) from exc
    for name in (
        "expected_subjects",
        "expected_courses",
        "expected_offered_courses",
        "expected_sections",
        "expected_lectures",
        "expected_unscheduled_sections",
    ):
        value = getattr(args, name)
        if value is not None and value < 0:
            raise WCQPreparationError(f"--{name.replace('_', '-')} must be non-negative")
    if args.timeout <= 0:
        raise WCQPreparationError("--timeout must be positive")
    if args.subject_dir is not None and args.index_file is None:
        raise WCQPreparationError("--subject-dir requires --index-file")

    term_url = urljoin(args.base_url.rstrip("/") + "/", f"{args.term}/")
    if args.index_file is None:
        index_html = _fetch_text(term_url, timeout=args.timeout)
    else:
        index_html = _read_text(args.index_file)

    _, references = parse_index(index_html, term=args.term, term_url=term_url)
    index_subject = page_subject(index_html)
    subject_pages: dict[str, str] = {}
    if args.index_file is not None:
        directory = args.subject_dir or args.index_file.parent
        for reference in references:
            if reference.code == index_subject:
                subject_pages[reference.code] = index_html
            else:
                subject_pages[reference.code] = _read_text(
                    _subject_file(directory, reference.code)
                )
    else:
        for reference in references:
            subject_pages[reference.code] = (
                index_html
                if reference.code == index_subject
                else _fetch_text(reference.url, timeout=args.timeout)
            )

    snapshot = build_snapshot(
        index_html=index_html,
        term=args.term,
        term_url=term_url,
        retrieved_at=_iso_timestamp(args.retrieved_at),
        semester_start_date=args.semester_start_date,
        subject_html=subject_pages,
    )
    counts = validate_snapshot(
        snapshot,
        expected_subjects=args.expected_subjects,
        expected_courses=args.expected_courses,
        expected_offered_courses=args.expected_offered_courses,
        expected_sections=args.expected_sections,
        expected_lectures=args.expected_lectures,
        expected_unscheduled_sections=args.expected_unscheduled_sections,
    )
    _atomic_write_json(args.output, snapshot, force=args.force)
    return args.output, counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        output, counts = run(args)
    except WCQPreparationError as exc:
        parser.exit(2, f"error: {exc}\n")
    print(
        f"prepared {output}: subjects={counts['subjects']} "
        f"courses={counts['courses']} offered_courses={counts['offered_courses']} "
        f"sections={counts['sections']} "
        f"lectures={counts['lectures']} "
        f"unscheduled_sections={counts['unscheduled_sections']}"
    )
    print("No database import or deployment was performed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
