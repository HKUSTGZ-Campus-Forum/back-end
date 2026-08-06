from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.extensions import db
from app.models.course_domain import CourseMeeting, CourseOffering, CourseSection


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
        section.is_main = source.is_main
        db.session.flush()

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
