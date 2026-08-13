"""Maintain the crash-safe journal for the legacy production API deployment.

The production service has a fixed working tree, so a deployment cannot use an
atomic release-directory swap.  This journal makes the remaining boundary
explicit: failures before ``MIGRATION_STARTED`` may restore the prior checkout;
at and after that phase the only safe automated recovery is forward to the
recorded candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time


JOURNAL_ROOT = Path(
    "/data/prod_unikorn/back-end/.git/unikorn-operations/api-deploy"
)
BACKUP_ROOT = Path("/data/prod_unikorn/backups")
ACTIVE_NAME = "ACTIVE.json"
ARCHIVE_NAME = "archive"
MAX_JOURNAL_BYTES = 16 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BACKUP_RE = re.compile(
    r"^prod_unikorn-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\.dump$"
)

PHASES = (
    "PREPARED",
    "SERVICE_STOP_REQUESTED",
    "SERVICE_STOPPED",
    "FINAL_BACKUP_STARTED",
    "FINAL_BACKUP_VERIFIED",
    "CHECKOUT_ACTIVATION_REQUESTED",
    "CANDIDATE_CHECKED_OUT",
    "MIGRATION_STARTED",
    "DB_AT_TARGET",
    "CANDIDATE_START_REQUESTED",
    "CANDIDATE_STARTED",
    "HEALTHY",
    "COMMITTED",
)
PHASE_INDEX = {phase: index for index, phase in enumerate(PHASES)}
FORWARD_BOUNDARY = PHASE_INDEX["MIGRATION_STARTED"]


class JournalError(RuntimeError):
    """The deployment journal is absent, unsafe, or internally inconsistent."""


def _validate_sha(value: str, name: str) -> str:
    if not SHA_RE.fullmatch(value):
        raise JournalError(f"{name} is not a full lowercase Git SHA")
    return value


def _open_root(*, create: bool) -> int:
    parent_fd = _open_operations_parent()
    if create:
        try:
            os.mkdir(JOURNAL_ROOT.name, mode=0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
    try:
        descriptor = os.open(
            JOURNAL_ROOT.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as error:
        os.close(parent_fd)
        raise JournalError("cannot safely open the production API journal root") from error
    os.close(parent_fd)
    details = os.fstat(descriptor)
    if details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) != 0o700:
        os.close(descriptor)
        raise JournalError("production API journal root has unsafe metadata")
    return descriptor


def _open_operations_parent() -> int:
    try:
        descriptor = os.open(
            JOURNAL_ROOT.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError as error:
        raise JournalError("cannot safely open the production operations root") from error
    details = os.fstat(descriptor)
    if details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) != 0o700:
        os.close(descriptor)
        raise JournalError("production operations root has unsafe metadata")
    return descriptor


def _read_active(root_fd: int, *, required: bool = True) -> dict | None:
    try:
        descriptor = os.open(
            ACTIVE_NAME,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=root_fd,
        )
    except FileNotFoundError:
        if required:
            raise JournalError("no active production API deployment journal")
        return None
    except OSError as error:
        raise JournalError("cannot safely open the active deployment journal") from error
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
            or details.st_size <= 0
            or details.st_size > MAX_JOURNAL_BYTES
        ):
            raise JournalError("active deployment journal has unsafe metadata")
        chunks = []
        bytes_read = 0
        while bytes_read <= MAX_JOURNAL_BYTES:
            chunk = os.read(descriptor, MAX_JOURNAL_BYTES + 1 - bytes_read)
            if not chunk:
                break
            chunks.append(chunk)
            bytes_read += len(chunk)
        payload = b"".join(chunks)
        if len(payload) != details.st_size:
            raise JournalError("active deployment journal changed while reading")
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (
            details.st_dev,
            details.st_ino,
            details.st_size,
            details.st_mtime_ns,
        ):
            raise JournalError("active deployment journal changed while reading")
    finally:
        os.close(descriptor)
    try:
        journal = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JournalError("active deployment journal is not valid JSON") from error
    _validate_journal(journal)
    return journal


def _validate_journal(journal: object) -> None:
    if not isinstance(journal, dict):
        raise JournalError("deployment journal is not an object")
    required = {
        "schema_version",
        "transaction_id",
        "target_sha",
        "old_sha",
        "phase",
        "created_at_epoch_ns",
        "updated_at_epoch_ns",
    }
    optional = {
        "backup_path",
        "backup_size",
        "backup_sha256",
        "backup_device",
        "backup_inode",
        "backup_mtime_ns",
        "backup_name",
        "database_name",
        "database_system_identifier",
    }
    if set(journal) - required - optional or not required <= set(journal):
        raise JournalError("deployment journal fields are not exact")
    if journal["schema_version"] != 1:
        raise JournalError("unsupported deployment journal schema")
    _validate_sha(journal["target_sha"], "target_sha")
    _validate_sha(journal["old_sha"], "old_sha")
    if not re.fullmatch(r"[0-9]+-[0-9a-f]{16}", journal["transaction_id"]):
        raise JournalError("invalid deployment transaction identifier")
    if journal["phase"] not in PHASE_INDEX:
        raise JournalError("invalid deployment phase")
    for field in ("created_at_epoch_ns", "updated_at_epoch_ns"):
        if (
            not isinstance(journal[field], int)
            or isinstance(journal[field], bool)
            or journal[field] <= 0
        ):
            raise JournalError(f"invalid {field}")
    if journal["updated_at_epoch_ns"] < journal["created_at_epoch_ns"]:
        raise JournalError("deployment journal timestamps are out of order")
    backup_fields = {
        "backup_name",
        "backup_path",
        "backup_size",
        "backup_sha256",
        "backup_device",
        "backup_inode",
        "backup_mtime_ns",
        "database_name",
        "database_system_identifier",
    }
    present_backup_fields = backup_fields & set(journal)
    if PHASE_INDEX[journal["phase"]] < PHASE_INDEX["FINAL_BACKUP_STARTED"]:
        if present_backup_fields:
            raise JournalError(
                "pre-backup journal unexpectedly contains database or backup metadata"
            )
    elif journal["phase"] == "FINAL_BACKUP_STARTED":
        if present_backup_fields != {"backup_name"}:
            raise JournalError("final backup start must bind the planned backup name")
        if not BACKUP_RE.fullmatch(journal["backup_name"]):
            raise JournalError("planned backup name is invalid")
        if not journal["backup_name"].endswith(
            f"-{journal['target_sha'][:12]}.dump"
        ):
            raise JournalError("planned backup name is not bound to the deployment target")
    else:
        if present_backup_fields != backup_fields:
            raise JournalError("verified backup and database identity are incomplete")
        backup = Path(journal["backup_path"])
        if journal["backup_name"] != backup.name:
            raise JournalError("backup path differs from the planned backup name")
        if backup.parent != BACKUP_ROOT or not BACKUP_RE.fullmatch(backup.name):
            raise JournalError("backup path is outside the fixed production scope")
        if not journal["backup_path"].endswith(
            f"-{journal['target_sha'][:12]}.dump"
        ):
            raise JournalError("backup path is not bound to the deployment target")
        if (
            not isinstance(journal["backup_size"], int)
            or isinstance(journal["backup_size"], bool)
            or journal["backup_size"] <= 0
        ):
            raise JournalError("backup size is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", journal["backup_sha256"]):
            raise JournalError("backup digest is invalid")
        for field in ("backup_device", "backup_inode", "backup_mtime_ns"):
            if (
                not isinstance(journal[field], int)
                or isinstance(journal[field], bool)
                or journal[field] <= 0
            ):
                raise JournalError(f"invalid {field}")
        if journal["database_name"] != "prod_unikorn":
            raise JournalError("database name is not the fixed production database")
        if not re.fullmatch(r"[0-9]+", journal["database_system_identifier"]):
            raise JournalError("database system identifier is invalid")


def _write_active(root_fd: int, journal: dict) -> None:
    _validate_journal(journal)
    payload = (
        json.dumps(journal, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_JOURNAL_BYTES:
        raise JournalError("deployment journal is unexpectedly large")
    temporary_name = f".ACTIVE.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=root_fd,
        )
        try:
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary_name, ACTIVE_NAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        os.fsync(root_fd)
    except Exception:
        try:
            os.unlink(temporary_name, dir_fd=root_fd)
        except FileNotFoundError:
            pass
        raise


def _archive_active(root_fd: int, journal: dict, *, outcome: str) -> None:
    if outcome not in {"aborted", "committed"}:
        raise JournalError("invalid archive outcome")
    try:
        os.mkdir(ARCHIVE_NAME, mode=0o700, dir_fd=root_fd)
        os.fsync(root_fd)
    except FileExistsError:
        pass
    archive_fd = os.open(
        ARCHIVE_NAME,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=root_fd,
    )
    try:
        details = os.fstat(archive_fd)
        if details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) != 0o700:
            raise JournalError("deployment journal archive has unsafe metadata")
        archive_name = f"{journal['transaction_id']}-{outcome}.json"
        os.rename(ACTIVE_NAME, archive_name, src_dir_fd=root_fd, dst_dir_fd=archive_fd)
        os.fsync(archive_fd)
        os.fsync(root_fd)
    finally:
        os.close(archive_fd)


def prepare(target_sha: str, old_sha: str) -> dict:
    target_sha = _validate_sha(target_sha, "target_sha")
    old_sha = _validate_sha(old_sha, "old_sha")
    root_fd = _open_root(create=True)
    try:
        current = _read_active(root_fd, required=False)
        if current is not None:
            if current["phase"] == "COMMITTED":
                if current["target_sha"] == target_sha:
                    return {**current, "resumed": True}
                _archive_active(root_fd, current, outcome="committed")
            elif current["target_sha"] != target_sha:
                raise JournalError(
                    "an incomplete deployment for a different target requires recovery"
                )
            else:
                return {**current, "resumed": True}
        now = time.time_ns()
        journal = {
            "schema_version": 1,
            "transaction_id": f"{now}-{secrets.token_hex(8)}",
            "target_sha": target_sha,
            "old_sha": old_sha,
            "phase": "PREPARED",
            "created_at_epoch_ns": now,
            "updated_at_epoch_ns": now,
        }
        _write_active(root_fd, journal)
        return {**journal, "resumed": False}
    finally:
        os.close(root_fd)


def advance(
    target_sha: str,
    phase: str,
    *,
    backup_path: str | None = None,
    backup_name: str | None = None,
    backup_size: int | None = None,
    backup_sha256: str | None = None,
    backup_device: int | None = None,
    backup_inode: int | None = None,
    backup_mtime_ns: int | None = None,
    database_name: str | None = None,
    database_system_identifier: str | None = None,
) -> dict:
    target_sha = _validate_sha(target_sha, "target_sha")
    if phase not in PHASE_INDEX:
        raise JournalError("invalid requested deployment phase")
    root_fd = _open_root(create=False)
    try:
        journal = _read_active(root_fd)
        assert journal is not None
        if journal["target_sha"] != target_sha:
            raise JournalError("active deployment target does not match")
        current_index = PHASE_INDEX[journal["phase"]]
        requested_index = PHASE_INDEX[phase]
        if requested_index == current_index:
            return journal
        if requested_index != current_index + 1:
            raise JournalError("deployment phases must advance exactly one step")
        updated = dict(journal)
        updated["phase"] = phase
        updated["updated_at_epoch_ns"] = time.time_ns()
        supplied_backup_values = (
            backup_path,
            backup_size,
            backup_sha256,
            backup_device,
            backup_inode,
            backup_mtime_ns,
            database_name,
            database_system_identifier,
        )
        if phase == "FINAL_BACKUP_STARTED":
            if backup_name is None:
                raise JournalError("planned backup name is required")
            if any(value is not None for value in supplied_backup_values):
                raise JournalError(
                    "verified metadata is accepted only at FINAL_BACKUP_VERIFIED"
                )
            updated["backup_name"] = backup_name
        elif phase == "FINAL_BACKUP_VERIFIED":
            if any(value is None for value in supplied_backup_values):
                raise JournalError(
                    "verified backup metadata and database identity are required"
                )
            updated.update(
                backup_name=journal.get("backup_name"),
                backup_path=backup_path,
                backup_size=backup_size,
                backup_sha256=backup_sha256,
                backup_device=backup_device,
                backup_inode=backup_inode,
                backup_mtime_ns=backup_mtime_ns,
                database_name=database_name,
                database_system_identifier=database_system_identifier,
            )
        elif backup_name is not None or any(
            value is not None for value in supplied_backup_values
        ):
            raise JournalError(
                "backup and database metadata are accepted only at FINAL_BACKUP_VERIFIED"
            )
        _write_active(root_fd, updated)
        return updated
    finally:
        os.close(root_fd)


def inspect() -> dict:
    """Distinguish an absent active journal from any unsafe journal state."""

    try:
        root_fd = _open_root(create=False)
    except JournalError:
        # A missing journal directory is normal only when its safely opened
        # parent proves that exact child path is absent.
        parent_fd = _open_operations_parent()
        try:
            try:
                os.stat(JOURNAL_ROOT.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return {"status": "absent"}
        finally:
            os.close(parent_fd)
        raise
    try:
        journal = _read_active(root_fd, required=False)
        if journal is None:
            return {"status": "absent"}
        return {"status": "active", "journal": journal}
    finally:
        os.close(root_fd)


def show(target_sha: str | None = None) -> dict:
    root_fd = _open_root(create=False)
    try:
        journal = _read_active(root_fd)
        assert journal is not None
        if target_sha is not None and journal["target_sha"] != _validate_sha(
            target_sha, "target_sha"
        ):
            raise JournalError("active deployment target does not match")
        return journal
    finally:
        os.close(root_fd)


def verify_backup(target_sha: str) -> dict:
    """Re-authenticate the recorded backup before crossing the DB boundary."""

    target_sha = _validate_sha(target_sha, "target_sha")
    root_fd = _open_root(create=False)
    try:
        journal = _read_active(root_fd)
        assert journal is not None
        if journal["target_sha"] != target_sha:
            raise JournalError("active deployment target does not match")
        if PHASE_INDEX[journal["phase"]] < PHASE_INDEX["FINAL_BACKUP_VERIFIED"]:
            raise JournalError("deployment has no verified backup")
        backup_path = Path(journal["backup_path"])
        try:
            backup_root_fd = os.open(
                BACKUP_ROOT,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
        except OSError as error:
            raise JournalError("cannot safely open the database backup root") from error
        root_details = os.fstat(backup_root_fd)
        if (
            root_details.st_uid != os.geteuid()
            or stat.S_IMODE(root_details.st_mode) != 0o750
        ):
            os.close(backup_root_fd)
            raise JournalError("database backup root has unsafe metadata")
        try:
            descriptor = os.open(
                backup_path.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=backup_root_fd,
            )
        except OSError as error:
            os.close(backup_root_fd)
            raise JournalError("cannot safely open the recorded database backup") from error
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.geteuid()
                or details.st_gid != os.getegid()
                or stat.S_IMODE(details.st_mode) != 0o600
                or details.st_nlink != 1
                or details.st_size != journal["backup_size"]
                or details.st_dev != journal["backup_device"]
                or details.st_ino != journal["backup_inode"]
                or details.st_mtime_ns != journal["backup_mtime_ns"]
            ):
                raise JournalError("recorded database backup has unsafe metadata")
            before_identity = (
                details.st_dev,
                details.st_ino,
                details.st_uid,
                details.st_gid,
                details.st_mode,
                details.st_nlink,
                details.st_size,
                details.st_mtime_ns,
                details.st_ctime_ns,
            )
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
            after = os.fstat(descriptor)
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_uid,
                after.st_gid,
                after.st_mode,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if after_identity != before_identity:
                raise JournalError("recorded database backup changed while reading")
        finally:
            os.close(descriptor)
            os.close(backup_root_fd)
        if digest.hexdigest() != journal["backup_sha256"]:
            raise JournalError("recorded database backup digest does not match")
        return {
            "status": "verified",
            "target_sha": target_sha,
            "backup_path": str(backup_path),
            "backup_size": details.st_size,
            "backup_sha256": digest.hexdigest(),
        }
    finally:
        os.close(root_fd)


def archive_aborted(target_sha: str) -> dict:
    target_sha = _validate_sha(target_sha, "target_sha")
    root_fd = _open_root(create=False)
    try:
        journal = _read_active(root_fd)
        assert journal is not None
        if journal["target_sha"] != target_sha:
            raise JournalError("active deployment target does not match")
        if PHASE_INDEX[journal["phase"]] >= FORWARD_BOUNDARY:
            raise JournalError("cannot abort a deployment at or beyond migration start")
        _archive_active(root_fd, journal, outcome="aborted")
        return {**journal, "outcome": "aborted"}
    finally:
        os.close(root_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--target-sha", required=True)
    prepare_parser.add_argument("--old-sha", required=True)

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--target-sha")
    subparsers.add_parser("inspect")

    verify_backup_parser = subparsers.add_parser("verify-backup")
    verify_backup_parser.add_argument("--target-sha", required=True)

    advance_parser = subparsers.add_parser("advance")
    advance_parser.add_argument("--target-sha", required=True)
    advance_parser.add_argument("--phase", required=True, choices=PHASES)
    advance_parser.add_argument("--backup-path")
    advance_parser.add_argument("--backup-name")
    advance_parser.add_argument("--backup-size", type=int)
    advance_parser.add_argument("--backup-sha256")
    advance_parser.add_argument("--backup-device", type=int)
    advance_parser.add_argument("--backup-inode", type=int)
    advance_parser.add_argument("--backup-mtime-ns", type=int)
    advance_parser.add_argument("--database-name")
    advance_parser.add_argument("--database-system-identifier")

    abort_parser = subparsers.add_parser("archive-aborted")
    abort_parser.add_argument("--target-sha", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "prepare":
            result = prepare(arguments.target_sha, arguments.old_sha)
        elif arguments.command == "inspect":
            result = inspect()
        elif arguments.command == "show":
            result = show(arguments.target_sha)
        elif arguments.command == "verify-backup":
            result = verify_backup(arguments.target_sha)
        elif arguments.command == "advance":
            result = advance(
                arguments.target_sha,
                arguments.phase,
                backup_path=arguments.backup_path,
                backup_name=arguments.backup_name,
                backup_size=arguments.backup_size,
                backup_sha256=arguments.backup_sha256,
                backup_device=arguments.backup_device,
                backup_inode=arguments.backup_inode,
                backup_mtime_ns=arguments.backup_mtime_ns,
                database_name=arguments.database_name,
                database_system_identifier=arguments.database_system_identifier,
            )
        else:
            result = archive_aborted(arguments.target_sha)
    except (JournalError, OSError, ValueError) as error:
        print(json.dumps({"status": "blocked", "message": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
