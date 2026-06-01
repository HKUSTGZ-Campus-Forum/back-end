"""
Import public scheduler data from CoursePlan.search into UniKorn.

Usage:
    python -m app.scripts.migrate_scheduler_data --source "$COURSEPLAN_READONLY_DATABASE_URL"

The source database is read-only. Destination scheduler tables are replaced as
one validated snapshot transaction, while scheduler-related course fields are
upserted by course code.
"""
import argparse
import json
from dataclasses import dataclass

from sqlalchemy import create_engine, text

from app import create_app
from app.extensions import db
from app.models.course import Course
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_map import SchedulerMapComponent, SchedulerMapLine
from app.models.scheduler_section import SchedulerSection


class SnapshotValidationError(RuntimeError):
    pass


@dataclass
class Snapshot:
    courses: list
    sections: list
    lectures: list
    components: list
    lines: list


def load_snapshot(source_conn):
    return Snapshot(
        courses=source_conn.execute(text(
            'SELECT course_code, course_title, course_title_abbr, course_desc, '
            'pre_requirement, co_requirement, exclusion, credit, subject, '
            'catalog_number, pg_course, klms_course, vector FROM course'
        )).fetchall(),
        sections=source_conn.execute(text(
            'SELECT semester_id, section_id, course_code, name, bundle, layer, '
            'quota, section_type, is_main FROM section'
        )).fetchall(),
        lectures=source_conn.execute(text(
            'SELECT semester_id, section_id, day, start_time, end_time, room, '
            'instructor FROM lecture'
        )).fetchall(),
        components=source_conn.execute(text(
            'SELECT id, node_type, x_coordinate, y_coordinate, category '
            'FROM map_component'
        )).fetchall(),
        lines=source_conn.execute(text(
            'SELECT start_id, end_id, line_type, x_coordinate, category FROM map_line'
        )).fetchall(),
    )


def validate_snapshot(snapshot):
    course_codes = {row[0] for row in snapshot.courses}
    section_keys = {(row[0], row[1]) for row in snapshot.sections}
    component_ids = {row[0] for row in snapshot.components}

    if not snapshot.sections:
        raise SnapshotValidationError('snapshot contains no sections')

    for row in snapshot.sections:
        if row[2] not in course_codes:
            raise SnapshotValidationError(
                f'section {row[1]} references missing course {row[2]}'
            )

    for row in snapshot.lectures:
        if (row[0], row[1]) not in section_keys:
            raise SnapshotValidationError(
                f'lecture references missing section {row[0]}/{row[1]}'
            )

    for row in snapshot.lines:
        if row[0] not in component_ids or row[1] not in component_ids:
            raise SnapshotValidationError(
                f'map line references missing component {row[0]}->{row[1]}'
            )


def validate_destination(snapshot, summary):
    expected = {
        'sections': len(snapshot.sections),
        'lectures': len(snapshot.lectures),
        'map_components': len(snapshot.components),
        'map_lines': len(snapshot.lines),
    }
    actual = {key: summary[key] for key in expected}
    if actual != expected:
        raise SnapshotValidationError(
            f'destination counts do not match snapshot: {actual} != {expected}'
        )


def _normalized_subject(subject):
    return subject.strip().upper() if isinstance(subject, str) else subject


def import_snapshot(source_conn):
    snapshot = load_snapshot(source_conn)
    validate_snapshot(snapshot)

    try:
        SchedulerMapLine.query.delete()
        SchedulerMapComponent.query.delete()
        SchedulerLecture.query.delete()
        SchedulerSection.query.delete()

        for row in snapshot.courses:
            course = Course.query.filter_by(code=row[0]).first()
            if course is None:
                course = Course(code=row[0], name=row[1], credits=row[7])
            course.name = row[1]
            course.course_title_abbr = row[2]
            course.description = row[3] or ''
            course.pre_requirement = row[4]
            course.co_requirement = row[5]
            course.exclusion = row[6]
            course.credits = row[7]
            course.subject = _normalized_subject(row[8])
            course.catalog_number = row[9]
            course.pg_course = row[10]
            course.klms_course = row[11]
            course.vector = row[12]
            db.session.add(course)
        db.session.flush()

        courses = {course.code: course for course in Course.query.all()}
        for row in snapshot.sections:
            db.session.add(SchedulerSection(
                semester_id=row[0],
                section_id=row[1],
                course_id=courses[row[2]].id,
                name=row[3],
                bundle=row[4],
                layer=row[5],
                quota=row[6],
                section_type=row[7],
                is_main=row[8],
            ))
        db.session.flush()

        for row in snapshot.lectures:
            db.session.add(SchedulerLecture(
                semester_id=row[0],
                section_id=row[1],
                day=row[2],
                start_time=row[3],
                end_time=row[4],
                room=row[5],
                instructor=row[6],
            ))

        for row in snapshot.components:
            db.session.add(SchedulerMapComponent(
                id=row[0],
                node_type=row[1],
                x_coordinate=row[2],
                y_coordinate=row[3],
                category=row[4],
            ))
        db.session.flush()

        for row in snapshot.lines:
            db.session.add(SchedulerMapLine(
                start_id=row[0],
                end_id=row[1],
                line_type=row[2],
                x_coordinate=row[3],
                category=row[4],
            ))
        db.session.flush()

        summary = {
            'courses': len(snapshot.courses),
            'sections': SchedulerSection.query.count(),
            'lectures': SchedulerLecture.query.count(),
            'map_components': SchedulerMapComponent.query.count(),
            'map_lines': SchedulerMapLine.query.count(),
        }
        validate_destination(snapshot, summary)
        db.session.commit()
        return summary
    except Exception:
        db.session.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(
        description='Import public scheduler data from CoursePlan.search'
    )
    parser.add_argument(
        '--source',
        required=True,
        help='Read-only source PostgreSQL connection string',
    )
    args = parser.parse_args()

    app = create_app()
    with create_engine(args.source).connect() as source_conn, app.app_context():
        if source_conn.dialect.name == 'postgresql':
            source_conn.execute(text('SET TRANSACTION READ ONLY'))
        print(json.dumps(import_snapshot(source_conn), ensure_ascii=True, sort_keys=True))


if __name__ == '__main__':
    main()
