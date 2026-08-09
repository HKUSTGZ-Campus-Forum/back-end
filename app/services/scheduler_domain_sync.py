from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.extensions import db
from app.models.course_domain import (
    CourseMeeting,
    CourseOffering,
    CourseSection,
    UserOfferingCart,
    UserSectionSelection,
)


def _lectures_for_section(source_section: Any) -> list[Any]:
    lectures = source_section.lectures
    if hasattr(lectures, "all"):
        return lectures.all()
    return list(lectures)


def sync_offering_sections(
    offering: CourseOffering,
    source_sections: Iterable[Any],
) -> dict[str, int]:
    """Synchronize an offering without replacing stable CourseSection rows.

    UserSectionSelection points at CourseSection.id. Updating matching sections
    in place preserves those choices across timetable imports, while sections
    that genuinely disappeared are removed normally.
    """
    incoming = list(source_sections)
    existing = {
        section.source_section_id: section
        for section in CourseSection.query.filter_by(offering_id=offering.id).all()
    }
    incoming_ids = {section.section_id for section in incoming}
    created = 0
    updated = 0
    cart_user_ids = [
        user_id
        for (user_id,) in (
            db.session.query(UserOfferingCart.user_id)
            .filter_by(offering_id=offering.id)
            .all()
        )
    ]
    incoming_groups = {
        section.section_id: (section.bundle, section.layer)
        for section in incoming
    }
    inherited_by_group_user = {}
    existing_selection_keys = set()
    if cart_user_ids:
        existing_selection_rows = (
            db.session.query(
                UserSectionSelection.user_id,
                CourseSection.id,
                CourseSection.source_section_id,
                CourseSection.bundle,
                CourseSection.layer,
                UserSectionSelection.enabled,
            )
            .join(CourseSection, CourseSection.id == UserSectionSelection.section_id)
            .filter(
                UserSectionSelection.offering_id == offering.id,
                UserSectionSelection.user_id.in_(cart_user_ids),
                CourseSection.offering_id == offering.id,
            )
            .order_by(CourseSection.source_section_id)
            .all()
        )
        for user_id, section_id, source_section_id, bundle, layer, enabled in existing_selection_rows:
            existing_selection_keys.add((user_id, section_id))
            target_group = incoming_groups.get(source_section_id, (bundle, layer))
            inherited_by_group_user.setdefault((*target_group, user_id), enabled)

    for source in incoming:
        section = existing.get(source.section_id)
        if section is None:
            section = CourseSection(
                offering_id=offering.id,
                source_section_id=source.section_id,
            )
            db.session.add(section)
            created += 1
        else:
            updated += 1

        section.name = source.name
        section.section_type = source.section_type
        section.bundle = source.bundle
        section.layer = source.layer
        section.quota = source.quota
        section.enrol = getattr(source, "enrol", None)
        section.avail = getattr(source, "avail", None)
        section.wait = getattr(source, "wait", None)
        section.is_main = source.is_main
        db.session.flush()

        if cart_user_ids:
            for user_id in cart_user_ids:
                selection_key = (user_id, section.id)
                if selection_key in existing_selection_keys:
                    continue
                db.session.add(UserSectionSelection(
                    user_id=user_id,
                    offering_id=offering.id,
                    section_id=section.id,
                    enabled=inherited_by_group_user.get(
                        (section.bundle, section.layer, user_id),
                        True,
                    ),
                    source="cart",
                ))
                existing_selection_keys.add(selection_key)

        CourseMeeting.query.filter_by(section_id=section.id).delete(
            synchronize_session=False
        )
        for lecture in _lectures_for_section(source):
            db.session.add(CourseMeeting(
                section_id=section.id,
                day=lecture.day,
                start_time=lecture.start_time,
                end_time=lecture.end_time,
                room=lecture.room,
                instructor_text=lecture.instructor,
            ))

    removed = 0
    for source_section_id, section in existing.items():
        if source_section_id in incoming_ids:
            continue
        db.session.delete(section)
        removed += 1

    return {"created": created, "updated": updated, "removed": removed}
