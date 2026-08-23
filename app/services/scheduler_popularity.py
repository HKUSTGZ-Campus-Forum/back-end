from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from math import ceil
import time
from typing import Iterable, NamedTuple

from sqlalchemy import Integer, and_, cast, func, or_, text

from app.extensions import db
from app.models.course import Course
from app.models.course_domain import (
    CourseOffering,
    CourseMeeting,
    CourseSection,
    UserOfferingCart,
    UserSectionSelection,
)
from app.models.scheduler_popularity import (
    SchedulerPopularityCourseSnapshot,
    SchedulerPopularityEvent,
    SchedulerPopularitySectionSnapshot,
    SchedulerPopularitySnapshotRun,
)
from app.models.scheduler_plan import SchedulerPlan, SchedulerPlanCourse
from app.models.user import User
from app.services.course_domain import normalize_course_code
from app.services.institutional_email import (
    canonical_verified_email_account,
    is_institutional_email,
    normalize_email,
)


POPULARITY_STATES = {"looking", "scheduling"}
POPULARITY_HISTORY_SEMESTER = "2610"
POPULARITY_HISTORY_INTERVAL_SECONDS = 300
POPULARITY_HISTORY_START_AT = datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc)
POPULARITY_HISTORY_END_AT = datetime(2026, 9, 30, 15, 59, tzinfo=timezone.utc)
POPULARITY_HISTORY_MAX_POINTS = 1000
POPULARITY_HISTORY_MAX_RANGE_SECONDS = 62 * 24 * 60 * 60
POPULARITY_HISTORY_FRESH_AFTER_SECONDS = 2 * POPULARITY_HISTORY_INTERVAL_SECONDS
POPULARITY_HISTORY_EXPECTED_COURSE_COUNT = 383
POPULARITY_HISTORY_EXPECTED_SECTION_COUNT = 801
POPULARITY_HISTORY_EXPECTED_MEETING_COUNT = 820
# Derived from the canonical identifiers and meeting fields in the reviewed
# ``app/data/pending/scheduler_offerings/26-27fall.json`` package.  This is not
# merely a file hash: the same deterministic digest is recomputed from the
# database before every sample, so a swapped entity fails even if totals match.
POPULARITY_HISTORY_EXPECTED_UNIVERSE_SHA256 = (
    "16e8154a923197ef8a7f679c1f91a9ea5c71a4e6cdd479939b76d529acea2aa8"
)
_POPULARITY_HISTORY_ADVISORY_LOCK = 261030005
_POPULARITY_HISTORY_LOCK_RETRY_SECONDS = 1.0


class PopularityHistoryUniverse(NamedTuple):
    sha256: str
    course_count: int
    section_count: int
    meeting_count: int


POPULARITY_HISTORY_EXPECTED_UNIVERSE = PopularityHistoryUniverse(
    POPULARITY_HISTORY_EXPECTED_UNIVERSE_SHA256,
    POPULARITY_HISTORY_EXPECTED_COURSE_COUNT,
    POPULARITY_HISTORY_EXPECTED_SECTION_COUNT,
    POPULARITY_HISTORY_EXPECTED_MEETING_COUNT,
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def popularity_history_bucket(now: datetime) -> datetime:
    """Floor an instant to the source five-minute UTC bucket."""
    instant = _as_utc(now)
    minute = instant.minute - instant.minute % 5
    return instant.replace(minute=minute, second=0, microsecond=0)


def _popularity_history_universe(
    semester_id: str,
) -> tuple[PopularityHistoryUniverse, list[tuple[CourseOffering, Course]]]:
    """Return the deterministic offered universe and the rows used to sample it."""
    offerings = (
        db.session.query(CourseOffering, Course)
        .join(Course, Course.id == CourseOffering.course_id)
        .filter(
            CourseOffering.semester_id == semester_id,
            CourseOffering.status == "offered",
            Course.is_deleted.is_(False),
        )
        .order_by(CourseOffering.id)
        .all()
    )
    offering_ids = [offering.id for offering, _course in offerings]
    course_identifiers: list[list[str]] = []
    normalized_by_offering: dict[int, str] = {}
    for offering, course in offerings:
        code = normalize_course_code(course.code)
        if not code:
            raise RuntimeError(f"offering {offering.id} has no canonical course code")
        if code in normalized_by_offering.values():
            raise RuntimeError(f"reviewed semester contains duplicate offered course {code}")
        normalized_by_offering[offering.id] = code
        course_identifiers.append([code])

    sections = (
        CourseSection.query
        .filter(
            CourseSection.offering_id.in_(offering_ids),
            CourseSection.status == "active",
        )
        .order_by(CourseSection.offering_id, CourseSection.source_section_id)
        .all()
        if offering_ids else []
    )
    section_identifiers: list[list[str]] = []
    section_scope_by_id: dict[int, tuple[str, str]] = {}
    for section in sections:
        code = normalized_by_offering[section.offering_id]
        source_section_id = str(section.source_section_id or "").strip()
        if not source_section_id:
            raise RuntimeError(f"section {section.id} has no canonical source identifier")
        section_scope_by_id[section.id] = (code, source_section_id)
        section_identifiers.append([code, source_section_id])

    section_ids = list(section_scope_by_id)
    meetings = (
        CourseMeeting.query
        .filter(CourseMeeting.section_id.in_(section_ids))
        .order_by(
            CourseMeeting.section_id,
            CourseMeeting.day,
            CourseMeeting.start_time,
            CourseMeeting.end_time,
            CourseMeeting.room,
            CourseMeeting.instructor_text,
        )
        .all()
        if section_ids else []
    )
    meeting_identifiers = [
        [
            *section_scope_by_id[meeting.section_id],
            int(meeting.day),
            int(meeting.start_time),
            int(meeting.end_time),
            str(meeting.room),
            str(meeting.instructor_text),
        ]
        for meeting in meetings
    ]
    canonical_payload = {
        "semester_id": semester_id,
        "courses": sorted(course_identifiers),
        "sections": sorted(section_identifiers),
        "meetings": sorted(meeting_identifiers),
    }
    digest = hashlib.sha256(json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return PopularityHistoryUniverse(
        digest,
        len(course_identifiers),
        len(section_identifiers),
        len(meeting_identifiers),
    ), offerings


def _assert_popularity_history_universe(
    actual: PopularityHistoryUniverse,
    expected: PopularityHistoryUniverse,
) -> None:
    if actual != expected:
        raise RuntimeError(
            "popularity sampler refused an unreviewed semester universe: "
            f"expected sha256/counts={expected.sha256}/"
            f"{expected.course_count}/{expected.section_count}/{expected.meeting_count}, "
            f"observed sha256/counts={actual.sha256}/"
            f"{actual.course_count}/{actual.section_count}/{actual.meeting_count}"
        )


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


def _canonical_contributor_ids(
    offering_ids: list[int],
    *,
    saved_plan_course_codes: list[str] | None = None,
    saved_plan_semester_id: str | None = None,
) -> set[int]:
    if not offering_ids:
        return set()

    relevant_user_ids = {
        user_id
        for user_id, in db.session.query(UserOfferingCart.user_id)
        .filter(UserOfferingCart.offering_id.in_(offering_ids))
        .distinct()
        .all()
    }
    if saved_plan_course_codes and saved_plan_semester_id:
        relevant_user_ids.update(
            owner_id
            for owner_id, in db.session.query(SchedulerPlan.owner_id)
            .join(SchedulerPlanCourse, SchedulerPlanCourse.plan_id == SchedulerPlan.id)
            .filter(
                SchedulerPlanCourse.normalized_course_code.in_(saved_plan_course_codes),
                SchedulerPlan.semester_id == saved_plan_semester_id,
                SchedulerPlan.is_deleted.is_(False),
            )
            .distinct()
            .all()
        )
    if not relevant_user_ids:
        return set()

    relevant_users = User.query.filter(
        User.id.in_(relevant_user_ids),
        User.is_deleted.is_(False),
        User.email_verified.is_(True),
    ).all()
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
    output_by_offering: dict[int, dict] = {}
    output_by_course_code: dict[str, dict] = {}
    courses = []
    for _, offering, course in target_rows:
        normalized_code = normalize_course_code(course.code)
        output = {
            "course_code": course.code,
            "cart_count": 0,
            "saved_plan_count": 0,
        }
        courses.append(output)
        output_by_offering[offering.id] = output
        output_by_course_code[normalized_code] = output

    canonical_ids = _canonical_contributor_ids(
        offering_ids,
        saved_plan_course_codes=list(output_by_course_code),
        saved_plan_semester_id=semester_id,
    )
    if canonical_ids:
        cart_counts = (
            db.session.query(
                UserOfferingCart.offering_id,
                func.count(func.distinct(UserOfferingCart.user_id)),
            )
            .filter(
                UserOfferingCart.offering_id.in_(offering_ids),
                UserOfferingCart.user_id.in_(canonical_ids),
            )
            .group_by(UserOfferingCart.offering_id)
            .all()
        )
        for offering_id, count in cart_counts:
            output = output_by_offering.get(offering_id)
            if output is not None:
                output["cart_count"] = count

        saved_plan_counts = (
            db.session.query(
                SchedulerPlanCourse.normalized_course_code,
                func.count(func.distinct(SchedulerPlan.owner_id)),
            )
            .join(
                SchedulerPlan,
                SchedulerPlan.id == SchedulerPlanCourse.plan_id,
            )
            .filter(
                SchedulerPlanCourse.normalized_course_code.in_(output_by_course_code),
                SchedulerPlan.owner_id.in_(canonical_ids),
                SchedulerPlan.semester_id == semester_id,
                SchedulerPlan.is_deleted.is_(False),
            )
            .group_by(SchedulerPlanCourse.normalized_course_code)
            .all()
        )
        for normalized_code, count in saved_plan_counts:
            output = output_by_course_code.get(normalized_code)
            if output is not None:
                output["saved_plan_count"] = count

    return {
        "semester_id": semester_id,
        "generated_at": generated_at,
        "courses": courses,
    }


def collect_popularity_history_sample(
    *,
    semester_id: str = POPULARITY_HISTORY_SEMESTER,
    sampled_at: datetime | None = None,
    baseline: bool = False,
    lock_wait_seconds: float = 0,
    commit_deadline: datetime | None = None,
    statement_timeout_seconds: int = 180,
    expected_universe: PopularityHistoryUniverse = POPULARITY_HISTORY_EXPECTED_UNIVERSE,
    _observed_at: datetime | None = None,
) -> dict:
    """Persist one anonymous, idempotent popularity sample.

    Only positive facts are stored.  The completed run row, including its exact
    reviewed universe digest, is the authoritative record that sampling
    succeeded.  ``expected_universe`` exists for explicit isolated tests; all
    production callers use the reviewed 2610 constant.
    """
    if semester_id != POPULARITY_HISTORY_SEMESTER:
        raise ValueError(f"Popularity history is only enabled for semester {POPULARITY_HISTORY_SEMESTER}")
    if lock_wait_seconds < 0:
        raise ValueError("lock_wait_seconds must be non-negative")
    if statement_timeout_seconds <= 0:
        raise ValueError("statement_timeout_seconds must be positive")
    if commit_deadline is not None:
        commit_deadline = _as_utc(commit_deadline)

    requested_at = _as_utc(sampled_at or datetime.now(timezone.utc))
    if requested_at < POPULARITY_HISTORY_START_AT:
        raise ValueError("sample timestamp is before the popularity tracking campaign")
    if requested_at > POPULARITY_HISTORY_END_AT:
        return {
            "status": "after_cutoff",
            "semester_id": semester_id,
            "bucket_at": _iso_utc(requested_at),
            "course_facts": 0,
            "section_facts": 0,
        }
    bucket_at = (
        requested_at
        if baseline
        else
        POPULARITY_HISTORY_END_AT
        if requested_at == POPULARITY_HISTORY_END_AT
        else popularity_history_bucket(requested_at)
    )

    # A one-shot CLI owns this session.  Clear any implicit transaction opened
    # during app initialization so the sampler can define one atomic unit.
    db.session.rollback()
    try:
        with db.session.begin():
            if db.engine.dialect.name == "postgresql":
                db.session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                # PostgreSQL's SET grammar does not accept a bind parameter in
                # the value position. set_config keeps the value bound and the
                # final ``true`` scopes it to this transaction.
                db.session.execute(
                    text("SELECT set_config('statement_timeout', :timeout_value, true)"),
                    {"timeout_value": f"{statement_timeout_seconds * 1000}ms"},
                )
                lock_deadline = time.monotonic() + lock_wait_seconds
                while True:
                    locked = db.session.execute(
                        text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
                        {"lock_key": _POPULARITY_HISTORY_ADVISORY_LOCK},
                    ).scalar()
                    if locked or time.monotonic() >= lock_deadline:
                        break
                    time.sleep(min(
                        _POPULARITY_HISTORY_LOCK_RETRY_SECONDS,
                        max(0, lock_deadline - time.monotonic()),
                    ))
                if not locked:
                    return {
                        "status": "locked",
                        "semester_id": semester_id,
                        "bucket_at": _iso_utc(bucket_at),
                        "course_facts": 0,
                        "section_facts": 0,
                    }
            if commit_deadline is not None and datetime.now(timezone.utc) > commit_deadline:
                raise ValueError("sample execution passed its permitted cutoff window")

            if _observed_at is not None:
                observed_at = _as_utc(_observed_at)
            elif db.engine.dialect.name == "postgresql":
                observed_at = _as_utc(db.session.execute(
                    text("SELECT CURRENT_TIMESTAMP")
                ).scalar_one())
            else:
                observed_at = datetime.now(timezone.utc)
            universe, offerings = _popularity_history_universe(semester_id)
            _assert_popularity_history_universe(universe, expected_universe)

            existing = SchedulerPopularitySnapshotRun.query.filter_by(
                semester_id=semester_id,
                bucket_at=bucket_at,
            ).first()
            first_run = (
                SchedulerPopularitySnapshotRun.query
                .filter_by(semester_id=semester_id)
                .order_by(SchedulerPopularitySnapshotRun.bucket_at)
                .first()
            )
            if first_run is not None:
                first_universe = PopularityHistoryUniverse(
                    first_run.universe_sha256,
                    first_run.universe_course_count,
                    first_run.universe_section_count,
                    first_run.universe_meeting_count,
                )
                if first_universe != universe:
                    raise RuntimeError(
                        "popularity sampler universe changed after tracking started"
                    )
            if baseline:
                if first_run is not None:
                    return {
                        "status": "tracking_already_started",
                        "semester_id": semester_id,
                        "bucket_at": _iso_utc(first_run.bucket_at),
                        "observed_at": _iso_utc(first_run.observed_at),
                        "course_facts": len(first_run.course_snapshots),
                        "section_facts": len(first_run.section_snapshots),
                    }
            elif (
                first_run is not None
                and bucket_at < _as_utc(first_run.bucket_at)
                and popularity_history_bucket(first_run.bucket_at) == bucket_at
            ):
                return {
                    "status": "covered_by_baseline",
                    "semester_id": semester_id,
                    "bucket_at": _iso_utc(first_run.bucket_at),
                    "observed_at": _iso_utc(first_run.observed_at),
                    "course_facts": len(first_run.course_snapshots),
                    "section_facts": len(first_run.section_snapshots),
                }
            if existing is not None:
                return {
                    "status": "already_completed",
                    "semester_id": semester_id,
                    "bucket_at": _iso_utc(bucket_at),
                    "observed_at": _iso_utc(existing.observed_at),
                    "course_facts": len(existing.course_snapshots),
                    "section_facts": len(existing.section_snapshots),
                }

            offering_ids = [offering.id for offering, _ in offerings]
            canonical_ids = _canonical_contributor_ids(offering_ids)

            course_counts: dict[int, list[int]] = {}
            section_counts: dict[int, list[int]] = {}
            if canonical_ids and offering_ids:
                for offering_id, enabled, count in (
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
                ):
                    course_counts.setdefault(offering_id, [0, 0])[1 if enabled else 0] = count

                for section_id, enabled, count in (
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
                ):
                    section_counts.setdefault(section_id, [0, 0])[1 if enabled else 0] = count

            run = SchedulerPopularitySnapshotRun(
                semester_id=semester_id,
                bucket_at=bucket_at,
                observed_at=observed_at,
                universe_sha256=universe.sha256,
                universe_course_count=universe.course_count,
                universe_section_count=universe.section_count,
                universe_meeting_count=universe.meeting_count,
                completed_at=datetime.now(timezone.utc),
            )
            db.session.add(run)
            db.session.flush()

            display_by_offering: dict[int, tuple[str, str]] = {}
            for offering, course in offerings:
                normalized_code = normalize_course_code(course.code)
                if not normalized_code:
                    continue
                display_by_offering[offering.id] = (normalized_code, course.code)
                looking_count, scheduling_count = course_counts.get(offering.id, (0, 0))
                if looking_count or scheduling_count:
                    db.session.add(SchedulerPopularityCourseSnapshot(
                        run_id=run.id,
                        course_code=normalized_code,
                        display_course_code=course.code,
                        looking_count=looking_count,
                        scheduling_count=scheduling_count,
                    ))

            if display_by_offering and section_counts:
                sections = CourseSection.query.filter(
                    CourseSection.offering_id.in_(display_by_offering),
                    CourseSection.id.in_(section_counts),
                ).all()
                for section in sections:
                    normalized_code, display_code = display_by_offering[section.offering_id]
                    looking_count, scheduling_count = section_counts[section.id]
                    if looking_count or scheduling_count:
                        db.session.add(SchedulerPopularitySectionSnapshot(
                            run_id=run.id,
                            course_code=normalized_code,
                            display_course_code=display_code,
                            section_source_id=section.source_section_id,
                            looking_count=looking_count,
                            scheduling_count=scheduling_count,
                        ))

            if commit_deadline is not None and datetime.now(timezone.utc) > commit_deadline:
                raise ValueError("sample execution passed its permitted cutoff window")

            course_fact_count = sum(
                1 for counts in course_counts.values() if counts[0] or counts[1]
            )
            section_fact_count = sum(
                1 for counts in section_counts.values() if counts[0] or counts[1]
            )
        return {
            "status": "completed",
            "semester_id": semester_id,
            "bucket_at": _iso_utc(bucket_at),
            "observed_at": _iso_utc(observed_at),
            "course_facts": course_fact_count,
            "section_facts": section_fact_count,
        }
    except Exception:
        db.session.rollback()
        raise


def collect_terminal_popularity_history_sample(
    *,
    now: datetime | None = None,
    lock_wait_seconds: float = 0,
    expected_universe: PopularityHistoryUniverse = POPULARITY_HISTORY_EXPECTED_UNIVERSE,
) -> dict:
    """Attempt the cutoff once without backdating a later observation.

    The one-shot timer may begin shortly after its scheduled wall-clock slot.
    That actual time is persisted separately as ``observed_at``; it is never
    represented as if the state had been observed at the cutoff. A miss outside
    this launch window remains a visible terminal gap.
    """
    if lock_wait_seconds != 0:
        raise ValueError("terminal lock wait must be zero to prevent backdating")
    actual_now = _as_utc(now or datetime.now(timezone.utc))
    if actual_now < POPULARITY_HISTORY_END_AT:
        raise ValueError("terminal sample cannot run before the tracking cutoff")
    latest_allowed = POPULARITY_HISTORY_END_AT + timedelta(seconds=55)
    if actual_now > latest_allowed:
        raise ValueError("terminal sample execution is outside the allowed cutoff window")

    result = collect_popularity_history_sample(
        semester_id=POPULARITY_HISTORY_SEMESTER,
        sampled_at=POPULARITY_HISTORY_END_AT,
        lock_wait_seconds=0,
        # The state is observed at transaction start, but the transaction must
        # also commit inside the same narrow 23:59 wall-clock window. If it
        # crosses that boundary, roll back and expose an honest terminal gap.
        commit_deadline=latest_allowed,
        statement_timeout_seconds=30,
        expected_universe=expected_universe,
    )
    if result["status"] not in {"completed", "already_completed"}:
        return result
    if not popularity_history_terminal_sample_exists():
        raise RuntimeError("terminal popularity sample was not persisted")
    return result


def popularity_history_terminal_sample_exists() -> bool:
    return SchedulerPopularitySnapshotRun.query.filter_by(
        semester_id=POPULARITY_HISTORY_SEMESTER,
        bucket_at=POPULARITY_HISTORY_END_AT,
    ).first() is not None


def popularity_history_sampling_status(
    *,
    semester_id: str = POPULARITY_HISTORY_SEMESTER,
    now: datetime | None = None,
) -> dict:
    """Return database-backed sampler freshness metadata."""
    checked_at = _as_utc(now or datetime.now(timezone.utc))
    freshness_target = min(checked_at, POPULARITY_HISTORY_END_AT)
    latest = (
        SchedulerPopularitySnapshotRun.query
        .filter_by(semester_id=semester_id)
        .order_by(SchedulerPopularitySnapshotRun.bucket_at.desc())
        .first()
    )
    latest_at = _as_utc(latest.bucket_at) if latest is not None else None
    latest_observed_at = _as_utc(latest.observed_at) if latest is not None else None
    terminal_present = popularity_history_terminal_sample_exists()
    if latest is None:
        sampling_state = "not_started"
    elif checked_at > POPULARITY_HISTORY_END_AT:
        sampling_state = "ended_complete" if terminal_present else "ended_incomplete"
    elif latest_at is not None and (
        checked_at - latest_at
    ).total_seconds() <= POPULARITY_HISTORY_FRESH_AFTER_SECONDS:
        sampling_state = "fresh"
    else:
        sampling_state = "stale"
    return {
        "semester_id": semester_id,
        "checked_at": _iso_utc(checked_at),
        "latest_bucket_at": _iso_utc(latest_at),
        "latest_observed_at": _iso_utc(latest_observed_at),
        "age_seconds": max(0, int((freshness_target - latest_at).total_seconds())) if latest_at else None,
        "sampling_state": sampling_state,
        "terminal_present": terminal_present,
    }


def _history_effective_interval(from_at: datetime, to_at: datetime) -> int:
    raw_points = max(1, int((to_at - from_at).total_seconds() // POPULARITY_HISTORY_INTERVAL_SECONDS) + 1)
    if raw_points <= POPULARITY_HISTORY_MAX_POINTS:
        multiplier = 1
    else:
        multiplier = ceil(raw_points / POPULARITY_HISTORY_MAX_POINTS)

    # Resolution buckets are aligned to the Unix epoch, not to ``from_at``.
    # An unaligned range can therefore intersect one more bucket than a simple
    # duration calculation predicts. Increase the source-interval multiplier
    # until the aligned cardinality itself satisfies the public hard limit.
    while True:
        effective_interval = multiplier * POPULARITY_HISTORY_INTERVAL_SECONDS
        first_bucket = int(from_at.timestamp()) // effective_interval
        last_bucket = int(to_at.timestamp()) // effective_interval
        if last_bucket - first_bucket + 1 <= POPULARITY_HISTORY_MAX_POINTS:
            return effective_interval
        multiplier += 1


def _history_response_metadata(
    *,
    semester_id: str,
    requested_coverage_end: datetime,
    generated_at: datetime,
) -> dict:
    status = popularity_history_sampling_status(
        semester_id=semester_id,
        now=generated_at,
    )
    return {
        "latest_scheduled_sample_at": status["latest_bucket_at"],
        "latest_observed_sample_at": status["latest_observed_at"],
        "requested_coverage_end_at": _iso_utc(requested_coverage_end),
        "sampling_state": status["sampling_state"],
        "terminal_present": status["terminal_present"],
    }


def _history_coverage_buckets(
    *,
    from_at: datetime,
    to_at: datetime,
    effective_interval: int,
    observed_counts: dict[int, int],
    tracking_started_at: datetime | None,
) -> list[dict]:
    """Describe expected/observed source samples for each returned resolution bucket."""
    if from_at > to_at:
        return []
    first_number = int(from_at.timestamp()) // effective_interval
    last_number = int(to_at.timestamp()) // effective_interval
    coverage = []
    for bucket_number in range(first_number, last_number + 1):
        bucket_start = datetime.fromtimestamp(
            bucket_number * effective_interval,
            tz=timezone.utc,
        )
        intersection_start = max(from_at, bucket_start)
        intersection_end = min(to_at, bucket_start + timedelta(seconds=effective_interval - 1))
        first_expected = popularity_history_bucket(intersection_start)
        if first_expected < intersection_start:
            first_expected += timedelta(seconds=POPULARITY_HISTORY_INTERVAL_SECONDS)
        expected = 0
        if first_expected <= intersection_end:
            expected = int(
                (intersection_end - first_expected).total_seconds()
                // POPULARITY_HISTORY_INTERVAL_SECONDS
            ) + 1
        if (
            tracking_started_at is not None
            and tracking_started_at != POPULARITY_HISTORY_END_AT
            and tracking_started_at != popularity_history_bucket(tracking_started_at)
            and intersection_start <= tracking_started_at <= intersection_end
        ):
            # Tracking begins with one truthful deployment baseline, which may
            # fall between regular five-minute source slots. It is additional
            # to those slots and cannot compensate for one that is missing.
            expected += 1
        if POPULARITY_HISTORY_END_AT >= intersection_start and POPULARITY_HISTORY_END_AT <= intersection_end:
            # The fixed 23:59 terminal slot is additional to normal :00/:05 slots.
            if POPULARITY_HISTORY_END_AT != popularity_history_bucket(POPULARITY_HISTORY_END_AT):
                expected += 1
        observed = observed_counts.get(bucket_number, 0)
        coverage.append({
            "bucket_at": _iso_utc(bucket_start),
            "expected_samples": expected,
            "observed_samples": observed,
            "partial": observed < expected,
        })
    if len(coverage) > POPULARITY_HISTORY_MAX_POINTS:
        raise RuntimeError("history resolution exceeded the hard point limit")
    return coverage


def _history_resolution_bucket_expression(column, effective_interval: int):
    """Return a portable positive-epoch bucket expression for SQL grouping."""
    if db.engine.dialect.name == "postgresql":
        epoch_seconds = func.extract("epoch", column)
        return cast(func.floor(epoch_seconds / effective_interval), Integer)
    else:
        # Tests and local development use SQLite. All campaign timestamps are
        # positive Unix epochs, so integer truncation is equivalent to floor.
        epoch_seconds = cast(func.strftime("%s", column), Integer)
        return cast(epoch_seconds / effective_interval, Integer)


def build_popularity_history(
    *,
    viewer_id: int,
    semester_id: str,
    course_code: str,
    section_id: str | None,
    from_at: datetime,
    to_at: datetime,
    resolution: str = "auto",
) -> dict | None:
    """Return cart-scoped history, or ``None`` when the requested scope is invalid."""
    if resolution != "auto":
        raise ValueError("resolution must be auto")
    normalized_code = normalize_course_code(course_code)
    if not normalized_code:
        raise ValueError("Invalid course code")

    target = (
        db.session.query(UserOfferingCart, CourseOffering, Course)
        .join(CourseOffering, CourseOffering.id == UserOfferingCart.offering_id)
        .join(Course, Course.id == CourseOffering.course_id)
        .filter(
            UserOfferingCart.user_id == viewer_id,
            CourseOffering.semester_id == semester_id,
            CourseOffering.status == "offered",
            Course.is_deleted.is_(False),
            or_(
                Course.normalized_code == normalized_code,
                func.upper(func.replace(Course.code, " ", "")) == normalized_code,
            ),
        )
        .order_by(CourseOffering.id)
        .first()
    )
    if target is None:
        return None
    _, offering, course = target

    canonical_section_id = None
    if section_id:
        section = CourseSection.query.filter_by(
            offering_id=offering.id,
            source_section_id=section_id.strip(),
            status="active",
        ).first()
        if section is None:
            return None
        canonical_section_id = section.source_section_id

    started_at = (
        db.session.query(func.min(SchedulerPopularitySnapshotRun.bucket_at))
        .filter(SchedulerPopularitySnapshotRun.semester_id == semester_id)
        .scalar()
    )
    generated_at = datetime.now(timezone.utc)
    requested_coverage_end = min(_as_utc(to_at), POPULARITY_HISTORY_END_AT)
    from_at = _as_utc(from_at)
    to_at = requested_coverage_end
    response_metadata = _history_response_metadata(
        semester_id=semester_id,
        requested_coverage_end=requested_coverage_end,
        generated_at=generated_at,
    )
    if started_at is None:
        return {
            "semester_id": semester_id,
            "course_code": course.code,
            "section_id": canonical_section_id,
            "tracking_started_at": None,
            "tracking_ends_at": _iso_utc(POPULARITY_HISTORY_END_AT),
            "source_interval_seconds": POPULARITY_HISTORY_INTERVAL_SECONDS,
            "effective_interval_seconds": POPULARITY_HISTORY_INTERVAL_SECONDS,
            "generated_at": _iso_utc(generated_at),
            **response_metadata,
            "coverage_buckets": [],
            "points": [],
        }
    from_at = max(from_at, _as_utc(started_at))
    if from_at > to_at:
        effective_interval = POPULARITY_HISTORY_INTERVAL_SECONDS
        selected_runs = []
        observed_counts: dict[int, int] = {}
    else:
        if (to_at - from_at).total_seconds() > POPULARITY_HISTORY_MAX_RANGE_SECONDS:
            raise ValueError("history range is too large")
        effective_interval = _history_effective_interval(from_at, to_at)
        resolution_bucket = _history_resolution_bucket_expression(
            SchedulerPopularitySnapshotRun.bucket_at,
            effective_interval,
        ).label("resolution_bucket")
        source_runs = (
            db.session.query(
                SchedulerPopularitySnapshotRun.id.label("run_id"),
                SchedulerPopularitySnapshotRun.bucket_at.label("bucket_at"),
                SchedulerPopularitySnapshotRun.observed_at.label("observed_at"),
                resolution_bucket,
            )
            .filter(
                SchedulerPopularitySnapshotRun.semester_id == semester_id,
                SchedulerPopularitySnapshotRun.bucket_at >= from_at,
                SchedulerPopularitySnapshotRun.bucket_at <= to_at,
            )
            .subquery()
        )
        ranked_runs = db.session.query(
            source_runs.c.run_id,
            source_runs.c.bucket_at,
            source_runs.c.observed_at,
            source_runs.c.resolution_bucket,
            func.count().over(
                partition_by=source_runs.c.resolution_bucket,
            ).label("observed_samples"),
            func.row_number().over(
                partition_by=source_runs.c.resolution_bucket,
                order_by=(source_runs.c.bucket_at.desc(), source_runs.c.run_id.desc()),
            ).label("resolution_rank"),
        ).subquery()
        selected_runs = (
            db.session.query(
                ranked_runs.c.run_id,
                ranked_runs.c.bucket_at,
                ranked_runs.c.observed_at,
                ranked_runs.c.resolution_bucket,
                ranked_runs.c.observed_samples,
            )
            .filter(ranked_runs.c.resolution_rank == 1)
            .order_by(ranked_runs.c.resolution_bucket)
            .all()
        )
        if len(selected_runs) > POPULARITY_HISTORY_MAX_POINTS:
            raise RuntimeError("history query exceeded the hard point limit")
        observed_counts = {
            int(run.resolution_bucket): int(run.observed_samples)
            for run in selected_runs
        }

    if canonical_section_id is None:
        fact_model = SchedulerPopularityCourseSnapshot
        fact_filter = (fact_model.course_code == normalized_code,)
    else:
        fact_model = SchedulerPopularitySectionSnapshot
        fact_filter = (
            fact_model.course_code == normalized_code,
            fact_model.section_source_id == canonical_section_id,
        )
    facts = {
        fact.run_id: fact
        for fact in fact_model.query.filter(
            fact_model.run_id.in_([run.run_id for run in selected_runs]),
            *fact_filter,
        ).all()
    } if selected_runs else {}

    coverage_buckets = _history_coverage_buckets(
        from_at=from_at,
        to_at=min(to_at, generated_at),
        effective_interval=effective_interval,
        observed_counts=observed_counts,
        tracking_started_at=_as_utc(started_at),
    )

    return {
        "semester_id": semester_id,
        "course_code": course.code,
        "section_id": canonical_section_id,
        "tracking_started_at": _iso_utc(started_at),
        "tracking_ends_at": _iso_utc(POPULARITY_HISTORY_END_AT),
        "source_interval_seconds": POPULARITY_HISTORY_INTERVAL_SECONDS,
        "effective_interval_seconds": effective_interval,
        "generated_at": _iso_utc(generated_at),
        **response_metadata,
        "coverage_buckets": coverage_buckets,
        "points": [
            {
                "sampled_at": _iso_utc(run.bucket_at),
                "observed_at": _iso_utc(run.observed_at),
                "looking_count": facts[run.run_id].looking_count if run.run_id in facts else 0,
                "scheduling_count": facts[run.run_id].scheduling_count if run.run_id in facts else 0,
            }
            for run in selected_runs
        ],
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
