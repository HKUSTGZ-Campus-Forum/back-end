from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import text

from app.extensions import db
from app.models.course_domain import SisnSyncRun
from app.scripts.import_scheduler_offerings import (
    ImportPlan,
    SnapshotExpectations,
    apply_offerings,
    build_import_plan,
    load_offerings_data,
    snapshot_counts,
)
from app.services.sisn_offerings import adapt_proxy_envelope, load_baseline
from app.services.sisn_proxy_client import SisnProxyClient


class SisnSyncBlocked(RuntimeError):
    """A safe, expected guard prevented a candidate from being applied."""


@dataclass(frozen=True)
class SisnSyncGuards:
    min_source_courses: int = 300
    max_source_courses: int = 600
    min_source_classes: int = 650
    max_source_classes: int = 1200
    min_source_schedules: int = 700
    max_source_schedules: int = 1800
    min_candidate_sections: int = 650
    max_fallback_main_classes: int = 20
    max_missing_baseline_classes: int = 50
    max_omitted_unscheduled_classes: int = 50
    expected_source_courses: int | None = None
    expected_source_classes: int | None = None
    expected_source_schedules: int | None = None


@dataclass(frozen=True)
class SisnSyncResult:
    request_id: str
    status: str
    mode: str
    semester_id: str
    source_payload_sha256: str | None
    candidate_sha256: str | None
    counts: dict[str, int]
    plan: dict[str, Any]
    warnings: list[str]


_LOCAL_LOCK = threading.Lock()
_LOCK_KEY = 7_312_601_001


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _candidate_sha256(snapshot: dict[str, Any]) -> str:
    candidate = {
        "semester_id": snapshot.get("semester_id"),
        "courses": snapshot.get("courses"),
    }
    return hashlib.sha256(_stable_json(candidate).encode("utf-8")).hexdigest()


def _plan_dict(plan: ImportPlan) -> dict[str, Any]:
    return asdict(plan)


def _safe_error_message(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    return message[:1000] or exc.__class__.__name__


@contextmanager
def _sync_lock() -> Iterator[None]:
    if not _LOCAL_LOCK.acquire(blocking=False):
        raise SisnSyncBlocked("another SISN sync is already running")
    advisory_acquired = False
    try:
        if db.engine.dialect.name == "postgresql":
            advisory_acquired = bool(db.session.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": _LOCK_KEY},
            ).scalar())
            if not advisory_acquired:
                raise SisnSyncBlocked("another SISN sync holds the database lock")
        yield
    finally:
        if advisory_acquired:
            db.session.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": _LOCK_KEY},
            )
            db.session.commit()
        _LOCAL_LOCK.release()


def _validate_guards(counts: dict[str, int], guards: SisnSyncGuards) -> None:
    range_checks = (
        ("source_courses", guards.min_source_courses, guards.max_source_courses),
        ("source_classes", guards.min_source_classes, guards.max_source_classes),
        ("source_schedules", guards.min_source_schedules, guards.max_source_schedules),
    )
    failures = [
        f"{name}={counts[name]} outside reviewed range {minimum}..{maximum}"
        for name, minimum, maximum in range_checks
        if not minimum <= counts[name] <= maximum
    ]
    minimum_checks = (
        ("candidate_sections", guards.min_candidate_sections),
    )
    failures.extend(
        f"{name}={counts[name]} below reviewed minimum {minimum}"
        for name, minimum in minimum_checks
        if counts[name] < minimum
    )
    maximum_checks = (
        ("fallback_main_classes", guards.max_fallback_main_classes),
        ("missing_baseline_classes", guards.max_missing_baseline_classes),
        ("omitted_unscheduled_classes", guards.max_omitted_unscheduled_classes),
    )
    failures.extend(
        f"{name}={counts[name]} above reviewed maximum {maximum}"
        for name, maximum in maximum_checks
        if counts[name] > maximum
    )
    exact_checks = (
        ("source_courses", guards.expected_source_courses),
        ("source_classes", guards.expected_source_classes),
        ("source_schedules", guards.expected_source_schedules),
    )
    failures.extend(
        f"{name}={counts[name]} does not match reviewed value {expected}"
        for name, expected in exact_checks
        if expected is not None and counts[name] != expected
    )
    if failures:
        raise SisnSyncBlocked("; ".join(failures))


def _archive_envelope(
    envelope: dict[str, Any],
    *,
    archive_dir: Path | None,
    term: str,
    request_id: str,
    retention_files: int,
) -> None:
    if archive_dir is None:
        return
    archive_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(archive_dir, 0o700)
    final_path = archive_dir / f"sisn-{term}-{request_id}.json.gz"
    fd, temporary_name = tempfile.mkstemp(prefix=".sisn-", suffix=".tmp", dir=archive_dir)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as raw_file:
            with gzip.GzipFile(fileobj=raw_file, mode="wb", mtime=0) as compressed:
                compressed.write(_stable_json(envelope).encode("utf-8"))
            raw_file.flush()
            os.fsync(raw_file.fileno())
        os.replace(temporary_name, final_path)
        archive_paths = sorted(
            archive_dir.glob(f"sisn-{term}-*.json.gz"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for expired_path in archive_paths[max(retention_files, 1):]:
            expired_path.unlink()
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _finish_run(
    run: SisnSyncRun,
    *,
    status: str,
    source_hash: str | None = None,
    candidate_hash: str | None = None,
    fetched_at: datetime | None = None,
    counts: dict[str, int] | None = None,
    plan: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    run.status = status
    run.source_payload_sha256 = source_hash
    run.candidate_sha256 = candidate_hash
    run.fetched_at = fetched_at
    run.counts = counts or {}
    run.plan = plan or {}
    run.error_code = error_code
    run.error_message = error_message
    run.completed_at = datetime.now(timezone.utc)
    db.session.add(run)
    db.session.commit()


def run_sisn_sync(
    *,
    client: SisnProxyClient,
    term: str,
    baseline_path: Path,
    mode: str = "dry-run",
    guards: SisnSyncGuards | None = None,
    archive_dir: Path | None = None,
    request_id: str | None = None,
    archive_retention_files: int = 336,
) -> SisnSyncResult:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"dry-run", "apply"}:
        raise ValueError("mode must be dry-run or apply")
    request_id = request_id or f"sisn-{uuid.uuid4().hex}"
    if len(request_id) > 64:
        raise ValueError("request_id must contain at most 64 characters")
    run = SisnSyncRun(
        request_id=request_id,
        semester_id=term,
        mode=normalized_mode,
        status="started",
    )
    db.session.add(run)
    db.session.commit()
    source_hash = None
    candidate_hash = None
    fetched_at = None
    counts: dict[str, int] = {}
    plan_value: dict[str, Any] = {}
    warnings: list[str] = []

    try:
        with _sync_lock():
            baseline = load_baseline(baseline_path)
            envelope = client.fetch_class_quota(term=term)
            _archive_envelope(
                envelope,
                archive_dir=archive_dir,
                term=term,
                request_id=request_id,
                retention_files=archive_retention_files,
            )
            adaptation = adapt_proxy_envelope(
                envelope,
                term=term,
                baseline=baseline,
                baseline_label=baseline_path.name,
            )
            source_hash = adaptation.source_payload_sha256
            fetched_at = adaptation.fetched_at
            counts = adaptation.counts
            warnings = adaptation.warnings
            _validate_guards(counts, guards or SisnSyncGuards())
            candidate_hash = _candidate_sha256(adaptation.snapshot)
            snapshot = load_offerings_data(adaptation.snapshot, semester_override=term)
            plan_value = _plan_dict(build_import_plan(snapshot))

            if normalized_mode == "apply":
                prior = SisnSyncRun.query.filter(
                    SisnSyncRun.id != run.id,
                    SisnSyncRun.semester_id == term,
                    SisnSyncRun.candidate_sha256 == candidate_hash,
                    SisnSyncRun.status == "applied",
                ).first()
                if prior is not None:
                    _finish_run(
                        run,
                        status="skipped",
                        source_hash=source_hash,
                        candidate_hash=candidate_hash,
                        fetched_at=fetched_at,
                        counts=counts,
                        plan=plan_value,
                    )
                    status = "skipped"
                else:
                    expected = snapshot_counts(snapshot)
                    applied_plan = apply_offerings(
                        snapshot,
                        expected_counts=SnapshotExpectations(**asdict(expected)),
                        allow_destructive_replacement=True,
                        import_hash=candidate_hash,
                        source="sisn",
                        preserve_missing_sections=True,
                    )
                    plan_value = _plan_dict(applied_plan)
                    _finish_run(
                        run,
                        status="applied",
                        source_hash=source_hash,
                        candidate_hash=candidate_hash,
                        fetched_at=fetched_at,
                        counts=counts,
                        plan=plan_value,
                    )
                    status = "applied"
            else:
                _finish_run(
                    run,
                    status="dry-run",
                    source_hash=source_hash,
                    candidate_hash=candidate_hash,
                    fetched_at=fetched_at,
                    counts=counts,
                    plan=plan_value,
                )
                status = "dry-run"
    except SisnSyncBlocked as exc:
        db.session.rollback()
        _finish_run(
            run,
            status="blocked",
            source_hash=source_hash,
            candidate_hash=candidate_hash,
            fetched_at=fetched_at,
            counts=counts,
            plan=plan_value,
            error_code=exc.__class__.__name__,
            error_message=_safe_error_message(exc),
        )
        status = "blocked"
    except Exception as exc:
        db.session.rollback()
        _finish_run(
            run,
            status="failed",
            source_hash=source_hash,
            candidate_hash=candidate_hash,
            fetched_at=fetched_at,
            counts=counts,
            plan=plan_value,
            error_code=exc.__class__.__name__,
            error_message=_safe_error_message(exc),
        )
        raise

    return SisnSyncResult(
        request_id=request_id,
        status=status,
        mode=normalized_mode,
        semester_id=term,
        source_payload_sha256=source_hash,
        candidate_sha256=candidate_hash,
        counts=counts,
        plan=plan_value,
        warnings=warnings,
    )
