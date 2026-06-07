import argparse
import json
from pathlib import Path

from app import create_app
from app.extensions import db
from app.services.course_domain_migration import (
    CourseDomainMigrationReport,
    canonicalize_courses,
    migrate_catalog_versions,
    migrate_offerings,
    migrate_requirements,
    migrate_review_targets,
    migrate_scheduler_carts,
    migrate_user_academic_state,
)


def run_course_domain_migration(*, apply: bool, anomaly_file: Path) -> CourseDomainMigrationReport:
    catalog_path = Path(__file__).resolve().parents[1] / "data" / "course_catalog.json"
    prerequisite_path = Path(__file__).resolve().parents[1] / "data" / "course_prerequisites.json"
    report = CourseDomainMigrationReport()

    report.canonical_courses = canonicalize_courses(apply=apply)
    report.catalog_versions = migrate_catalog_versions(catalog_path=catalog_path, apply=apply)
    report.requirements = migrate_requirements(prerequisite_path=prerequisite_path, apply=apply)
    report.offerings = migrate_offerings(apply=apply)
    report.user_state = migrate_user_academic_state(apply=apply)
    migrate_scheduler_carts(apply=apply)
    report.review_targets = migrate_review_targets(anomaly_path=anomaly_file, apply=apply)

    if apply:
        db.session.commit()
    else:
        db.session.rollback()

    return report


def main():
    parser = argparse.ArgumentParser(description="Migrate UniKorn course data into the redesigned course domain.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Inspect and report without committing changes.")
    mode.add_argument("--apply", action="store_true", help="Apply the migration.")
    parser.add_argument("--anomaly-file", required=True, help="Path to write unresolved review target anomalies.")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        report = run_course_domain_migration(
            apply=args.apply,
            anomaly_file=Path(args.anomaly_file),
        )
        print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
