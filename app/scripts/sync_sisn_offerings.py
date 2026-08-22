from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.config import Config
from app.scripts.import_scheduler_offerings import create_import_app
from app.services.sisn_proxy_client import SisnProxyClient
from app.services.sisn_sync import SisnSyncGuards, run_sisn_sync


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the official SISN snapshot through the school-server proxy. "
            "The default is a non-mutating dry-run."
        )
    )
    parser.add_argument("--term", default=Config.SISN_SYNC_TERM)
    parser.add_argument("--baseline", type=Path, default=Path(Config.SISN_SYNC_BASELINE_PATH))
    parser.add_argument("--archive-dir", type=Path, default=(
        Path(Config.SISN_SYNC_ARCHIVE_DIR) if Config.SISN_SYNC_ARCHIVE_DIR else None
    ))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--request-id")
    parser.add_argument("--expected-source-courses", type=int)
    parser.add_argument("--expected-source-classes", type=int)
    parser.add_argument("--expected-source-schedules", type=int)
    return parser


def _guards(args: argparse.Namespace) -> SisnSyncGuards:
    return SisnSyncGuards(
        min_source_courses=Config.SISN_SYNC_MIN_SOURCE_COURSES,
        max_source_courses=Config.SISN_SYNC_MAX_SOURCE_COURSES,
        min_source_classes=Config.SISN_SYNC_MIN_SOURCE_CLASSES,
        max_source_classes=Config.SISN_SYNC_MAX_SOURCE_CLASSES,
        min_source_schedules=Config.SISN_SYNC_MIN_SOURCE_SCHEDULES,
        max_source_schedules=Config.SISN_SYNC_MAX_SOURCE_SCHEDULES,
        min_candidate_sections=Config.SISN_SYNC_MIN_CANDIDATE_SECTIONS,
        max_fallback_main_classes=Config.SISN_SYNC_MAX_FALLBACK_MAIN_CLASSES,
        max_missing_baseline_classes=Config.SISN_SYNC_MAX_MISSING_BASELINE_CLASSES,
        max_omitted_unscheduled_classes=Config.SISN_SYNC_MAX_OMITTED_UNSCHEDULED_CLASSES,
        max_baseline_meeting_fallback_sections=(
            Config.SISN_SYNC_MAX_BASELINE_MEETING_FALLBACK_SECTIONS
        ),
        expected_source_courses=args.expected_source_courses,
        expected_source_classes=args.expected_source_classes,
        expected_source_schedules=args.expected_source_schedules,
    )


def main() -> int:
    args = _parser().parse_args()
    proxy_base_url = os.environ.get("SISN_PROXY_BASE_URL", Config.SISN_PROXY_BASE_URL)
    shared_secret = os.environ.get(
        "SISN_PROXY_SHARED_SECRET", Config.SISN_PROXY_SHARED_SECRET
    )
    client = SisnProxyClient(
        base_url=proxy_base_url,
        shared_secret=shared_secret,
        timeout_seconds=Config.SISN_PROXY_TIMEOUT_SECONDS,
    )
    app = create_import_app()
    with app.app_context():
        result = run_sisn_sync(
            client=client,
            term=args.term,
            baseline_path=args.baseline.resolve(),
            mode="apply" if args.apply else "dry-run",
            guards=_guards(args),
            archive_dir=args.archive_dir.resolve() if args.archive_dir else None,
            archive_retention_files=Config.SISN_SYNC_ARCHIVE_RETENTION_FILES,
            request_id=args.request_id,
        )
    print(json.dumps(result.__dict__, ensure_ascii=False, sort_keys=True))
    return 0 if result.status in {"dry-run", "applied", "skipped"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
