from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.course_domain import normalize_course_code


CLASS_TYPE_MAIN = "E"
CLASS_TYPE_ASSOCIATED = "N"
SUPPORTED_CLASS_TYPES = {CLASS_TYPE_MAIN, CLASS_TYPE_ASSOCIATED}
SEMESTER_RE = re.compile(r"^\d{4}$")


class SisnPayloadError(RuntimeError):
    pass


class SisnMappingError(RuntimeError):
    pass


@dataclass(frozen=True)
class SisnAdaptation:
    snapshot: dict[str, Any]
    source_payload_sha256: str
    fetched_at: datetime
    counts: dict[str, int]
    warnings: list[str]


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def snapshot_sha256(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(_stable_json(snapshot).encode("utf-8")).hexdigest()


def _string(value: Any, context: str, *, allow_empty: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not value and not allow_empty:
        raise SisnPayloadError(f"{context}: expected a non-empty string")
    return value


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise SisnPayloadError(f"{context}: expected integer")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise SisnPayloadError(f"{context}: expected integer") from exc
    if parsed < minimum:
        raise SisnPayloadError(f"{context}: expected value >= {minimum}")
    return parsed


def _credit(value: Any, context: str) -> int:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SisnPayloadError(f"{context}: expected numeric credit") from exc
    if parsed < 0 or not parsed.is_integer():
        raise SisnPayloadError(
            f"{context}: the current UniKorn course model requires whole-number credits"
        )
    return int(parsed)


def _iso_datetime(value: Any, context: str) -> datetime:
    raw = _string(value, context)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SisnPayloadError(f"{context}: expected ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SisnPayloadError(f"{context}: timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def validate_proxy_envelope(
    envelope: Any,
    *,
    expected_term: str,
) -> tuple[dict[str, Any], str, datetime]:
    if not SEMESTER_RE.fullmatch(expected_term):
        raise SisnPayloadError("expected term must contain four digits")
    if not isinstance(envelope, dict):
        raise SisnPayloadError("proxy envelope must be an object")
    if envelope.get("schema_version") != 1 or envelope.get("source") != "sisn":
        raise SisnPayloadError("unsupported SISN proxy envelope")
    if envelope.get("requested_term") != expected_term:
        raise SisnPayloadError("SISN proxy returned a different requested term")
    fetched_at = _iso_datetime(envelope.get("fetched_at"), "fetched_at")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise SisnPayloadError("SISN proxy payload must be an object")
    expected_sha256 = _string(envelope.get("payload_sha256"), "payload_sha256")
    actual_sha256 = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    if expected_sha256 != actual_sha256:
        raise SisnPayloadError("SISN proxy payload SHA-256 mismatch")
    if str(payload.get("status")) != "200" or not isinstance(payload.get("courses"), list):
        raise SisnPayloadError("SISN payload is not a successful class-quota response")
    if not payload["courses"]:
        raise SisnPayloadError("SISN payload contains no courses")
    return payload, actual_sha256, fetched_at


def _time_hhmm(value: Any, context: str) -> int:
    raw = _string(value, context)
    if not re.fullmatch(r"\d{2}:\d{2}", raw):
        raise SisnPayloadError(f"{context}: expected HH:MM")
    hour, minute = (int(part) for part in raw.split(":"))
    if hour > 23 or minute > 59:
        raise SisnPayloadError(f"{context}: invalid time")
    return hour * 100 + minute


def _normalize_reserve_cap(value: Any, context: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SisnPayloadError(f"{context}: reserveCap must be a list")
    rows = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise SisnPayloadError(f"{context}[{index}]: expected object")
        rows.append({
            "name": _string(
                item.get("rsrvName"),
                f"{context}[{index}].rsrvName",
                allow_empty=True,
            ),
            "capacity": _integer(
                item.get("rsrvEnrlCap"),
                f"{context}[{index}].rsrvEnrlCap",
            ),
            "enrolled": _integer(
                item.get("rsrvEnrlTot"),
                f"{context}[{index}].rsrvEnrlTot",
            ),
        })
    return rows


def _meetings(
    value: Any,
    context: str,
    *,
    stats: Counter[str] | None = None,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SisnPayloadError(f"{context}: schedules must be a list")
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for index, schedule in enumerate(value):
        item_context = f"{context}[{index}]"
        if not isinstance(schedule, dict):
            raise SisnPayloadError(f"{item_context}: expected object")
        weekdays = schedule.get("weekdays")
        if not isinstance(weekdays, list):
            raise SisnPayloadError(f"{item_context}.weekdays: expected list")
        raw_start_time = str(schedule.get("startTime") or "").strip()
        raw_end_time = str(schedule.get("endTime") or "").strip()
        if (
            not raw_start_time
            and not raw_end_time
            and all(weekday is None for weekday in weekdays)
        ):
            if stats is not None:
                stats["omitted_tba_schedules"] += 1
            continue
        start_time = _time_hhmm(raw_start_time, f"{item_context}.startTime")
        end_time = _time_hhmm(raw_end_time, f"{item_context}.endTime")
        if start_time >= end_time:
            raise SisnPayloadError(f"{item_context}: startTime must precede endTime")
        venue = _string(schedule.get("venue"), f"{item_context}.venue", allow_empty=True)
        facility_id = _string(
            schedule.get("facilityId"),
            f"{item_context}.facilityId",
            allow_empty=True,
        )
        instructors = schedule.get("instructors") or []
        if not isinstance(instructors, list):
            raise SisnPayloadError(f"{item_context}.instructors: expected list")
        instructor_text = "; ".join(
            _string(instructor, f"{item_context}.instructors", allow_empty=True)
            for instructor in instructors
            if _string(instructor, f"{item_context}.instructors", allow_empty=True)
        )
        date_range = {
            "start_date": _string(schedule.get("startDt"), f"{item_context}.startDt"),
            "end_date": _string(schedule.get("endDt"), f"{item_context}.endDt"),
            "facility_id": facility_id or None,
        }
        for weekday in weekdays:
            day = _integer(weekday, f"{item_context}.weekdays", minimum=1)
            if day > 7:
                raise SisnPayloadError(f"{item_context}.weekdays: expected 1-7")
            key = (day, start_time, end_time, venue, instructor_text, facility_id)
            meeting = grouped.setdefault(key, {
                "day": day,
                "start_time": start_time,
                "end_time": end_time,
                "room": venue,
                "instructor": instructor_text,
                "facility_id": facility_id or None,
                "date_ranges": [],
            })
            if date_range not in meeting["date_ranges"]:
                meeting["date_ranges"].append(date_range)
    return [
        grouped[key]
        for key in sorted(grouped, key=lambda item: tuple(str(value) for value in item))
    ]


def _reviewed_baseline_meetings(
    section: dict[str, Any],
    context: str,
) -> list[dict[str, Any]]:
    """Normalize reviewed WCQ meetings used when SISN omits a class schedule.

    The reviewed baseline is the authority for section grouping and is also a
    safe secondary source for meeting times. Returning normalized copies keeps
    the SISN candidate independent from the loaded baseline object and makes a
    malformed fallback fail closed before it can reach the database importer.
    """
    lectures = section.get("lectures") or []
    if not isinstance(lectures, list):
        raise SisnMappingError(f"{context}.lectures: expected list")

    normalized: list[dict[str, Any]] = []
    for index, lecture in enumerate(lectures):
        lecture_context = f"{context}.lectures[{index}]"
        if not isinstance(lecture, dict):
            raise SisnMappingError(f"{lecture_context}: expected object")
        day = _integer(lecture.get("day"), f"{lecture_context}.day", minimum=1)
        if day > 7:
            raise SisnMappingError(f"{lecture_context}.day: expected 1-7")
        start_time = _integer(
            lecture.get("start_time"),
            f"{lecture_context}.start_time",
        )
        end_time = _integer(
            lecture.get("end_time"),
            f"{lecture_context}.end_time",
        )
        if start_time >= end_time:
            raise SisnMappingError(
                f"{lecture_context}: start_time must precede end_time"
            )
        normalized.append({
            "day": day,
            "start_time": start_time,
            "end_time": end_time,
            "room": _string(
                lecture.get("room"),
                f"{lecture_context}.room",
                allow_empty=True,
            ),
            "instructor": _string(
                lecture.get("instructor"),
                f"{lecture_context}.instructor",
                allow_empty=True,
            ),
            "facility_id": _string(
                lecture.get("facility_id"),
                f"{lecture_context}.facility_id",
                allow_empty=True,
            ) or None,
            "date_ranges": list(lecture.get("date_ranges") or []),
        })
    return normalized


def _baseline_maps(
    baseline: dict[str, Any],
    expected_term: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    if baseline.get("semester_id") != expected_term:
        raise SisnMappingError("baseline semester does not match SISN term")
    courses = baseline.get("courses")
    if not isinstance(courses, list) or not courses:
        raise SisnMappingError("baseline must contain courses")
    by_course: dict[str, dict[str, Any]] = {}
    by_class: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for course in courses:
        if not isinstance(course, dict):
            raise SisnMappingError("baseline course must be an object")
        code = normalize_course_code(course.get("course_code"))
        if not code or code in by_course:
            raise SisnMappingError("baseline contains an invalid or duplicate course code")
        by_course[code] = course
        for section in course.get("sections") or []:
            class_number = _string(section.get("section_id"), "baseline section_id")
            if class_number in by_class:
                raise SisnMappingError(f"baseline contains duplicate class {class_number}")
            by_class[class_number] = (course, section)
    return by_course, by_class


def adapt_proxy_envelope(
    envelope: Any,
    *,
    term: str,
    baseline: dict[str, Any],
    baseline_label: str,
) -> SisnAdaptation:
    payload, source_sha256, fetched_at = validate_proxy_envelope(
        envelope,
        expected_term=term,
    )
    baseline_by_course, baseline_by_class = _baseline_maps(baseline, term)
    api_courses = payload["courses"]
    seen_courses: set[str] = set()
    seen_classes: set[str] = set()
    mapped_baseline_classes: set[str] = set()
    omitted_unscheduled: list[str] = []
    fallback_main_classes: list[str] = []
    baseline_meeting_fallbacks: list[dict[str, Any]] = []
    warnings: list[str] = []
    output_courses = []
    raw_class_count = 0
    raw_schedule_count = 0
    raw_reserve_class_count = 0
    transform_stats: Counter[str] = Counter()

    for course_index, api_course in enumerate(api_courses):
        context = f"courses[{course_index}]"
        if not isinstance(api_course, dict):
            raise SisnPayloadError(f"{context}: expected object")
        code = normalize_course_code(api_course.get("crseCode"))
        if not code or code in seen_courses:
            raise SisnPayloadError(f"{context}: invalid or duplicate crseCode")
        seen_courses.add(code)
        baseline_course = baseline_by_course.get(code)
        baseline_sections = (baseline_course or {}).get("sections") or []
        main_types = [
            str(section.get("section_type") or "").strip()
            for section in baseline_sections
            if section.get("is_main") and section.get("section_type")
        ]
        fallback_type = Counter(main_types).most_common(1)[0][0] if main_types else "E"
        used_main_bundles = {
            int(section.get("bundle"))
            for section in baseline_sections
            if section.get("is_main")
        }
        next_fallback_bundle = max(used_main_bundles or {0}) + 1
        output_sections = []

        classes = api_course.get("classes") or []
        if not isinstance(classes, list):
            raise SisnPayloadError(f"{context}.classes: expected list")
        for class_index, api_class in enumerate(classes):
            class_context = f"{context}.classes[{class_index}]"
            if not isinstance(api_class, dict):
                raise SisnPayloadError(f"{class_context}: expected object")
            raw_class_count += 1
            class_number = _string(api_class.get("classNbr"), f"{class_context}.classNbr")
            if class_number in seen_classes:
                raise SisnPayloadError(f"duplicate classNbr {class_number}")
            seen_classes.add(class_number)
            class_type = _string(api_class.get("classType"), f"{class_context}.classType")
            if class_type not in SUPPORTED_CLASS_TYPES:
                raise SisnMappingError(f"unsupported classType {class_type!r}")
            schedules = api_class.get("schedules") or []
            raw_schedule_count += len(schedules) if isinstance(schedules, list) else 0
            reserve_cap = _normalize_reserve_cap(
                api_class.get("reserveCap"),
                f"{class_context}.reserveCap",
            )
            if reserve_cap:
                raw_reserve_class_count += 1
            baseline_match = baseline_by_class.get(class_number)
            if baseline_match is not None:
                baseline_match_course, baseline_section = baseline_match
                if normalize_course_code(baseline_match_course.get("course_code")) != code:
                    raise SisnMappingError(
                        f"class {class_number} moved between courses in SISN"
                    )
                expected_main = class_type == CLASS_TYPE_MAIN
                if bool(baseline_section.get("is_main")) != expected_main:
                    raise SisnMappingError(
                        f"class {class_number} changed main/associated semantics"
                    )
                mapped_baseline_classes.add(class_number)
                section_type = _string(
                    baseline_section.get("section_type"),
                    f"baseline class {class_number}.section_type",
                )
                section_name = _string(
                    baseline_section.get("name"),
                    f"baseline class {class_number}.name",
                )
                bundle = _integer(
                    baseline_section.get("bundle"),
                    f"baseline class {class_number}.bundle",
                )
                layer = _integer(
                    baseline_section.get("layer"),
                    f"baseline class {class_number}.layer",
                )
                is_main = bool(baseline_section.get("is_main"))
            else:
                if not schedules:
                    omitted_unscheduled.append(class_number)
                    continue
                if class_type != CLASS_TYPE_MAIN:
                    raise SisnMappingError(
                        f"scheduled associated class {class_number} has no reviewed WCQ mapping"
                    )
                while next_fallback_bundle in used_main_bundles:
                    next_fallback_bundle += 1
                bundle = next_fallback_bundle
                used_main_bundles.add(bundle)
                next_fallback_bundle += 1
                layer = 0
                is_main = True
                section_type = fallback_type
                section_name = f"{fallback_type}{bundle:02d}"
                fallback_main_classes.append(class_number)

            quota = _integer(api_class.get("enrlCap"), f"{class_context}.enrlCap")
            enrol = _integer(api_class.get("enrlTot"), f"{class_context}.enrlTot")
            meetings = _meetings(
                schedules,
                f"{class_context}.schedules",
                stats=transform_stats,
            )
            if not meetings and baseline_match is not None:
                baseline_meetings = _reviewed_baseline_meetings(
                    baseline_section,
                    f"baseline class {class_number}",
                )
                if baseline_meetings:
                    meetings = baseline_meetings
                    baseline_meeting_fallbacks.append({
                        "course_code": code,
                        "section_id": class_number,
                        "section_name": section_name,
                        "meeting_count": len(baseline_meetings),
                    })

            output_sections.append({
                "course_code": code,
                "section_type": section_type,
                "name": section_name,
                "bundle": bundle,
                "semester_id": term,
                "section_id": class_number,
                "quota": quota,
                "enrol": enrol,
                "avail": max(quota - enrol, 0),
                "wait": _integer(api_class.get("waitTot"), f"{class_context}.waitTot"),
                "lectures": meetings,
                "is_main": is_main,
                "layer": layer,
                "status": "active",
                "source_class_type": class_type,
                "source_section_label": _string(
                    api_class.get("section"),
                    f"{class_context}.section",
                ),
                "associated_class": _integer(
                    api_class.get("associatedClass"),
                    f"{class_context}.associatedClass",
                ),
                "consent_required": bool(api_class.get("consent", False)),
                "remarks": _string(
                    api_class.get("remarks"),
                    f"{class_context}.remarks",
                    allow_empty=True,
                ) or None,
                "reserve_cap": reserve_cap,
            })

        baseline_title = (baseline_course or {}).get("course_title")
        output_courses.append({
            "course_code": code,
            "sections": output_sections,
            "subject": _string(api_course.get("subject"), f"{context}.subject"),
            "catalog_number": _string(
                api_course.get("catalogNbr"),
                f"{context}.catalogNbr",
            ),
            "course_title": _string(
                api_course.get("crseDesc") or baseline_title,
                f"{context}.course_title",
            ),
            "course_title_abbr": _string(
                api_course.get("crseDesc"),
                f"{context}.crseDesc",
                allow_empty=True,
            ) or None,
            "course_desc": _string(
                api_course.get("longDesc") or (baseline_course or {}).get("course_desc"),
                f"{context}.longDesc",
                allow_empty=True,
            ),
            "credit": _credit(api_course.get("credit"), f"{context}.credit"),
            "pre_requirement": _string(
                api_course.get("preReq"),
                f"{context}.preReq",
                allow_empty=True,
            ) or None,
            "co_requirement": _string(
                api_course.get("coReq"),
                f"{context}.coReq",
                allow_empty=True,
            ) or None,
            "exclusion": _string(
                api_course.get("exclusion"),
                f"{context}.exclusion",
                allow_empty=True,
            ) or None,
            "pg_course": bool(
                (baseline_course or {}).get("pg_course")
                or int(re.sub(r"\D", "", str(api_course.get("catalogNbr") or "0")) or 0) >= 5000
            ),
            "klms_course": bool((baseline_course or {}).get("klms_course", False)),
            "vector": (baseline_course or {}).get("vector"),
            "attributes": api_course.get("attributes") or [],
            "prev_course_code": _string(
                api_course.get("prevCrseCode"),
                f"{context}.prevCrseCode",
                allow_empty=True,
            ) or None,
        })

    missing_baseline_classes = sorted(set(baseline_by_class) - mapped_baseline_classes)
    if fallback_main_classes:
        warnings.append(
            f"generated conservative main-section labels for {len(fallback_main_classes)} new classes"
        )
    if omitted_unscheduled:
        warnings.append(
            f"omitted {len(omitted_unscheduled)} unmapped classes without schedules"
        )
    if missing_baseline_classes:
        warnings.append(
            f"SISN no longer returned {len(missing_baseline_classes)} baseline classes"
        )
    if transform_stats["omitted_tba_schedules"]:
        warnings.append(
            "omitted "
            f"{transform_stats['omitted_tba_schedules']} TBA schedule rows without a day or time"
        )
    if baseline_meeting_fallbacks:
        fallback_count = len(baseline_meeting_fallbacks)
        class_label = "class" if fallback_count == 1 else "classes"
        warnings.append(
            "preserved reviewed WCQ meetings for "
            f"{fallback_count} {class_label} whose SISN schedules were empty"
        )

    snapshot = {
        "semester_id": term,
        "semester_name": baseline.get("semester_name") or term,
        "semester_start_date": baseline.get("semester_start_date"),
        "provenance": {
            "source": "sisn",
            "source_payload_sha256": source_sha256,
            "fetched_at": fetched_at.isoformat(),
            "baseline": baseline_label,
            "fallback_main_classes": sorted(fallback_main_classes),
            "omitted_unscheduled_classes": sorted(omitted_unscheduled),
            "missing_baseline_classes": missing_baseline_classes,
            "baseline_meeting_fallbacks": baseline_meeting_fallbacks,
        },
        "courses": output_courses,
    }
    counts = {
        "source_courses": len(api_courses),
        "source_classes": raw_class_count,
        "source_schedules": raw_schedule_count,
        "source_classes_with_reserves": raw_reserve_class_count,
        "candidate_courses": len(output_courses),
        "candidate_offered_courses": sum(bool(course["sections"]) for course in output_courses),
        "candidate_sections": sum(len(course["sections"]) for course in output_courses),
        "candidate_meetings": sum(
            len(section["lectures"])
            for course in output_courses
            for section in course["sections"]
        ),
        "fallback_main_classes": len(fallback_main_classes),
        "omitted_unscheduled_classes": len(omitted_unscheduled),
        "missing_baseline_classes": len(missing_baseline_classes),
        "baseline_meeting_fallback_sections": len(baseline_meeting_fallbacks),
        "omitted_tba_schedules": transform_stats["omitted_tba_schedules"],
    }
    return SisnAdaptation(
        snapshot=snapshot,
        source_payload_sha256=source_sha256,
        fetched_at=fetched_at,
        counts=counts,
        warnings=warnings,
    )


def load_baseline(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SisnMappingError(f"could not load reviewed baseline {path}") from exc
    if not isinstance(value, dict):
        raise SisnMappingError("reviewed baseline must be an object")
    return value
