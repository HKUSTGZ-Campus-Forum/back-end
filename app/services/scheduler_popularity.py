from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
import time
from typing import Iterable

from sqlalchemy import and_, func, or_, text

from app.extensions import db
from app.models.course import Course
from app.models.course_domain import (
    CourseOffering,
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
POPULARITY_HISTORY_END_AT = datetime(2026, 9, 30, 15, 59, tzinfo=timezone.utc)
POPULARITY_HISTORY_MAX_POINTS = 1000
POPULARITY_HISTORY_MAX_RANGE_SECONDS = 62 * 24 * 60 * 60
_POPULARITY_HISTORY_ADVISORY_LOCK = 261030005
_POPULARITY_HISTORY_LOCK_RETRY_SECONDS = 1.0


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


def collect_popularity_history_sample(
    *,
    semester_id: str = POPULARITY_HISTORY_SEMESTER,
    sampled_at: datetime | None = None,
    baseline: bool = False,
    lock_wait_seconds: float = 0,
    commit_deadline: datetime | None = None,
    statement_timeout_seconds: int = 180,
) -> dict:
    """Persist one anonymous, idempotent popularity sample.

    Only positive facts are stored.  The completed run row is the authoritative
    record that sampling succeeded, so its absence means a gap while an absent
    fact in an existing run means zero.
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
            if baseline:
                if first_run is not None:
                    return {
                        "status": "tracking_already_started",
                        "semester_id": semester_id,
                        "bucket_at": _iso_utc(first_run.bucket_at),
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
                    "course_facts": len(first_run.course_snapshots),
                    "section_facts": len(first_run.section_snapshots),
                }
            if existing is not None:
                return {
                    "status": "already_completed",
                    "semester_id": semester_id,
                    "bucket_at": _iso_utc(bucket_at),
                    "course_facts": len(existing.course_snapshots),
                    "section_facts": len(existing.section_snapshots),
                }

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
            "course_facts": course_fact_count,
            "section_facts": section_fact_count,
        }
    except Exception:
        db.session.rollback()
        raise


def collect_terminal_popularity_history_sample(
    *,
    now: datetime | None = None,
    tolerance_seconds: int = 120,
    lock_wait_seconds: float = 110,
) -> dict:
    """Capture the exact cutoff bucket only during its narrow execution window."""
    if tolerance_seconds < 0:
        raise ValueError("terminal tolerance must be non-negative")
    actual_now = _as_utc(now or datetime.now(timezone.utc))
    if actual_now < POPULARITY_HISTORY_END_AT:
        raise ValueError("terminal sample cannot run before the tracking cutoff")
    latest_allowed = POPULARITY_HISTORY_END_AT + timedelta(seconds=tolerance_seconds)
    if actual_now > latest_allowed:
        raise ValueError("terminal sample execution is outside the allowed cutoff window")

    result = collect_popularity_history_sample(
        semester_id=POPULARITY_HISTORY_SEMESTER,
        sampled_at=POPULARITY_HISTORY_END_AT,
        lock_wait_seconds=lock_wait_seconds,
        commit_deadline=latest_allowed,
        statement_timeout_seconds=30,
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
    return {
        "semester_id": semester_id,
        "checked_at": _iso_utc(checked_at),
        "latest_bucket_at": _iso_utc(latest_at),
        "age_seconds": max(0, int((freshness_target - latest_at).total_seconds())) if latest_at else None,
    }


def _history_effective_interval(from_at: datetime, to_at: datetime) -> int:
    raw_points = max(1, int((to_at - from_at).total_seconds() // POPULARITY_HISTORY_INTERVAL_SECONDS) + 1)
    if raw_points <= POPULARITY_HISTORY_MAX_POINTS:
        return POPULARITY_HISTORY_INTERVAL_SECONDS
    multiplier = ceil(raw_points / POPULARITY_HISTORY_MAX_POINTS)
    return multiplier * POPULARITY_HISTORY_INTERVAL_SECONDS


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
        ).first()
        if section is None:
            return None
        canonical_section_id = section.source_section_id

    started_at = (
        db.session.query(func.min(SchedulerPopularitySnapshotRun.bucket_at))
        .filter(SchedulerPopularitySnapshotRun.semester_id == semester_id)
        .scalar()
    )
    from_at = _as_utc(from_at)
    to_at = min(_as_utc(to_at), POPULARITY_HISTORY_END_AT)
    if started_at is None:
        return {
            "semester_id": semester_id,
            "course_code": course.code,
            "section_id": canonical_section_id,
            "tracking_started_at": None,
            "tracking_ends_at": _iso_utc(POPULARITY_HISTORY_END_AT),
            "source_interval_seconds": POPULARITY_HISTORY_INTERVAL_SECONDS,
            "effective_interval_seconds": POPULARITY_HISTORY_INTERVAL_SECONDS,
            "generated_at": _iso_utc(datetime.now(timezone.utc)),
            "points": [],
        }
    from_at = max(from_at, _as_utc(started_at))
    if from_at > to_at:
        effective_interval = POPULARITY_HISTORY_INTERVAL_SECONDS
        runs = []
    else:
        if (to_at - from_at).total_seconds() > POPULARITY_HISTORY_MAX_RANGE_SECONDS:
            raise ValueError("history range is too large")
        effective_interval = _history_effective_interval(from_at, to_at)
        runs = (
            SchedulerPopularitySnapshotRun.query
            .filter(
                SchedulerPopularitySnapshotRun.semester_id == semester_id,
                SchedulerPopularitySnapshotRun.bucket_at >= from_at,
                SchedulerPopularitySnapshotRun.bucket_at <= to_at,
            )
            .order_by(SchedulerPopularitySnapshotRun.bucket_at)
            .all()
        )

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
            fact_model.run_id.in_([run.id for run in runs]),
            *fact_filter,
        ).all()
    } if runs else {}

    # Resolution auto uses last-value sampling in deterministic wall-clock
    # buckets. Completed source runs remain the only source of points; empty
    # buckets are omitted so clients can display gaps.
    selected_runs = []
    by_resolution_bucket: dict[int, SchedulerPopularitySnapshotRun] = {}
    for run in runs:
        timestamp = _as_utc(run.bucket_at)
        bucket_number = int(timestamp.timestamp()) // effective_interval
        by_resolution_bucket[bucket_number] = run
    selected_runs.extend(by_resolution_bucket[key] for key in sorted(by_resolution_bucket))
    if len(selected_runs) > POPULARITY_HISTORY_MAX_POINTS:
        # Epoch-aligned buckets can intersect one more bucket than a duration
        # estimate for unaligned endpoints. Preserve the newest bounded window.
        selected_runs = selected_runs[-POPULARITY_HISTORY_MAX_POINTS:]

    return {
        "semester_id": semester_id,
        "course_code": course.code,
        "section_id": canonical_section_id,
        "tracking_started_at": _iso_utc(started_at),
        "tracking_ends_at": _iso_utc(POPULARITY_HISTORY_END_AT),
        "source_interval_seconds": POPULARITY_HISTORY_INTERVAL_SECONDS,
        "effective_interval_seconds": effective_interval,
        "generated_at": _iso_utc(datetime.now(timezone.utc)),
        "points": [
            {
                "sampled_at": _iso_utc(run.bucket_at),
                "looking_count": facts[run.id].looking_count if run.id in facts else 0,
                "scheduling_count": facts[run.id].scheduling_count if run.id in facts else 0,
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
