import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path

from app import create_app
from app.extensions import db
from app.models.course_domain import CourseMeeting, CourseOffering, CourseSection
from app.services.course_domain import find_course_by_code, normalize_course_code
from app.services.course_domain_migration import (
    MigrationSummary,
    _current_version_for_course,
    migrate_offerings,
)


DEFAULT_SEMESTERS = ("2430", "2440")
LEGACY_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "scheduler_legacy"
PACKAGED_SEMESTER_FILES = {
    "2430": ("legacy_2430_sections.tsv", "legacy_2430_lectures.tsv"),
    "2440": ("legacy_2440_sections.tsv", "legacy_2440_lectures.tsv"),
}


def _merge_summary(target: MigrationSummary, source: MigrationSummary) -> MigrationSummary:
    target.scanned += source.scanned
    target.created += source.created
    target.updated += source.updated
    target.skipped += source.skipped
    target.anomalies.extend(source.anomalies)
    return target


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _int_value(value: str | None, default: int = 0) -> int:
    if value in {None, "", r"\N"}:
        return default
    return int(str(value).strip())


def _bool_value(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "t", "true", "yes", "y"}


def _packaged_legacy_backfill(semester_id: str, *, apply: bool) -> MigrationSummary:
    summary = MigrationSummary()
    file_pair = PACKAGED_SEMESTER_FILES.get(semester_id)
    if file_pair is None:
        return summary

    section_path = LEGACY_DATA_DIR / file_pair[0]
    lecture_path = LEGACY_DATA_DIR / file_pair[1]
    if not section_path.exists() or not lecture_path.exists():
        summary.anomalies.append({
            "type": "missing_packaged_legacy_scheduler_files",
            "semester_id": semester_id,
            "section_path": str(section_path),
            "lecture_path": str(lecture_path),
        })
        return summary

    sections = [
        row for row in _read_tsv(section_path)
        if str(row.get("semester_id", "")).strip() == semester_id
    ]
    lectures_by_section: dict[str, list[dict[str, str]]] = {}
    for row in _read_tsv(lecture_path):
        if str(row.get("semester_id", "")).strip() != semester_id:
            continue
        lectures_by_section.setdefault(str(row["section_id"]), []).append(row)

    sections_by_course: dict[str, list[dict[str, str]]] = {}
    for section in sections:
        course_code = str(section["course_code"]).strip()
        sections_by_course.setdefault(course_code, []).append(section)
        summary.scanned += 1

    for course_code, course_sections in sections_by_course.items():
        course = find_course_by_code(course_code)
        if course is None:
            summary.skipped += len(course_sections)
            summary.anomalies.append({
                "type": "unresolved_packaged_legacy_course",
                "course_code": course_code,
                "semester_id": semester_id,
                "section_count": len(course_sections),
            })
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
                source="packaged_legacy_scheduler",
                status="offered",
            )
            db.session.add(offering)
            db.session.flush()
        else:
            summary.updated += 1
            if apply:
                section_ids = [
                    section_id for (section_id,) in (
                        db.session.query(CourseSection.id)
                        .filter_by(offering_id=offering.id)
                        .all()
                    )
                ]
                if section_ids:
                    CourseMeeting.query.filter(CourseMeeting.section_id.in_(section_ids)).delete(
                        synchronize_session=False
                    )
                CourseSection.query.filter_by(offering_id=offering.id).delete(synchronize_session=False)
                offering.catalog_version_id = version.id if version else None
                offering.offering_code = course.normalized_code or normalize_course_code(course.code)
                offering.title_snapshot = version.title if version else course.name
                offering.credits_snapshot = version.credits if version else course.credits
                offering.source = "packaged_legacy_scheduler"
                offering.status = "offered"

        if not apply:
            continue

        for legacy_section in course_sections:
            source_section_id = str(legacy_section["section_id"]).strip()
            section = CourseSection(
                offering_id=offering.id,
                source_section_id=source_section_id,
                name=str(legacy_section["name"]).strip(),
                section_type=str(legacy_section["section_type"]).strip(),
                bundle=_int_value(legacy_section.get("bundle"), 1),
                layer=_int_value(legacy_section.get("layer"), 0),
                quota=_int_value(legacy_section.get("quota"), 0),
                enrol=_int_value(legacy_section.get("enrol"), 0),
                avail=_int_value(legacy_section.get("avail"), 0),
                wait=_int_value(legacy_section.get("wait"), 0),
                is_main=_bool_value(legacy_section.get("is_main")),
            )
            db.session.add(section)
            db.session.flush()

            for lecture in lectures_by_section.get(source_section_id, []):
                db.session.add(CourseMeeting(
                    section_id=section.id,
                    day=_int_value(lecture.get("day")),
                    start_time=_int_value(lecture.get("start_time")),
                    end_time=_int_value(lecture.get("end_time")),
                    room=str(lecture.get("room") or ""),
                    instructor_text=str(lecture.get("instructor") or ""),
                ))

    return summary


def run_backfill(*, semesters: list[str], apply: bool):
    summary = migrate_offerings(apply=apply, semester_ids=semesters)
    for semester_id in semesters:
        _merge_summary(summary, _packaged_legacy_backfill(str(semester_id), apply=apply))
    if apply:
        db.session.commit()
    else:
        db.session.rollback()
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Backfill selected legacy scheduler sections into course-domain offerings."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Inspect and report without committing changes.")
    mode.add_argument("--apply", action="store_true", help="Apply the backfill.")
    parser.add_argument(
        "--semesters",
        nargs="+",
        default=list(DEFAULT_SEMESTERS),
        help="Semester ids to backfill from legacy scheduler sections.",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        summary = run_backfill(semesters=args.semesters, apply=args.apply)
        print(json.dumps(asdict(summary), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
