import argparse
import json
from dataclasses import asdict

from app import create_app
from app.extensions import db
from app.services.course_domain_migration import migrate_offerings


DEFAULT_SEMESTERS = ("2430", "2440")


def run_backfill(*, semesters: list[str], apply: bool):
    summary = migrate_offerings(apply=apply, semester_ids=semesters)
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
