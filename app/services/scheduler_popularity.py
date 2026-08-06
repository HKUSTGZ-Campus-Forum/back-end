from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import and_, func, or_

from app.extensions import db
from app.models.course import Course
from app.models.course_domain import (
    CourseOffering,
    CourseSection,
    UserOfferingCart,
    UserSectionSelection,
)
from app.models.scheduler_popularity import SchedulerPopularityEvent
from app.models.user import User
from app.services.course_domain import normalize_course_code
from app.services.institutional_email import (
    canonical_verified_email_account,
    is_institutional_email,
    normalize_email,
)


POPULARITY_STATES = {"looking", "scheduling"}


def is_eligible_popularity_user(user: User | None) -> bool:
    return bool(
        user
        and not user.is_deleted
        and user.email_verified
        and normalize_email(user.email)
        and is_institutional_email(user.email)
    )


def is_canonical_popularity_user(user: User | None) -> bool:
    """Return whether user is the oldest eligible account for its email."""
    if not is_eligible_popularity_user(user):
        return False
    canonical = canonical_verified_email_account(user.email)
    return canonical is not None and canonical.id == user.id


def _canonical_contributor_ids(offering_ids: list[int]) -> set[int]:
    if not offering_ids:
        return set()

    relevant_users = (
        User.query
        .join(UserOfferingCart, UserOfferingCart.user_id == User.id)
        .filter(
            UserOfferingCart.offering_id.in_(offering_ids),
            User.is_deleted.is_(False),
            User.email_verified.is_(True),
        )
        .all()
    )
    normalized_emails = {
        normalize_email(user.email)
        for user in relevant_users
        if normalize_email(user.email) and is_institutional_email(user.email)
    }
    if not normalized_emails:
        return set()

    eligible_candidates = (
        User.query
        .filter(
            User.is_deleted.is_(False),
            User.email_verified.is_(True),
            func.lower(func.trim(User.email)).in_(normalized_emails),
        )
        .order_by(User.created_at.asc(), User.id.asc())
        .all()
    )
    canonical_by_email: dict[str, User] = {}
    for candidate in eligible_candidates:
        normalized = normalize_email(candidate.email)
        if normalized not in normalized_emails or not is_institutional_email(candidate.email):
            continue
        canonical_by_email.setdefault(normalized, candidate)

    return {candidate.id for candidate in canonical_by_email.values()}


def normalize_popularity_course_codes(values: Iterable[str], *, limit: int = 30) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        for token in str(raw_value or "").split(","):
            code = normalize_course_code(token)
            if not code or code in seen:
                continue
            if len(code) > 32:
                raise ValueError("Invalid course code")
            seen.add(code)
            normalized.append(code)
    if len(normalized) > limit:
        raise ValueError(f"At most {limit} course codes are allowed")
    return normalized


def build_popularity_snapshot(
    *,
    viewer_id: int,
    semester_id: str,
    course_codes: list[str],
) -> dict:
    generated_at = datetime.now(timezone.utc).isoformat()
    empty = {
        "semester_id": semester_id,
        "generated_at": generated_at,
        "courses": [],
    }
    if not course_codes:
        return empty

    target_rows = (
        db.session.query(UserOfferingCart, CourseOffering, Course)
        .join(CourseOffering, CourseOffering.id == UserOfferingCart.offering_id)
        .join(Course, Course.id == CourseOffering.course_id)
        .filter(
            UserOfferingCart.user_id == viewer_id,
            CourseOffering.semester_id == semester_id,
            CourseOffering.status == "offered",
            Course.is_deleted.is_(False),
            or_(
                Course.normalized_code.in_(course_codes),
                func.upper(func.replace(Course.code, " ", "")).in_(course_codes),
            ),
        )
        .order_by(Course.code, CourseOffering.id)
        .all()
    )
    if not target_rows:
        return empty

    offering_ids = [offering.id for _, offering, _ in target_rows]
    sections = (
        CourseSection.query
        .filter(CourseSection.offering_id.in_(offering_ids))
        .order_by(CourseSection.offering_id, CourseSection.layer, CourseSection.bundle, CourseSection.source_section_id)
        .all()
    )
    sections_by_offering: dict[int, list[CourseSection]] = {}
    for section in sections:
        sections_by_offering.setdefault(section.offering_id, []).append(section)

    output_by_offering: dict[int, dict] = {}
    section_output_by_id: dict[int, dict] = {}
    courses = []
    for _, offering, course in target_rows:
        output = {
            "course_code": course.code,
            "looking_count": 0,
            "scheduling_count": 0,
            "sections": [],
        }
        for section in sections_by_offering.get(offering.id, []):
            section_output = {
                "section_id": section.source_section_id,
                "looking_count": 0,
                "scheduling_count": 0,
            }
            output["sections"].append(section_output)
            section_output_by_id[section.id] = section_output
        courses.append(output)
        output_by_offering[offering.id] = output

    canonical_ids = _canonical_contributor_ids(offering_ids)
    if canonical_ids:
        course_counts = (
            db.session.query(
                UserOfferingCart.offering_id,
                UserOfferingCart.enabled,
                func.count(func.distinct(UserOfferingCart.user_id)),
            )
            .filter(
                UserOfferingCart.offering_id.in_(offering_ids),
                UserOfferingCart.user_id.in_(canonical_ids),
            )
            .group_by(UserOfferingCart.offering_id, UserOfferingCart.enabled)
            .all()
        )
        for offering_id, enabled, count in course_counts:
            output = output_by_offering.get(offering_id)
            if output is not None:
                key = "scheduling_count" if enabled else "looking_count"
                output[key] = count

        section_counts = (
            db.session.query(
                UserSectionSelection.section_id,
                UserOfferingCart.enabled,
                func.count(func.distinct(UserSectionSelection.user_id)),
            )
            .join(
                UserOfferingCart,
                and_(
                    UserOfferingCart.user_id == UserSectionSelection.user_id,
                    UserOfferingCart.offering_id == UserSectionSelection.offering_id,
                ),
            )
            .join(
                CourseSection,
                and_(
                    CourseSection.id == UserSectionSelection.section_id,
                    CourseSection.offering_id == UserSectionSelection.offering_id,
                ),
            )
            .filter(
                UserSectionSelection.offering_id.in_(offering_ids),
                UserSectionSelection.user_id.in_(canonical_ids),
                UserSectionSelection.enabled.is_(True),
            )
            .group_by(UserSectionSelection.section_id, UserOfferingCart.enabled)
            .all()
        )
        for section_id, enabled, count in section_counts:
            output = section_output_by_id.get(section_id)
            if output is not None:
                key = "scheduling_count" if enabled else "looking_count"
                output[key] = count

    return {
        "semester_id": semester_id,
        "generated_at": generated_at,
        "courses": courses,
    }


def popularity_state(enabled: bool) -> str:
    return "scheduling" if enabled else "looking"


def record_popularity_transition(
    *,
    contributor_is_eligible: bool,
    offering_id: int,
    from_state: str | None,
    to_state: str | None,
    reason: str,
    section: CourseSection | None = None,
) -> None:
    if not contributor_is_eligible or from_state == to_state:
        return
    if from_state not in POPULARITY_STATES | {None} or to_state not in POPULARITY_STATES | {None}:
        raise ValueError("Invalid popularity transition")
    if from_state is None and to_state is None:
        return

    db.session.add(SchedulerPopularityEvent(
        offering_id=offering_id,
        section_id=section.id if section else None,
        section_source_id=section.source_section_id if section else None,
        from_state=from_state,
        to_state=to_state,
        reason=reason,
    ))
