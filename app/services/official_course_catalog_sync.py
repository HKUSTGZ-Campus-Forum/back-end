from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from app.extensions import db
from app.models.course import Course
from app.models.course_domain import CourseCatalogRequirement, CourseCatalogVersion
from app.services.course_domain import display_course_code, normalize_course_code
from app.services.course_relationships import (
    RELATION_FIELDS,
    PARSER_VERSION,
    parse_requirement,
    replace_catalog_version_requirements,
)


CATALOG_SOURCE = "sis_course_catalog"


class OfficialCourseCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class OfficialCatalogCourse:
    code: str
    title: str
    description: str | None
    credits: int
    prerequisite: str | None
    corequisite: str | None
    exclusion: str | None
    subject: str
    catalog_number: str
    title_abbr: str | None
    vector: str | None
    academic_year: str | None
    term_name: str | None
    previous_course_code: str | None
    colist: str | None
    source_updated_at: str | None
    source_course_id: str | None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _credits(value: Any, *, code: str) -> int:
    try:
        parsed = Decimal(str(value or "0").strip())
    except InvalidOperation as exc:
        raise OfficialCourseCatalogError(f"{code}: invalid minUnits value") from exc
    if parsed < 0 or parsed != parsed.to_integral_value():
        raise OfficialCourseCatalogError(
            f"{code}: credits must be a non-negative whole number for the current schema"
        )
    return int(parsed)


def normalize_official_catalog_records(records: Any) -> list[OfficialCatalogCourse]:
    if not isinstance(records, list):
        raise OfficialCourseCatalogError("official catalog response records must be a list")
    normalized: dict[str, OfficialCatalogCourse] = {}
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            raise OfficialCourseCatalogError(f"record {index}: expected an object")
        code = normalize_course_code(
            item.get("crseCode")
            or f"{item.get('crsePrefix') or ''}{item.get('catalogNbr') or ''}"
        )
        title = str(item.get("crseTitle") or "").strip()
        if not code or not title:
            raise OfficialCourseCatalogError(f"record {index}: course code and title are required")
        subject = str(item.get("crsePrefix") or code[:4]).strip().upper()
        catalog_number = str(item.get("catalogNbr") or code[len(subject):]).strip().upper()
        course = OfficialCatalogCourse(
            code=code,
            title=title,
            description=_optional_text(item.get("crseDescr")),
            credits=_credits(item.get("minUnits"), code=code),
            prerequisite=_optional_text(item.get("crsePrerequisite")),
            corequisite=_optional_text(item.get("crseCorequisite")),
            exclusion=_optional_text(item.get("crseExclusion")),
            subject=subject,
            catalog_number=catalog_number,
            title_abbr=_optional_text(item.get("crseTitleAbbr")),
            vector=_optional_text(item.get("crseVector")),
            academic_year=_optional_text(item.get("acadYearFull")),
            term_name=_optional_text(item.get("termName")),
            previous_course_code=_optional_text(item.get("prevCrseCode")),
            colist=_optional_text(item.get("crseColist")),
            source_updated_at=_optional_text(item.get("adsSyncTime")),
            source_course_id=_optional_text(item.get("crseId")),
        )
        if code in normalized:
            raise OfficialCourseCatalogError(f"duplicate official catalog course {code}")
        normalized[code] = course
    return [normalized[code] for code in sorted(normalized)]


def fetch_official_course_catalog(
    *,
    url: str,
    term: str,
    career: str = "UG",
    timeout_seconds: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    response = requests.post(
        url,
        params={"page": 1, "size": 1000},
        json={
            "filter": {
                "and": [
                    {"=": {"field": "term_code", "value": term}},
                    {"=": {"field": "career_type", "value": career}},
                ]
            }
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200:
        raise OfficialCourseCatalogError(
            f"official catalog returned application code {payload.get('code')!r}"
        )
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise OfficialCourseCatalogError("official catalog response is missing data.records")
    return data["records"], data.get("pagination") or {}


def _course_hash(course: OfficialCatalogCourse) -> str:
    serialized = json.dumps(asdict(course), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _snapshot_hash(courses: list[OfficialCatalogCourse]) -> str:
    serialized = json.dumps(
        [asdict(course) for course in courses],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def sync_official_course_catalog_records(
    records: Any,
    *,
    term: str,
    apply: bool = False,
    min_courses: int = 150,
    max_courses: int = 500,
) -> dict[str, Any]:
    courses = normalize_official_catalog_records(records)
    if not min_courses <= len(courses) <= max_courses:
        raise OfficialCourseCatalogError(
            f"official catalog course count {len(courses)} outside guard range "
            f"[{min_courses}, {max_courses}]"
        )

    existing_groups: dict[str, list[Course]] = {}
    for course in Course.query.filter_by(is_deleted=False).all():
        identity = normalize_course_code(course.normalized_code or course.code)
        existing_groups.setdefault(identity, []).append(course)
    existing_courses = {
        code: max(
            matches,
            key=lambda course: (
                normalize_course_code(course.normalized_code) == code,
                normalize_course_code(course.code) == code and " " not in str(course.code or ""),
                bool(course.subject and course.catalog_number),
                course.id or 0,
            ),
        )
        for code, matches in existing_groups.items()
    }
    inserted_codes = [course.code for course in courses if course.code not in existing_courses]
    version_changes: list[OfficialCatalogCourse] = []
    version_rebuilds: list[OfficialCatalogCourse] = []
    for item in courses:
        existing = existing_courses.get(item.code)
        source_version = f"{term}:{_course_hash(item)[:16]}"
        version = None if existing is None else CourseCatalogVersion.query.filter_by(
            course_id=existing.id,
            source=CATALOG_SOURCE,
            source_version=source_version,
        ).first()
        if version is None:
            version_changes.append(item)
            continue
        requirements = CourseCatalogRequirement.query.filter_by(catalog_version_id=version.id).all()
        if len(requirements) != 3 or any(item.parser_version != PARSER_VERSION for item in requirements):
            version_rebuilds.append(item)

    report: dict[str, Any] = {
        "status": "applied" if apply else "dry-run",
        "source": CATALOG_SOURCE,
        "term": term,
        "snapshot_sha256": _snapshot_hash(courses),
        "courses": len(courses),
        "course_rows_to_insert": len(inserted_codes),
        "catalog_versions_to_create": len(version_changes),
        "catalog_versions_to_rebuild": len(version_rebuilds),
        "requirements_to_build": (len(version_changes) + len(version_rebuilds)) * 3,
        "inserted_course_codes": inserted_codes,
    }
    known_codes = set(existing_courses) | {course.code for course in courses}
    relation_counts = {relation_type: 0 for relation_type in RELATION_FIELDS}
    requirement_kind_counts = {kind: 0 for kind in ("course", "mixed", "non_course", "empty")}
    unresolved_codes: set[str] = set()
    reference_count = 0
    for item in courses:
        values = {
            "prerequisite": item.prerequisite,
            "corequisite": item.corequisite,
            "exclusion": item.exclusion,
        }
        for relation_type, raw_text in values.items():
            parsed = parse_requirement(raw_text)
            requirement_kind_counts[parsed.requirement_kind] += 1
            if parsed.raw_text:
                relation_counts[relation_type] += 1
            reference_count += len(parsed.course_codes)
            unresolved_codes.update(code for code in parsed.course_codes if code not in known_codes)
    report.update({
        "relation_counts": relation_counts,
        "requirement_kind_counts": requirement_kind_counts,
        "course_references": reference_count,
        "unresolved_course_codes": sorted(unresolved_codes),
    })
    if not apply:
        return report

    try:
        course_by_code = dict(existing_courses)
        for item in courses:
            course = course_by_code.get(item.code)
            if course is None:
                course = Course(
                    code=item.code,
                    normalized_code=item.code,
                    display_code=display_course_code(item.code),
                    name=item.title,
                    canonical_title=item.title,
                    description=item.description,
                    credits=item.credits,
                    subject=item.subject,
                    catalog_number=item.catalog_number,
                    is_active=True,
                    is_deleted=False,
                )
                db.session.add(course)
                course_by_code[item.code] = course
            else:
                course.normalized_code = item.code
                course.display_code = display_course_code(item.code)
                course.canonical_title = item.title
                course.name = item.title
                course.description = item.description
                course.credits = item.credits
                course.subject = item.subject
                course.catalog_number = item.catalog_number
                course.is_active = True
                course.is_deleted = False
                course.deleted_at = None
            if item.title_abbr:
                course.course_title_abbr = item.title_abbr
            db.session.add(course)
        db.session.flush()

        created_versions: list[CourseCatalogVersion] = []
        versions_to_build: list[CourseCatalogVersion] = []
        snapshot_sha256 = report["snapshot_sha256"]
        for item in courses:
            course = course_by_code[item.code]
            source_version = f"{term}:{_course_hash(item)[:16]}"
            version = CourseCatalogVersion.query.filter_by(
                course_id=course.id,
                source=CATALOG_SOURCE,
                source_version=source_version,
            ).first()
            if version is not None:
                requirements = CourseCatalogRequirement.query.filter_by(catalog_version_id=version.id).all()
                if len(requirements) != 3 or any(
                    requirement.parser_version != PARSER_VERSION for requirement in requirements
                ):
                    versions_to_build.append(version)
                continue
            version = CourseCatalogVersion(
                course_id=course.id,
                source=CATALOG_SOURCE,
                source_version=source_version,
                catalog_year=item.academic_year,
                title=item.title,
                title_abbr=item.title_abbr,
                description=item.description,
                credits=item.credits,
                pre_requirement_raw=item.prerequisite,
                co_requirement_raw=item.corequisite,
                exclusion_raw=item.exclusion,
                pg_course=False,
                vector=item.vector,
                source_metadata={
                    "snapshot_sha256": snapshot_sha256,
                    "source_course_id": item.source_course_id,
                    "source_updated_at": item.source_updated_at,
                    "term_name": item.term_name,
                    "previous_course_code": item.previous_course_code,
                    "colist": item.colist,
                    "authority": "HKUST(GZ) Program & Course Catalog",
                },
                effective_from_semester_id=term,
            )
            db.session.add(version)
            db.session.flush()
            created_versions.append(version)
            versions_to_build.append(version)

        edge_counts: dict[str, Any] = {
            "requirements": 0,
            "edges": 0,
            "unresolved": 0,
            "unresolved_codes": [],
        }
        for version in versions_to_build:
            counts = replace_catalog_version_requirements(
                version,
                course_by_code=course_by_code,
            )
            for key in ("requirements", "edges", "unresolved"):
                edge_counts[key] += counts[key]
            edge_counts["unresolved_codes"] = sorted(set(
                edge_counts["unresolved_codes"] + counts["unresolved_codes"]
            ))
        db.session.commit()
        report.update(edge_counts)
        report["catalog_versions_created"] = len(created_versions)
        report["catalog_versions_rebuilt"] = len(versions_to_build) - len(created_versions)
        return report
    except Exception:
        db.session.rollback()
        raise
