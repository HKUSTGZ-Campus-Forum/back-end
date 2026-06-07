from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.extensions import db
from app.models.academic_map import UserCourseRecord
from app.models.course import Course
from app.models.course_domain import (
    CourseCatalogRequirement,
    CourseCatalogVersion,
    CourseMeeting,
    CourseOffering,
    CoursePostOfferingTarget,
    CourseRequirementEdge,
    CourseSection,
    UserCourseAttempt,
    UserCourseState,
    UserOfferingCart,
    UserSectionSelection,
)
from app.models.post import Post
from app.models.scheduler_cart import SchedulerUserBundleCart, SchedulerUserCourseCart
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_section import SchedulerSection
from app.models.tag import Tag
from app.services.course_domain import (
    catalog_number_for_code,
    display_course_code,
    find_course_by_code,
    find_offering,
    grade_points_for_letter,
    normalize_course_code,
    subject_for_code,
)
from app.utils.semester import normalize_offering_identifier


COURSE_CODE_RE = re.compile(r"\b([A-Za-z]{4})\s*(\d{4})\b")
SEMESTER_ID_BY_SEASON = {
    "fall": "10",
    "winter": "20",
    "spring": "30",
    "summer": "40",
}


@dataclass
class MigrationSummary:
    scanned: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    anomalies: list[dict] = field(default_factory=list)


@dataclass
class CourseDomainMigrationReport:
    canonical_courses: MigrationSummary = field(default_factory=MigrationSummary)
    catalog_versions: MigrationSummary = field(default_factory=MigrationSummary)
    requirements: MigrationSummary = field(default_factory=MigrationSummary)
    offerings: MigrationSummary = field(default_factory=MigrationSummary)
    user_state: MigrationSummary = field(default_factory=MigrationSummary)
    review_targets: MigrationSummary = field(default_factory=MigrationSummary)

    def to_dict(self) -> dict:
        return asdict(self)


def _metadata_score(course: Course) -> int:
    return sum(
        1
        for value in [
            course.subject,
            course.catalog_number,
            course.course_title_abbr,
            course.pre_requirement,
            course.co_requirement,
            course.exclusion,
            course.vector,
        ]
        if value
    )


def _section_count(course: Course) -> int:
    return SchedulerSection.query.filter_by(course_id=course.id).count()


def _rank_course_candidate(course: Course) -> tuple:
    return (
        _section_count(course),
        _metadata_score(course),
        " " not in (course.code or ""),
        course.updated_at or course.created_at,
        course.id,
    )


def canonicalize_courses(*, apply: bool) -> MigrationSummary:
    summary = MigrationSummary()
    groups: dict[str, list[Course]] = {}
    for course in Course.query.filter_by(is_deleted=False).all():
        normalized = normalize_course_code(course.code)
        if not normalized:
            summary.skipped += 1
            continue
        groups.setdefault(normalized, []).append(course)
        summary.scanned += 1

    for normalized, courses in groups.items():
        canonical = max(courses, key=_rank_course_candidate)
        if len(courses) > 1:
            summary.anomalies.append({
                "type": "duplicate_course_code",
                "normalized_code": normalized,
                "course_ids": [course.id for course in courses],
                "codes": [course.code for course in courses],
                "selected_course_id": canonical.id,
            })
        if not apply:
            continue

        changed = False
        subject = subject_for_code(normalized)
        catalog_number = catalog_number_for_code(normalized)
        updates = {
            "normalized_code": normalized,
            "display_code": canonical.display_code or display_course_code(normalized),
            "canonical_title": canonical.canonical_title or canonical.name,
            "subject": canonical.subject or subject,
            "catalog_number": canonical.catalog_number or catalog_number,
        }
        for key, value in updates.items():
            if getattr(canonical, key) != value:
                setattr(canonical, key, value)
                changed = True
        for duplicate in courses:
            if duplicate.id == canonical.id:
                continue
            if duplicate.normalized_code is not None:
                duplicate.normalized_code = None
                changed = True
        if changed:
            summary.updated += 1

    return summary


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _int_credits(value: Any) -> int | None:
    matches = re.findall(r"\d+(?:\.\d+)?", str(value or ""))
    for match in matches:
        try:
            return int(float(match))
        except ValueError:
            continue
    return None


def migrate_catalog_versions(*, catalog_path: Path, apply: bool) -> MigrationSummary:
    summary = MigrationSummary()
    payload = _load_json(catalog_path)
    items = payload.get("courses") if isinstance(payload.get("courses"), list) else []

    for item in items:
        summary.scanned += 1
        code = normalize_course_code(item.get("course_code"))
        title = str(item.get("course_title") or "").strip()
        credits = _int_credits(item.get("credit"))
        if not code or not title or credits is None:
            summary.skipped += 1
            continue
        course = find_course_by_code(code)
        if course is None:
            if not apply:
                summary.created += 1
                continue
            course = Course(
                code=code,
                normalized_code=code,
                display_code=display_course_code(code),
                canonical_title=title,
                name=title,
                credits=credits,
                subject=(item.get("subject") or subject_for_code(code)),
                catalog_number=(item.get("catalog_number") or catalog_number_for_code(code)),
            )
            db.session.add(course)
            db.session.flush()

        existing = CourseCatalogVersion.query.filter_by(course_id=course.id, source="course_catalog.json").first()
        if existing is None:
            summary.created += 1
            if not apply:
                continue
            existing = CourseCatalogVersion(course_id=course.id, source="course_catalog.json", title=title, credits=credits)
        else:
            summary.updated += 1
        if apply:
            existing.title = title
            existing.title_abbr = item.get("course_title_abbr")
            existing.description = item.get("course_desc")
            existing.credits = credits
            existing.pre_requirement_raw = item.get("pre_requirement")
            existing.co_requirement_raw = item.get("co_requirement")
            existing.exclusion_raw = item.get("exclusion")
            existing.pg_course = bool(item.get("pg_course", False))
            existing.klms_course = bool(item.get("klms_course", False))
            existing.vector = item.get("vector")
            db.session.add(existing)

    return summary


def _prereq_by_code(prerequisite_path: Path) -> dict[str, dict]:
    payload = _load_json(prerequisite_path)
    courses = payload.get("courses") if isinstance(payload.get("courses"), list) else []
    return {
        normalize_course_code(item.get("course_code")): item
        for item in courses
        if normalize_course_code(item.get("course_code"))
    }


def _codes_from_text(text: str | None) -> list[str]:
    codes = []
    for subject, number in COURSE_CODE_RE.findall(text or ""):
        code = normalize_course_code(f"{subject}{number}")
        if code not in codes:
            codes.append(code)
    return codes


def _codes_from_expression(expression: Any) -> list[str]:
    if not isinstance(expression, dict):
        return []
    if expression.get("course_code"):
        return [normalize_course_code(expression.get("course_code"))]
    codes = []
    for item in expression.get("items", []):
        for code in _codes_from_expression(item):
            if code not in codes:
                codes.append(code)
    return codes


def _requirement_kind(raw_text: str | None, codes: list[str]) -> str:
    if not raw_text:
        return "empty"
    if not codes:
        return "non_course"
    normalized_text = normalize_course_code(raw_text)
    code_chars = "".join(codes)
    words = re.sub(r"[A-Z]{4}\s*\d{4}|AND|OR|\\(|\\)|\\[|\\]|&", "", str(raw_text), flags=re.IGNORECASE).strip()
    if words and normalized_text != code_chars:
        return "mixed"
    return "course"


def migrate_requirements(*, prerequisite_path: Path, apply: bool) -> MigrationSummary:
    summary = MigrationSummary()
    prereq_by_code = _prereq_by_code(prerequisite_path)
    relation_fields = [
        ("prerequisite", "pre_requirement_raw"),
        ("corequisite", "co_requirement_raw"),
        ("exclusion", "exclusion_raw"),
    ]

    if apply:
        CourseRequirementEdge.query.delete()
        CourseCatalogRequirement.query.delete()

    for version in CourseCatalogVersion.query.all():
        course = db.session.get(Course, version.course_id)
        if not course:
            summary.skipped += 1
            continue
        target_code = normalize_course_code(course.normalized_code or course.code)
        for relation_type, field_name in relation_fields:
            raw_text = getattr(version, field_name)
            if not raw_text:
                continue
            summary.scanned += 1
            expression = {}
            codes = _codes_from_text(raw_text)
            source = "course_catalog.json"
            if relation_type == "prerequisite":
                prereq = prereq_by_code.get(target_code) or {}
                expression = prereq.get("prerequisite_expression") or {}
                codes = _codes_from_expression(expression) or codes
                source = "course_prerequisites.json" if expression else source

            summary.created += 1
            if not apply:
                continue

            requirement = CourseCatalogRequirement(
                catalog_version_id=version.id,
                relation_type=relation_type,
                raw_text=raw_text,
                normalized_text=normalize_course_code(raw_text),
                requirement_kind=_requirement_kind(raw_text, codes),
                expression_json=expression,
                parser_version="20260607",
                source=source,
            )
            db.session.add(requirement)
            db.session.flush()

            for code in codes:
                source_course = find_course_by_code(code)
                if source_course is None:
                    summary.anomalies.append({
                        "type": "unresolved_requirement_course",
                        "target_course_code": target_code,
                        "relation_type": relation_type,
                        "source_course_code": code,
                    })
                    continue
                db.session.add(CourseRequirementEdge(
                    requirement_id=requirement.id,
                    from_course_id=source_course.id,
                    to_course_id=course.id,
                    relation_type=relation_type,
                ))

    return summary


def _current_version_for_course(course_id: int) -> CourseCatalogVersion | None:
    return (
        CourseCatalogVersion.query
        .filter_by(course_id=course_id)
        .order_by(CourseCatalogVersion.id.desc())
        .first()
    )


def migrate_offerings(*, apply: bool) -> MigrationSummary:
    summary = MigrationSummary()
    sections = SchedulerSection.query.all()
    groups: dict[tuple[int, str], list[SchedulerSection]] = {}
    for section in sections:
        groups.setdefault((section.course_id, section.semester_id), []).append(section)
        summary.scanned += 1

    for (course_id, semester_id), group_sections in groups.items():
        course = db.session.get(Course, course_id)
        if course is None:
            summary.skipped += len(group_sections)
            continue
        version = _current_version_for_course(course.id)
        offering = CourseOffering.query.filter_by(course_id=course.id, semester_id=semester_id).first()
        if offering is None:
            summary.created += 1
            if not apply:
                continue
            offering = CourseOffering(
                course_id=course.id,
                semester_id=semester_id,
                catalog_version_id=version.id if version else None,
                offering_code=course.normalized_code or normalize_course_code(course.code),
                title_snapshot=(version.title if version else course.name),
                credits_snapshot=(version.credits if version else course.credits),
                source="legacy_scheduler_sections",
                status="offered",
            )
            db.session.add(offering)
            db.session.flush()
        elif apply:
            CourseMeeting.query.join(CourseSection).filter(CourseSection.offering_id == offering.id).delete(synchronize_session=False)
            CourseSection.query.filter_by(offering_id=offering.id).delete(synchronize_session=False)

        if not apply:
            continue
        for legacy_section in group_sections:
            section = CourseSection(
                offering_id=offering.id,
                source_section_id=legacy_section.section_id,
                name=legacy_section.name,
                section_type=legacy_section.section_type,
                bundle=legacy_section.bundle,
                layer=legacy_section.layer,
                quota=legacy_section.quota,
                is_main=legacy_section.is_main,
            )
            db.session.add(section)
            db.session.flush()
            lectures = SchedulerLecture.query.filter_by(
                semester_id=legacy_section.semester_id,
                section_id=legacy_section.section_id,
            ).all()
            for lecture in lectures:
                db.session.add(CourseMeeting(
                    section_id=section.id,
                    day=lecture.day,
                    start_time=lecture.start_time,
                    end_time=lecture.end_time,
                    room=lecture.room,
                    instructor_text=lecture.instructor,
                ))

    return summary


def _attempt_status(record: UserCourseRecord) -> str | None:
    if record.status == UserCourseRecord.STATUS_COMPLETED:
        if str(record.grade or "").strip().upper() == "F":
            return "failed"
        return "completed"
    if record.status in {UserCourseRecord.STATUS_IN_PROGRESS, UserCourseRecord.STATUS_PLANNED}:
        return "in_progress"
    return None


def _state_status(record: UserCourseRecord) -> str:
    if record.status == UserCourseRecord.STATUS_COMPLETED:
        return "completed"
    if record.status in {UserCourseRecord.STATUS_IN_PROGRESS, UserCourseRecord.STATUS_PLANNED}:
        return "in_progress"
    if record.status == UserCourseRecord.STATUS_INTERESTED:
        return "interested"
    return "not_taken"


def migrate_user_academic_state(*, apply: bool) -> MigrationSummary:
    summary = MigrationSummary()
    if apply:
        UserCourseState.query.delete()
        UserCourseAttempt.query.delete()

    records = UserCourseRecord.query.all()
    for record in records:
        summary.scanned += 1
        course = find_course_by_code(record.course_code)
        if course is None:
            summary.anomalies.append({
                "type": "unresolved_user_course_record_course",
                "record_id": record.id,
                "course_code": record.course_code,
            })
            summary.skipped += 1
            continue

        attempt_status = _attempt_status(record)
        offering = find_offering(course, record.term_code) if record.term_code else None
        attempt = None
        if attempt_status and offering is not None:
            summary.created += 1
            if apply:
                attempt = UserCourseAttempt(
                    user_id=record.user_id,
                    course_id=course.id,
                    offering_id=offering.id,
                    status=attempt_status,
                    grade_letter=record.grade,
                    grade_points=grade_points_for_letter(record.grade),
                    term_label=record.term_label,
                    source="manual" if record.import_source == UserCourseRecord.SOURCE_MANUAL else "transcript_import",
                    raw_payload=record.raw_payload or {},
                )
                db.session.add(attempt)
                db.session.flush()
        elif attempt_status:
            summary.anomalies.append({
                "type": "unresolved_user_course_record_offering",
                "record_id": record.id,
                "course_code": record.course_code,
                "term_code": record.term_code,
            })

        if apply:
            state = UserCourseState.query.filter_by(user_id=record.user_id, course_id=course.id).first()
            if state is None:
                state = UserCourseState(
                    user_id=record.user_id,
                    course_id=course.id,
                    status=_state_status(record),
                    source="derived" if attempt else "manual",
                )
            if attempt is not None and attempt.status == "completed":
                current_best = state.best_grade_points
                new_points = attempt.grade_points
                if current_best is None or (new_points is not None and new_points > current_best):
                    state.best_attempt_id = attempt.id
                    state.best_grade_points = new_points
                    state.best_grade_letter = attempt.grade_letter
                    state.status = "completed"
                    state.source = "derived"
            db.session.add(state)

    return summary


def migrate_scheduler_carts(*, apply: bool) -> MigrationSummary:
    summary = MigrationSummary()
    if apply:
        UserSectionSelection.query.delete()
        UserOfferingCart.query.delete()

    for legacy_cart in SchedulerUserCourseCart.query.all():
        summary.scanned += 1
        course = find_course_by_code(legacy_cart.course_code)
        offering = find_offering(course, legacy_cart.semester_id) if course else None
        if offering is None:
            summary.anomalies.append({
                "type": "unresolved_scheduler_cart_offering",
                "user_id": legacy_cart.user_id,
                "course_code": legacy_cart.course_code,
                "semester_id": legacy_cart.semester_id,
            })
            summary.skipped += 1
            continue
        summary.created += 1
        if not apply:
            continue
        db.session.add(UserOfferingCart(
            user_id=legacy_cart.user_id,
            offering_id=offering.id,
            enabled=legacy_cart.enabled,
        ))
        bundles = SchedulerUserBundleCart.query.filter_by(
            user_id=legacy_cart.user_id,
            semester_id=legacy_cart.semester_id,
            course_code=legacy_cart.course_code,
            enabled=True,
        ).all()
        for bundle in bundles:
            sections = CourseSection.query.filter_by(
                offering_id=offering.id,
                bundle=bundle.id,
                layer=bundle.layer,
            ).all()
            for section in sections:
                db.session.add(UserSectionSelection(
                    user_id=legacy_cart.user_id,
                    offering_id=offering.id,
                    section_id=section.id,
                    enabled=True,
                    source="cart",
                ))

    return summary


def _semester_id_from_offering_tag(tag_name: str) -> str | None:
    normalized = normalize_offering_identifier(tag_name)
    if not normalized:
        return None
    year, season = normalized
    suffix = SEMESTER_ID_BY_SEASON.get(season)
    if not suffix:
        return None
    return f"{int(year) % 100:02d}{suffix}"


def _post_tag_names(post: Post) -> list[str]:
    return [tag.name for tag in post.tags]


def _review_resolution(post: Post) -> tuple[CourseOffering | None, dict | None]:
    tag_names = _post_tag_names(post)
    course = None
    course_code = None
    semester_id = None
    for tag_name in tag_names:
        candidate_course = find_course_by_code(tag_name)
        if candidate_course is not None:
            course = candidate_course
            course_code = candidate_course.normalized_code or normalize_course_code(candidate_course.code)
        candidate_semester_id = _semester_id_from_offering_tag(tag_name)
        if candidate_semester_id:
            semester_id = candidate_semester_id

    if course is None or semester_id is None:
        return None, {
            "record_type": "post",
            "record_id": post.id,
            "reason": "course_or_offering_tag_missing",
            "legacy_tags": tag_names,
            "suggested_course_code": course_code,
            "suggested_semester_id": semester_id,
        }

    offering = find_offering(course, semester_id)
    if offering is None:
        return None, {
            "record_type": "post",
            "record_id": post.id,
            "reason": "course_offering_not_found",
            "legacy_tags": tag_names,
            "suggested_course_code": course_code,
            "suggested_semester_id": semester_id,
        }
    return offering, None


def migrate_review_targets(*, anomaly_path: Path, apply: bool) -> MigrationSummary:
    summary = MigrationSummary()
    if apply:
        CoursePostOfferingTarget.query.delete()

    review_posts = (
        Post.query
        .filter(Post.tags.any(Tag.name == "course-review"))
        .all()
    )
    for post in review_posts:
        summary.scanned += 1
        offering, anomaly = _review_resolution(post)
        if anomaly:
            summary.anomalies.append(anomaly)
            summary.skipped += 1
            continue
        summary.created += 1
        if apply:
            db.session.add(CoursePostOfferingTarget(
                post_id=post.id,
                course_offering_id=offering.id,
            ))

    anomaly_path.parent.mkdir(parents=True, exist_ok=True)
    anomaly_path.write_text(json.dumps({
        "items": summary.anomalies,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
