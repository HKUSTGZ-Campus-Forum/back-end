# back-end/app/scripts/migrate_scheduler_data.py
"""
Migrate data from CoursePlan.search PostgreSQL to UniKorn PostgreSQL.

Usage:
    python -m app.scripts.migrate_scheduler_data --source "postgresql://user:pass@host:5432/courseplan_db"

This script is idempotent — it can be run multiple times safely.
"""
import argparse
from sqlalchemy import create_engine, text
from app import create_app
from app.extensions import db
from app.models.course import Course
from app.models.scheduler_section import SchedulerSection
from app.models.scheduler_lecture import SchedulerLecture
from app.models.scheduler_map import SchedulerMapComponent, SchedulerMapLine


def migrate_courses(source_conn):
    """Migrate course data from source to UniKorn courses table."""
    rows = source_conn.execute(text(
        'SELECT course_code, course_title, course_title_abbr, course_desc, '
        'pre_requirement, co_requirement, exclusion, credit, subject, '
        'catalog_number, pg_course, klms_course, vector FROM course'
    )).fetchall()

    created = 0
    updated = 0
    for row in rows:
        course_code, title, abbr, desc, pre, co, excl, credit, subject, cat_num, pg, klms, vec = row
        existing = Course.query.filter_by(code=course_code).first()
        if existing:
            existing.name = title
            existing.description = desc or ''
            existing.credits = credit
            existing.subject = subject
            existing.catalog_number = cat_num
            existing.course_title_abbr = abbr
            existing.pre_requirement = pre
            existing.co_requirement = co
            existing.exclusion = excl
            existing.pg_course = pg
            existing.klms_course = klms
            existing.vector = vec
            updated += 1
        else:
            course = Course(
                code=course_code,
                name=title,
                description=desc or '',
                credits=credit,
                subject=subject,
                catalog_number=cat_num,
                course_title_abbr=abbr,
                pre_requirement=pre,
                co_requirement=co,
                exclusion=excl,
                pg_course=pg,
                klms_course=klms,
                vector=vec,
            )
            db.session.add(course)
            created += 1

    db.session.commit()
    print(f"Courses: {created} created, {updated} updated")


def migrate_sections_and_lectures(source_conn):
    """Migrate section and lecture data."""
    # Clear existing scheduler data
    SchedulerLecture.query.delete()
    SchedulerSection.query.delete()
    db.session.commit()

    sections = source_conn.execute(text(
        'SELECT semester_id, section_id, course_code, name, bundle, layer, '
        'quota, section_type, is_main FROM section'
    )).fetchall()

    section_count = 0
    for row in sections:
        sem_id, sec_id, course_code, name, bundle, layer, quota, sec_type, is_main = row
        course = Course.query.filter_by(code=course_code).first()
        if not course:
            continue
        section = SchedulerSection(
            semester_id=sem_id,
            section_id=sec_id,
            course_id=course.id,
            name=name,
            bundle=bundle,
            layer=layer,
            quota=quota,
            section_type=sec_type,
            is_main=is_main,
        )
        db.session.add(section)
        section_count += 1

    db.session.commit()
    print(f"Sections: {section_count} migrated")

    lectures = source_conn.execute(text(
        'SELECT semester_id, section_id, day, start_time, end_time, room, instructor FROM lecture'
    )).fetchall()

    lecture_count = 0
    for row in lectures:
        sem_id, sec_id, day, start, end, room, instructor = row
        lecture = SchedulerLecture(
            semester_id=sem_id,
            section_id=sec_id,
            day=day,
            start_time=start,
            end_time=end,
            room=room,
            instructor=instructor,
        )
        db.session.add(lecture)
        lecture_count += 1

    db.session.commit()
    print(f"Lectures: {lecture_count} migrated")


def migrate_map_data(source_conn):
    """Migrate map component and line data."""
    SchedulerMapLine.query.delete()
    SchedulerMapComponent.query.delete()
    db.session.commit()

    comps = source_conn.execute(text(
        'SELECT id, node_type, x_coordinate, y_coordinate, category FROM map_component'
    )).fetchall()

    for row in comps:
        comp = SchedulerMapComponent(
            id=row[0],
            node_type=row[1],
            x_coordinate=row[2],
            y_coordinate=row[3],
            category=row[4],
        )
        db.session.add(comp)

    db.session.commit()
    print(f"Map components: {len(comps)} migrated")

    map_lines = source_conn.execute(text(
        'SELECT start_id, end_id, line_type, x_coordinate, category FROM map_line'
    )).fetchall()

    for row in map_lines:
        line = SchedulerMapLine(
            start_id=row[0],
            end_id=row[1],
            line_type=row[2],
            x_coordinate=row[3],
            category=row[4],
        )
        db.session.add(line)

    db.session.commit()
    print(f"Map lines: {len(map_lines)} migrated")


def main():
    parser = argparse.ArgumentParser(description='Migrate scheduler data from CoursePlan.search')
    parser.add_argument('--source', required=True, help='Source PostgreSQL connection string')
    args = parser.parse_args()

    source_engine = create_engine(args.source)
    source_conn = source_engine.connect()

    app = create_app()
    with app.app_context():
        print("Starting migration...")
        migrate_courses(source_conn)
        migrate_sections_and_lectures(source_conn)
        migrate_map_data(source_conn)
        print("Migration complete!")

    source_conn.close()


if __name__ == '__main__':
    main()
