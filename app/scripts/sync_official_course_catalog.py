from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import Config
from app.scripts.import_scheduler_offerings import create_import_app
from app.services.official_course_catalog_sync import (
    fetch_official_course_catalog,
    sync_official_course_catalog_records,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize authoritative course rules. Defaults to a non-mutating dry-run."
    )
    parser.add_argument("--term", default=Config.COURSE_CATALOG_SYNC_TERM)
    parser.add_argument("--career", default=Config.COURSE_CATALOG_SYNC_CAREER)
    parser.add_argument("--url", default=Config.COURSE_CATALOG_SYNC_URL)
    parser.add_argument("--file", type=Path, help="Read a saved official API response instead of the network")
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.file:
        payload = json.loads(args.file.read_text(encoding="utf-8"))
        records = payload.get("data", {}).get("records", payload.get("records", payload))
    else:
        records, _pagination = fetch_official_course_catalog(
            url=args.url,
            term=args.term,
            career=args.career,
            timeout_seconds=Config.COURSE_CATALOG_SYNC_TIMEOUT_SECONDS,
        )
    app = create_import_app()
    with app.app_context():
        result = sync_official_course_catalog_records(
            records,
            term=args.term,
            apply=args.apply,
            min_courses=Config.COURSE_CATALOG_SYNC_MIN_COURSES,
            max_courses=Config.COURSE_CATALOG_SYNC_MAX_COURSES,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
