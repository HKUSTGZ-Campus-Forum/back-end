from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func

from app.extensions import db
from app.models.course_domain import (
    CourseMeeting,
    CourseOffering,
    CourseSection,
    UserOfferingCart,
    UserSectionSelection,
)
from app.models.scheduler_plan import SchedulerPlan, SchedulerPlanCourse, SchedulerPlanSection
from app.models.user import User
from app.services.course_domain import find_course_by_code, find_offering, normalize_course_code
from app.services.scheduler_popularity import (
    is_canonical_popularity_user,
    is_eligible_popularity_user,
    popularity_state,
    record_popularity_transition,
)
from app.services.scheduler_policy import (
    course_credit_policy,
    course_selection_policy,
    module_code_for_sections,
)


MAX_PLAN_NAME = 80
MAX_PLAN_DESCRIPTION = 500
MAX_PLAN_COURSES = 20
MAX_USER_PLANS = 100


class PlanValidationError(ValueError):
    def __init__(self, message: str, code: str = "invalid_plan"):
        super().__init__(message)
        self.code = code


class PlanConflictError(RuntimeError):
    def __init__(self, message: str, code: str = "plan_conflict"):
        super().__init__(message)
        self.code = code


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_plan_text(value: Any, *, field: str, maximum: int, required: bool = False) -> str:
    if not isinstance(value, str):
        if required:
            raise PlanValidationError(f"{field} is required", f"{field}_required")
        return ""
    normalized = " ".join(value.strip().split())
    if required and not normalized:
        raise PlanValidationError(f"{field} is required", f"{field}_required")
    if len(normalized) > maximum:
        raise PlanValidationError(
            f"{field} must be {maximum} characters or fewer",
            f"{field}_too_long",
        )
    return normalized


def validate_visibility(value: Any) -> str:
    visibility = str(value or SchedulerPlan.VISIBILITY_PRIVATE).strip().lower()
    if visibility not in SchedulerPlan.VISIBILITIES:
        raise PlanValidationError("Invalid plan visibility", "invalid_visibility")
    return visibility


def validate_banned_periods(value: Any) -> list[list[bool]]:
    if value is None:
        return [[False] * 8 for _ in range(7)]
    if not isinstance(value, list) or len(value) != 7:
        raise PlanValidationError("Blocked periods must contain seven days", "invalid_blocked_periods")
    output = []
    for day in value:
        if not isinstance(day, list) or len(day) != 8 or any(not isinstance(item, bool) for item in day):
            raise PlanValidationError(
                "Each blocked-period day must contain eight booleans",
                "invalid_blocked_periods",
            )
        output.append(list(day))
    return output


def _meeting_snapshot(meeting: CourseMeeting) -> dict:
    return {
        "day": meeting.day,
        "start_time": meeting.start_time,
        "end_time": meeting.end_time,
        "room": meeting.room,
        "instructor": meeting.instructor_text,
        "facility_id": meeting.facility_id,
        "date_ranges": meeting.date_ranges or [],
    }


def _section_snapshot(section: CourseSection) -> dict:
    meetings = (
        CourseMeeting.query.filter_by(section_id=section.id)
        .order_by(
            CourseMeeting.day,
            CourseMeeting.start_time,
            CourseMeeting.end_time,
            CourseMeeting.id,
        )
        .all()
    )
    return {
        "section_id": section.source_section_id,
        "name": section.name,
        "section_type": section.section_type,
        "is_main": section.is_main,
        "quota": section.quota,
        "enrol": section.enrol,
        "unfilled_capacity": section.avail,
        "wait": section.wait,
        "reserve_cap": section.reserve_cap or [],
        "consent_required": section.consent_required,
        "remarks": section.remarks,
        "bundle": section.bundle,
        "layer": section.layer,
        "lectures": [_meeting_snapshot(meeting) for meeting in meetings],
    }


def _section_schedule_identity(snapshot: dict) -> dict:
    """Return only fields that can change whether a saved timetable is accurate."""
    return {
        "section_id": snapshot.get("section_id"),
        "name": snapshot.get("name"),
        "section_type": snapshot.get("section_type"),
        "is_main": snapshot.get("is_main"),
        "bundle": snapshot.get("bundle"),
        "layer": snapshot.get("layer"),
        "lectures": snapshot.get("lectures") or [],
    }


def _course_snapshot(offering: CourseOffering) -> dict:
    course = offering.course
    active_sections = (
        CourseSection.query.filter_by(offering_id=offering.id, status="active")
        .order_by(CourseSection.layer, CourseSection.bundle, CourseSection.source_section_id)
        .all()
    )
    return {
        "course_code": course.code,
        "course_title": offering.title_snapshot,
        "subject": course.subject,
        **course_credit_policy(course.subject, offering.credits_snapshot),
        "pg_course": bool(course.pg_course),
        "klms_course": bool(course.klms_course),
        "selection_policy": course_selection_policy(course.code, active_sections),
    }


def _resolve_plan_courses(semester_id: Any, course_payloads: Any) -> list[dict]:
    semester = str(semester_id or "").strip()
    if not semester:
        raise PlanValidationError("Semester is required", "semester_required")
    if not isinstance(course_payloads, list) or not course_payloads:
        raise PlanValidationError("A plan must contain at least one course", "courses_required")
    if len(course_payloads) > MAX_PLAN_COURSES:
        raise PlanValidationError(
            f"A plan can contain at most {MAX_PLAN_COURSES} courses",
            "too_many_courses",
        )

    resolved = []
    seen_codes: set[str] = set()
    for display_order, payload in enumerate(course_payloads):
        if not isinstance(payload, dict):
            raise PlanValidationError("Each plan course must be an object")
        normalized_code = normalize_course_code(payload.get("course_code"))
        if not normalized_code or normalized_code in seen_codes:
            raise PlanValidationError("Plan course codes must be unique", "duplicate_course")
        seen_codes.add(normalized_code)

        course = find_course_by_code(normalized_code)
        offering = find_offering(course, semester) if course else None
        if offering is None or offering.status != "offered":
            raise PlanValidationError(
                f"{normalized_code} is not offered in {semester}",
                "offering_unavailable",
            )

        selections = payload.get("selections")
        if not isinstance(selections, list) or not selections:
            raise PlanValidationError(
                f"{normalized_code} needs at least one selected section group",
                "selections_required",
            )
        selected_sections: list[CourseSection] = []
        policy = course_selection_policy(
            normalized_code,
            CourseSection.query.filter_by(offering_id=offering.id, status="active").all(),
        )
        is_modular = policy["kind"] == "module"
        seen_layers: set[int] = set()
        seen_selection_keys: set[tuple[int, int]] = set()
        selected_module_codes: set[str] = set()
        for selection in selections:
            if not isinstance(selection, dict):
                raise PlanValidationError("Each section selection must be an object")
            bundle = selection.get("bundle_id")
            layer = selection.get("layer")
            if isinstance(bundle, bool) or not isinstance(bundle, int):
                raise PlanValidationError("Bundle id must be an integer", "invalid_bundle")
            if isinstance(layer, bool) or not isinstance(layer, int):
                raise PlanValidationError("Layer must be an integer", "invalid_layer")
            if (layer, bundle) in seen_selection_keys:
                raise PlanValidationError(
                    f"{normalized_code} contains a duplicate section group",
                    "duplicate_section_group",
                )
            seen_selection_keys.add((layer, bundle))
            if not is_modular and layer in seen_layers:
                raise PlanValidationError(
                    f"{normalized_code} can select only one bundle per layer",
                    "duplicate_layer",
                )
            seen_layers.add(layer)
            sections = (
                CourseSection.query.filter_by(
                    offering_id=offering.id,
                    bundle=bundle,
                    layer=layer,
                    status="active",
                )
                .order_by(CourseSection.source_section_id)
                .all()
            )
            if not sections:
                raise PlanValidationError(
                    f"Selected section group for {normalized_code} is unavailable",
                    "section_group_unavailable",
                )
            if is_modular:
                module_code = module_code_for_sections(sections)
                if module_code is None:
                    raise PlanValidationError(
                        f"Selected section group for {normalized_code} is not a valid module",
                        "invalid_module_group",
                    )
                if module_code in selected_module_codes:
                    raise PlanValidationError(
                        f"{normalized_code} can select only one section for {module_code}",
                        "duplicate_module",
                    )
                selected_module_codes.add(module_code)
            selected_sections.extend(sections)

        if is_modular:
            for group in policy["groups"]:
                selected_count = len(selected_module_codes.intersection(group["module_codes"]))
                if not group["min_select"] <= selected_count <= group["max_select"]:
                    raise PlanValidationError(
                        f"{normalized_code} has an invalid {group['id']} module selection",
                        "invalid_module_selection",
                    )

        resolved.append({
            "offering": offering,
            "normalized_course_code": normalized_code,
            "display_order": display_order,
            "snapshot": _course_snapshot(offering),
            "sections": selected_sections,
        })
    return resolved


def _set_plan_content(plan: SchedulerPlan, resolved_courses: list[dict]) -> None:
    plan.courses.clear()
    db.session.flush()
    for resolved in resolved_courses:
        plan_course = SchedulerPlanCourse(
            offering_id=resolved["offering"].id,
            normalized_course_code=resolved["normalized_course_code"],
            display_order=resolved["display_order"],
            snapshot=resolved["snapshot"],
        )
        for section in resolved["sections"]:
            plan_course.sections.append(SchedulerPlanSection(
                section_id=section.id,
                source_section_id=section.source_section_id,
                bundle=section.bundle,
                layer=section.layer,
                snapshot=_section_snapshot(section),
            ))
        plan.courses.append(plan_course)


def create_plan(owner_id: int, payload: dict) -> SchedulerPlan:
    if SchedulerPlan.query.filter_by(owner_id=owner_id, is_deleted=False).count() >= MAX_USER_PLANS:
        raise PlanValidationError(
            f"You can save up to {MAX_USER_PLANS} plans",
            "plan_limit_reached",
        )
    name = normalize_plan_text(payload.get("name"), field="name", maximum=MAX_PLAN_NAME, required=True)
    description = normalize_plan_text(
        payload.get("description", ""),
        field="description",
        maximum=MAX_PLAN_DESCRIPTION,
    )
    visibility = validate_visibility(payload.get("visibility"))
    semester_id = str(payload.get("semester_id") or "").strip()
    resolved = _resolve_plan_courses(semester_id, payload.get("courses"))
    constraints = {"banned_periods": validate_banned_periods(payload.get("banned_periods"))}
    plan = SchedulerPlan(
        owner_id=owner_id,
        semester_id=semester_id,
        name=name,
        description=description,
        visibility=visibility,
        private_constraints=constraints,
        published_at=utcnow() if visibility == SchedulerPlan.VISIBILITY_PUBLIC else None,
    )
    _set_plan_content(plan, resolved)
    db.session.add(plan)
    db.session.commit()
    return plan


def update_plan(plan: SchedulerPlan, payload: dict) -> SchedulerPlan:
    expected_version = payload.get("version")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise PlanValidationError("Plan version is required", "version_required")
    if expected_version != plan.content_version:
        raise PlanConflictError("This plan was updated in another tab", "version_conflict")

    if "name" in payload:
        plan.name = normalize_plan_text(
            payload.get("name"), field="name", maximum=MAX_PLAN_NAME, required=True
        )
    if "description" in payload:
        plan.description = normalize_plan_text(
            payload.get("description"),
            field="description",
            maximum=MAX_PLAN_DESCRIPTION,
        )
    if "visibility" in payload:
        old_visibility = plan.visibility
        plan.visibility = validate_visibility(payload.get("visibility"))
        if plan.visibility == SchedulerPlan.VISIBILITY_PUBLIC and old_visibility != plan.visibility:
            plan.published_at = utcnow()
        elif plan.visibility != SchedulerPlan.VISIBILITY_PUBLIC:
            plan.published_at = None
    if "courses" in payload:
        semester_id = str(payload.get("semester_id") or plan.semester_id).strip()
        if semester_id != plan.semester_id:
            raise PlanValidationError("A saved plan cannot change semesters", "semester_immutable")
        resolved = _resolve_plan_courses(plan.semester_id, payload.get("courses"))
        _set_plan_content(plan, resolved)
    if "banned_periods" in payload:
        plan.private_constraints = {
            **(plan.private_constraints or {}),
            "banned_periods": validate_banned_periods(payload.get("banned_periods")),
        }
    plan.content_version += 1
    db.session.commit()
    return plan


def clone_plan(source: SchedulerPlan, owner_id: int, name: str | None = None) -> SchedulerPlan:
    if SchedulerPlan.query.filter_by(owner_id=owner_id, is_deleted=False).count() >= MAX_USER_PLANS:
        raise PlanValidationError(
            f"You can save up to {MAX_USER_PLANS} plans",
            "plan_limit_reached",
        )
    copy_name = normalize_plan_text(
        name if name is not None else source.name,
        field="name",
        maximum=MAX_PLAN_NAME,
        required=True,
    )
    clone = SchedulerPlan(
        owner_id=owner_id,
        semester_id=source.semester_id,
        name=copy_name,
        description=source.description,
        visibility=SchedulerPlan.VISIBILITY_PRIVATE,
        private_constraints={"banned_periods": [[False] * 8 for _ in range(7)]},
        source_plan_id=source.id,
    )
    for source_course in source.courses:
        clone_course = SchedulerPlanCourse(
            offering_id=source_course.offering_id,
            normalized_course_code=source_course.normalized_course_code,
            display_order=source_course.display_order,
            snapshot=dict(source_course.snapshot or {}),
        )
        for source_section in source_course.sections:
            clone_course.sections.append(SchedulerPlanSection(
                section_id=source_section.section_id,
                source_section_id=source_section.source_section_id,
                bundle=source_section.bundle,
                layer=source_section.layer,
                snapshot=dict(source_section.snapshot or {}),
            ))
        clone.courses.append(clone_course)
    db.session.add(clone)
    db.session.commit()
    return clone


def _section_availability(saved: SchedulerPlanSection) -> str:
    current = (
        CourseSection.query.populate_existing().filter_by(id=saved.section_id).first()
        if saved.section_id is not None
        else None
    )
    if (
        current is None
        or current.status != "active"
        or saved.plan_course.offering_id is None
        or current.offering_id != saved.plan_course.offering_id
    ):
        return "unavailable"
    current_snapshot = _section_snapshot(current)
    return (
        "current"
        if _section_schedule_identity(current_snapshot)
        == _section_schedule_identity(saved.snapshot or {})
        else "updated"
    )


def _plan_timetable(plan: SchedulerPlan) -> tuple[list[dict], list[dict], str]:
    courses: list[dict] = []
    selections: list[dict] = []
    availability = "current"
    for course_index, plan_course in enumerate(plan.courses):
        snapshot = dict(plan_course.snapshot or {})
        credit_fields = course_credit_policy(
            snapshot.get("subject"),
            snapshot.get("credit", 0),
        )
        layers: dict[int, list[dict]] = defaultdict(list)
        grouped_sections: dict[tuple[int, int], list[dict]] = defaultdict(list)
        for saved_section in plan_course.sections:
            state = _section_availability(saved_section)
            if state == "unavailable":
                availability = "unavailable"
            elif state == "updated" and availability == "current":
                availability = "updated"
            section_payload = dict(saved_section.snapshot or {})
            section_payload["availability"] = state
            grouped_sections[(saved_section.layer, saved_section.bundle)].append(section_payload)
        for (layer, bundle), sections in sorted(grouped_sections.items()):
            layers[layer].append({
                "id": bundle,
                "layer": layer,
                "enabled": True,
                "sections": sections,
            })
            selections.append({
                "courseIndex": course_index,
                "bundleId": bundle,
                "layer": layer,
            })
        courses.append({
            "course_code": snapshot.get("course_code", plan_course.normalized_course_code),
            "course_title": snapshot.get("course_title", plan_course.normalized_course_code),
            **credit_fields,
            "subject": snapshot.get("subject"),
            "pg_course": bool(snapshot.get("pg_course")),
            "klms_course": bool(snapshot.get("klms_course")),
            "enabled": True,
            "selection_policy": snapshot.get("selection_policy") or {
                "kind": "layer",
                "groups": [],
                "modules": [],
            },
            "layers": dict(layers),
        })
    return courses, selections, availability


def serialize_plan(
    plan: SchedulerPlan,
    *,
    viewer_id: int | None,
    include_content: bool = True,
) -> dict:
    is_owner = viewer_id == plan.owner_id
    payload = {
        "public_id": plan.public_id,
        "semester_id": plan.semester_id,
        "name": plan.name,
        "description": plan.description,
        "visibility": plan.visibility,
        "version": plan.content_version,
        "availability": "current",
        "course_codes": [course.normalized_course_code for course in plan.courses],
        "course_count": len(plan.courses),
        "total_credits": sum(
            int(
                course_credit_policy(
                    (course.snapshot or {}).get("subject"),
                    (course.snapshot or {}).get("credit") or 0,
                )["term_load_credit"]
                or 0
            )
            for course in plan.courses
        ),
        "author": {
            "id": plan.owner.id,
            "username": plan.owner.username,
            "avatar_url": plan.owner.avatar_url,
        },
        "is_owner": is_owner,
        "can_copy": viewer_id is not None and viewer_id != plan.owner_id,
        "created_at": plan.created_at.isoformat(),
        "updated_at": plan.updated_at.isoformat(),
        "published_at": plan.published_at.isoformat() if plan.published_at else None,
    }
    if include_content:
        courses, selections, availability = _plan_timetable(plan)
        payload.update({
            "availability": availability,
            "courses": courses,
            "selections": selections,
        })
    if is_owner:
        payload["banned_periods"] = (plan.private_constraints or {}).get(
            "banned_periods", [[False] * 8 for _ in range(7)]
        )
    return payload


def can_view_plan(plan: SchedulerPlan | None, viewer_id: int | None) -> bool:
    if plan is None or plan.is_deleted or plan.owner.is_deleted:
        return False
    if viewer_id == plan.owner_id:
        return True
    return plan.visibility in {
        SchedulerPlan.VISIBILITY_UNLISTED,
        SchedulerPlan.VISIBILITY_PUBLIC,
    }


def _meeting_date_ranges(meeting: CourseMeeting) -> list[tuple[date, date]] | None:
    """Return validated inclusive ranges, or None when timing is not safely bounded."""
    raw_ranges = meeting.date_ranges
    if not isinstance(raw_ranges, list) or not raw_ranges:
        return None

    ranges: list[tuple[date, date]] = []
    for raw_range in raw_ranges:
        if not isinstance(raw_range, dict):
            return None
        try:
            start = date.fromisoformat(str(raw_range.get("start_date") or ""))
            end = date.fromisoformat(str(raw_range.get("end_date") or ""))
        except ValueError:
            return None
        if start > end:
            return None
        ranges.append((start, end))
    return ranges


def _meetings_overlap(left: CourseMeeting, right: CourseMeeting) -> bool:
    if (
        left.day != right.day
        or left.start_time >= right.end_time
        or left.end_time <= right.start_time
    ):
        return False

    left_ranges = _meeting_date_ranges(left)
    right_ranges = _meeting_date_ranges(right)
    if left_ranges is None or right_ranges is None:
        # Older and hand-authored data has no reliable date bounds. Preserve the
        # conservative weekly-conflict behavior for those records.
        return True
    return any(
        left_start <= right_end and right_start <= left_end
        for left_start, left_end in left_ranges
        for right_start, right_end in right_ranges
    )


def _validate_apply_plan(
    plan: SchedulerPlan,
    *,
    include_private_constraints: bool,
) -> dict[int, set[int]]:
    selected_by_offering: dict[int, set[int]] = {}
    meeting_slots: list[tuple[int, CourseMeeting, str]] = []
    banned = (
        (plan.private_constraints or {}).get("banned_periods")
        if include_private_constraints
        else None
    ) or [[False] * 8 for _ in range(7)]
    time_slots = [
        (900, 1030), (1030, 1200), (1200, 1330), (1330, 1500),
        (1500, 1630), (1630, 1800), (1800, 1930), (1930, 2100),
    ]
    for plan_course in plan.courses:
        offering = plan_course.offering
        if offering is None or offering.status != "offered" or offering.semester_id != plan.semester_id:
            raise PlanConflictError("A course in this plan is no longer offered", "plan_unavailable")
        section_ids: set[int] = set()
        for saved in plan_course.sections:
            section = (
                CourseSection.query.populate_existing().filter_by(id=saved.section_id).first()
                if saved.section_id is not None
                else None
            )
            if section is None or section.status != "active" or section.offering_id != offering.id:
                raise PlanConflictError("A section in this plan is no longer available", "plan_unavailable")
            section_ids.add(section.id)
            for meeting in section.meetings.order_by(CourseMeeting.id).all():
                for prior_section_id, prior_meeting, label in meeting_slots:
                    # Multiple rows can describe one atomic section across room or
                    # teaching-date changes. They are not alternative classes.
                    if prior_section_id != section.id and _meetings_overlap(meeting, prior_meeting):
                        raise PlanConflictError(
                            f"Updated section times now conflict: {label}",
                            "updated_plan_conflict",
                        )
                for index, (slot_start, slot_end) in enumerate(time_slots):
                    if (
                        banned[meeting.day - 1][index]
                        and meeting.start_time < slot_end
                        and meeting.end_time > slot_start
                    ):
                        raise PlanConflictError(
                            "Updated section times overlap a blocked period",
                            "updated_plan_conflict",
                        )
                meeting_slots.append((section.id, meeting, section.source_section_id))
        selected_by_offering[offering.id] = section_ids
    return selected_by_offering


def apply_plan_to_workspace(plan: SchedulerPlan, user_id: int) -> None:
    selected_by_offering = _validate_apply_plan(
        plan,
        include_private_constraints=plan.owner_id == user_id,
    )
    old_carts = (
        UserOfferingCart.query.join(CourseOffering)
        .filter(
            UserOfferingCart.user_id == user_id,
            CourseOffering.semester_id == plan.semester_id,
        )
        .with_for_update()
        .all()
    )
    old_cart_by_offering = {cart.offering_id: cart for cart in old_carts}
    old_offering_ids = list(old_cart_by_offering)
    old_selections = (
        UserSectionSelection.query.filter(
            UserSectionSelection.user_id == user_id,
            UserSectionSelection.offering_id.in_(old_offering_ids),
            UserSectionSelection.enabled.is_(True),
        ).with_for_update().all()
        if old_offering_ids else []
    )
    old_selected = defaultdict(set)
    for selection in old_selections:
        old_selected[selection.offering_id].add(selection.section_id)

    user = db.session.get(User, user_id)
    eligible = is_eligible_popularity_user(user) and is_canonical_popularity_user(user)
    all_offering_ids = set(old_offering_ids) | set(selected_by_offering)
    sections_by_id = {
        section.id: section
        for section in CourseSection.query.filter(
            CourseSection.id.in_(set().union(*old_selected.values(), *selected_by_offering.values()))
        ).all()
    } if old_selected or selected_by_offering else {}

    for offering_id in all_offering_ids:
        old_course_state = (
            popularity_state(old_cart_by_offering[offering_id].enabled)
            if offering_id in old_cart_by_offering else None
        )
        new_course_state = "scheduling" if offering_id in selected_by_offering else None
        reason = "cart_added" if old_course_state is None else "cart_removed" if new_course_state is None else "course_toggled"
        record_popularity_transition(
            contributor_is_eligible=eligible,
            offering_id=offering_id,
            from_state=old_course_state,
            to_state=new_course_state,
            reason=reason,
        )
        for section_id in old_selected[offering_id] | selected_by_offering.get(offering_id, set()):
            old_state = old_course_state if section_id in old_selected[offering_id] else None
            new_state = new_course_state if section_id in selected_by_offering.get(offering_id, set()) else None
            record_popularity_transition(
                contributor_is_eligible=eligible,
                offering_id=offering_id,
                section=sections_by_id.get(section_id),
                from_state=old_state,
                to_state=new_state,
                reason=reason,
            )

    if old_offering_ids:
        UserSectionSelection.query.filter(
            UserSectionSelection.user_id == user_id,
            UserSectionSelection.offering_id.in_(old_offering_ids),
        ).delete(synchronize_session=False)
        UserOfferingCart.query.filter(
            UserOfferingCart.user_id == user_id,
            UserOfferingCart.offering_id.in_(old_offering_ids),
        ).delete(synchronize_session=False)
        db.session.flush()

    for offering_id, selected_section_ids in selected_by_offering.items():
        db.session.add(UserOfferingCart(user_id=user_id, offering_id=offering_id, enabled=True))
        for section in CourseSection.query.filter_by(offering_id=offering_id, status="active").all():
            db.session.add(UserSectionSelection(
                user_id=user_id,
                offering_id=offering_id,
                section_id=section.id,
                enabled=section.id in selected_section_ids,
                source="cart",
            ))
    db.session.commit()


def clear_workspace(semester_id: str, user_id: int) -> int:
    carts = (
        UserOfferingCart.query.join(CourseOffering)
        .filter(
            UserOfferingCart.user_id == user_id,
            CourseOffering.semester_id == semester_id,
        )
        .with_for_update()
        .all()
    )
    if not carts:
        return 0

    offering_ids = [cart.offering_id for cart in carts]
    selections = (
        UserSectionSelection.query.join(
            CourseSection,
            CourseSection.id == UserSectionSelection.section_id,
        )
        .filter(
            UserSectionSelection.user_id == user_id,
            UserSectionSelection.offering_id.in_(offering_ids),
            UserSectionSelection.enabled.is_(True),
        )
        .with_for_update()
        .all()
    )
    selected_by_offering: dict[int, list[UserSectionSelection]] = defaultdict(list)
    for selection in selections:
        selected_by_offering[selection.offering_id].append(selection)

    user = db.session.get(User, user_id)
    eligible = is_eligible_popularity_user(user) and is_canonical_popularity_user(user)
    for cart in carts:
        state = popularity_state(cart.enabled)
        record_popularity_transition(
            contributor_is_eligible=eligible,
            offering_id=cart.offering_id,
            from_state=state,
            to_state=None,
            reason="cart_removed",
        )
        for selection in selected_by_offering[cart.offering_id]:
            record_popularity_transition(
                contributor_is_eligible=eligible,
                offering_id=cart.offering_id,
                section=selection.section,
                from_state=state,
                to_state=None,
                reason="cart_removed",
            )

    UserSectionSelection.query.filter(
        UserSectionSelection.user_id == user_id,
        UserSectionSelection.offering_id.in_(offering_ids),
    ).delete(synchronize_session=False)
    UserOfferingCart.query.filter(
        UserOfferingCart.user_id == user_id,
        UserOfferingCart.offering_id.in_(offering_ids),
    ).delete(synchronize_session=False)
    db.session.commit()
    return len(carts)


def public_plan_query(*, semester_id: str = "", course_code: str = ""):
    query = SchedulerPlan.query.filter_by(
        visibility=SchedulerPlan.VISIBILITY_PUBLIC,
        is_deleted=False,
    ).join(User, User.id == SchedulerPlan.owner_id).filter(User.is_deleted.is_(False))
    if semester_id:
        query = query.filter(SchedulerPlan.semester_id == semester_id)
    normalized = normalize_course_code(course_code)
    if normalized:
        query = query.join(SchedulerPlanCourse).filter(
            SchedulerPlanCourse.normalized_course_code == normalized
        )
    return query.order_by(func.coalesce(SchedulerPlan.published_at, SchedulerPlan.updated_at).desc())
