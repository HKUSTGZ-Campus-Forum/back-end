#!/usr/bin/env python3
"""Read-only, descriptor-bound audit of the fixed production storage hierarchy."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


ROOT_PATH = Path("/")
DATA_NAME = "data"
PRODUCTION_NAME = "prod_unikorn"
APP_NAME = "back-end"
GIT_NAME = ".git"
MIGRATIONS_NAME = "migrations"
VERSIONS_NAME = "versions"
EXPECTED_APP_DIR = Path("/data/prod_unikorn/back-end")
EXPECTED_HEAD = b"ref: refs/heads/production\n"
PRODUCTION_REF_NAME = "production"
LEGACY_MIGRATION_NAME = "000000000000_create_oauth_tables.py"
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
OPTIONAL_PRODUCTION_CHILDREN = (
    "backups",
    "front-end",
    "operation-reports",
    "scheduler-popularity-releases",
)
MAX_CONTROL_FILE_BYTES = 4096
MAX_MIGRATION_BYTES = 1_048_576
FAILURE_INJECTOR = None


class AuditBlocked(RuntimeError):
    """The fixed hierarchy could not be inspected without ambiguity."""


def _reject_posix_acl(descriptor: int, description: str) -> None:
    """Reject Linux access/default ACLs; mode bits are the reviewed authority."""
    if not sys.platform.startswith("linux"):
        return
    try:
        names = os.listxattr(descriptor)
    except (AttributeError, OSError) as error:
        raise AuditBlocked(
            f"cannot attest POSIX ACLs for fixed {description}"
        ) from error
    forbidden = {"system.posix_acl_access", "system.posix_acl_default"}
    if forbidden.intersection(names):
        raise AuditBlocked(f"fixed {description} has an unexpected POSIX ACL")


def _failure_point(name: str, context: dict[str, Any] | None = None) -> None:
    if FAILURE_INJECTOR is not None:
        FAILURE_INJECTOR(name, context or {})


def _identity(details: os.stat_result) -> tuple[int, int]:
    return details.st_dev, details.st_ino


def _metadata(details: os.stat_result, path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "device": details.st_dev,
        "inode": details.st_ino,
        "owner_uid": details.st_uid,
        "group_gid": details.st_gid,
        "mode": f"{stat.S_IMODE(details.st_mode):04o}",
    }


def _open_directory_path(path: Path, description: str) -> int:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError as error:
        raise AuditBlocked(f"cannot safely open fixed {description}") from error
    try:
        _reject_posix_acl(descriptor, description)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_child(parent_fd: int, name: str, description: str) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise AuditBlocked(f"cannot safely open fixed {description}") from error
    try:
        _reject_posix_acl(descriptor, description)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _bound_stat(parent_fd: int, name: str, description: str) -> os.stat_result:
    try:
        details = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise AuditBlocked(f"cannot revalidate fixed {description}") from error
    if not stat.S_ISDIR(details.st_mode):
        raise AuditBlocked(f"fixed {description} is not a real directory")
    return details


def _open_optional_child(
    parent_fd: int,
    name: str,
    path: Path,
) -> tuple[int | None, dict[str, Any]]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except FileNotFoundError:
        return None, {"path": str(path), "status": "absent"}
    except OSError as error:
        raise AuditBlocked(f"cannot safely open optional fixed child {path}") from error
    try:
        details = os.fstat(descriptor)
        _reject_posix_acl(descriptor, f"optional child {path}")
        bound = _bound_stat(parent_fd, name, f"optional child {path}")
    except Exception:
        os.close(descriptor)
        raise
    if _identity(details) != _identity(bound):
        os.close(descriptor)
        raise AuditBlocked(f"optional child identity changed: {path}")
    return descriptor, {**_metadata(details, path), "status": "present"}


def _open_bound_file(
    parent_fd: int,
    name: str,
    path: Path,
    *,
    max_bytes: int,
) -> tuple[int, os.stat_result, bytes, dict[str, Any]]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise AuditBlocked(f"cannot safely open fixed file {path}") from error
    try:
        _reject_posix_acl(descriptor, f"file {path}")
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > max_bytes
        ):
            raise AuditBlocked(f"fixed file has unsafe metadata: {path}")
        payload = os.read(descriptor, max_bytes + 1)
        after = os.fstat(descriptor)
        bound = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            len(payload) != before.st_size
            or _identity(before) != _identity(after)
            or _identity(before) != _identity(bound)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or not stat.S_ISREG(bound.st_mode)
        ):
            raise AuditBlocked(f"fixed file changed while reading: {path}")
        record = {
            **_metadata(before, path),
            "size": before.st_size,
            "nlink": before.st_nlink,
            "mtime_ns": before.st_mtime_ns,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        return descriptor, before, payload, record
    except Exception:
        os.close(descriptor)
        raise


def _revalidate_bound_file(
    parent_fd: int,
    name: str,
    descriptor: int,
    initial: os.stat_result,
    record: dict[str, Any],
) -> None:
    before = os.fstat(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)
    payload = os.read(descriptor, record["size"] + 1)
    after = os.fstat(descriptor)
    try:
        bound = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise AuditBlocked(f"cannot revalidate fixed file {record['path']}") from error
    current_record = {
        **_metadata(after, Path(record["path"])),
        "size": after.st_size,
        "nlink": after.st_nlink,
        "mtime_ns": after.st_mtime_ns,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    if (
        not stat.S_ISREG(before.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or not stat.S_ISREG(bound.st_mode)
        or _identity(before) != _identity(initial)
        or _identity(after) != _identity(initial)
        or _identity(bound) != _identity(initial)
        or len(payload) != record["size"]
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or current_record != record
    ):
        raise AuditBlocked(f"fixed file changed during audit: {record['path']}")


def _open_git_control_files(
    git_fd: int,
    versions_fd: int,
) -> tuple[
    list[int],
    list[tuple[int, str, int, os.stat_result, dict[str, Any]]],
    list[tuple[int, str, int, os.stat_result, dict[str, Any]]],
    dict[str, Any],
]:
    descriptors: list[int] = []
    directories: list[tuple[int, str, int, os.stat_result, dict[str, Any]]] = []
    files: list[tuple[int, str, int, os.stat_result, dict[str, Any]]] = []
    try:
        head_fd, head_details, head_payload, head_record = _open_bound_file(
            git_fd,
            "HEAD",
            EXPECTED_APP_DIR / GIT_NAME / "HEAD",
            max_bytes=MAX_CONTROL_FILE_BYTES,
        )
        descriptors.append(head_fd)
        files.append((git_fd, "HEAD", head_fd, head_details, head_record))
        if head_payload != EXPECTED_HEAD:
            raise AuditBlocked("production Git HEAD is not the production branch")

        refs_fd = _open_child(git_fd, "refs", "Git refs directory")
        descriptors.append(refs_fd)
        refs_details = os.fstat(refs_fd)
        refs_record = _metadata(
            refs_details,
            EXPECTED_APP_DIR / GIT_NAME / "refs",
        )
        directories.append((git_fd, "refs", refs_fd, refs_details, refs_record))
        heads_fd = _open_child(refs_fd, "heads", "Git heads directory")
        descriptors.append(heads_fd)
        heads_details = os.fstat(heads_fd)
        heads_record = _metadata(
            heads_details,
            EXPECTED_APP_DIR / GIT_NAME / "refs" / "heads",
        )
        directories.append(
            (refs_fd, "heads", heads_fd, heads_details, heads_record)
        )
        ref_fd, ref_details, ref_payload, ref_record = _open_bound_file(
            heads_fd,
            PRODUCTION_REF_NAME,
            EXPECTED_APP_DIR / GIT_NAME / "refs" / "heads" / PRODUCTION_REF_NAME,
            max_bytes=MAX_CONTROL_FILE_BYTES,
        )
        descriptors.append(ref_fd)
        files.append(
            (
                heads_fd,
                PRODUCTION_REF_NAME,
                ref_fd,
                ref_details,
                ref_record,
            )
        )
        try:
            head_sha = ref_payload.decode("ascii").rstrip("\n")
        except UnicodeDecodeError as error:
            raise AuditBlocked("production Git ref is not ASCII") from error
        if ref_payload != f"{head_sha}\n".encode("ascii") or not GIT_SHA_RE.fullmatch(
            head_sha
        ):
            raise AuditBlocked("production Git ref is not an exact SHA")

        migration_fd, migration_details, _migration_payload, migration_record = (
            _open_bound_file(
                versions_fd,
                LEGACY_MIGRATION_NAME,
                EXPECTED_APP_DIR
                / MIGRATIONS_NAME
                / VERSIONS_NAME
                / LEGACY_MIGRATION_NAME,
                max_bytes=MAX_MIGRATION_BYTES,
            )
        )
        descriptors.append(migration_fd)
        files.append(
            (
                versions_fd,
                LEGACY_MIGRATION_NAME,
                migration_fd,
                migration_details,
                migration_record,
            )
        )
        return descriptors, directories, files, {
            "branch": "production",
            "head_sha": head_sha,
            "git_control_directories": [refs_record, heads_record],
            "git_control_files": [head_record, ref_record],
            "legacy_migration": migration_record,
        }
    except Exception:
        _close_all(*descriptors)
        raise


def _close_all(*descriptors: int | None) -> None:
    for descriptor in reversed(descriptors):
        if descriptor is not None:
            os.close(descriptor)


def audit() -> dict[str, Any]:
    root_fd = _open_directory_path(ROOT_PATH, "filesystem root")
    data_fd = production_fd = app_fd = git_fd = migrations_fd = versions_fd = None
    optional_states: list[tuple[str, int | None, dict[str, Any]]] = []
    control_descriptors: list[int] = []
    control_directories: list[
        tuple[int, str, int, os.stat_result, dict[str, Any]]
    ] = []
    control_files: list[tuple[int, str, int, os.stat_result, dict[str, Any]]] = []
    try:
        data_fd = _open_child(root_fd, DATA_NAME, "data container")
        production_fd = _open_child(
            data_fd, PRODUCTION_NAME, "production data parent"
        )
        app_fd = _open_child(production_fd, APP_NAME, "backend checkout")
        git_fd = _open_child(app_fd, GIT_NAME, "backend Git directory")
        migrations_fd = _open_child(app_fd, MIGRATIONS_NAME, "migrations directory")
        versions_fd = _open_child(
            migrations_fd, VERSIONS_NAME, "migration versions directory"
        )

        fixed = [
            (root_fd, None, None, ROOT_PATH, "filesystem root"),
            (data_fd, root_fd, DATA_NAME, ROOT_PATH / DATA_NAME, "data container"),
            (
                production_fd,
                data_fd,
                PRODUCTION_NAME,
                ROOT_PATH / DATA_NAME / PRODUCTION_NAME,
                "production data parent",
            ),
            (
                app_fd,
                production_fd,
                APP_NAME,
                EXPECTED_APP_DIR,
                "backend checkout",
            ),
            (
                git_fd,
                app_fd,
                GIT_NAME,
                EXPECTED_APP_DIR / GIT_NAME,
                "backend Git directory",
            ),
            (
                migrations_fd,
                app_fd,
                MIGRATIONS_NAME,
                EXPECTED_APP_DIR / MIGRATIONS_NAME,
                "migrations directory",
            ),
            (
                versions_fd,
                migrations_fd,
                VERSIONS_NAME,
                EXPECTED_APP_DIR / MIGRATIONS_NAME / VERSIONS_NAME,
                "migration versions directory",
            ),
        ]
        identities: dict[str, tuple[int, int]] = {}
        hierarchy: dict[str, dict[str, Any]] = {}
        for descriptor, parent_fd, name, path, description in fixed:
            details = os.fstat(descriptor)
            if not stat.S_ISDIR(details.st_mode):
                raise AuditBlocked(f"fixed {description} is not a directory")
            if parent_fd is None:
                bound = os.stat(ROOT_PATH, follow_symlinks=False)
            else:
                bound = _bound_stat(parent_fd, name, description)
            if _identity(details) != _identity(bound):
                raise AuditBlocked(f"fixed {description} identity changed")
            identities[description] = _identity(details)
            hierarchy[description] = _metadata(details, path)

        optional_children = []
        production_path = ROOT_PATH / DATA_NAME / PRODUCTION_NAME
        for name in OPTIONAL_PRODUCTION_CHILDREN:
            descriptor, record = _open_optional_child(
                production_fd,
                name,
                production_path / name,
            )
            if descriptor is not None:
                pass
            optional_states.append((name, descriptor, record))
            optional_children.append(record)

        (
            control_descriptors,
            control_directories,
            control_files,
            git_context,
        ) = _open_git_control_files(git_fd, versions_fd)

        context = {
            "schema_version": 1,
            "target": "production",
            "effective_uid": os.geteuid(),
            "effective_gid": os.getegid(),
            "hierarchy": hierarchy,
            "optional_children": optional_children,
            "same_device": len(
                {record["device"] for record in hierarchy.values()}
            )
            == 1,
            "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            **git_context,
        }

        _failure_point("before_final_revalidation", {})
        for descriptor, parent_fd, name, _path, description in fixed:
            details = os.fstat(descriptor)
            if parent_fd is None:
                bound = os.stat(ROOT_PATH, follow_symlinks=False)
            else:
                bound = _bound_stat(parent_fd, name, description)
            if (
                _identity(details) != identities[description]
                or _identity(bound) != identities[description]
                or _metadata(details, hierarchy[description]["path"])
                != hierarchy[description]
                or _metadata(bound, hierarchy[description]["path"])
                != hierarchy[description]
            ):
                raise AuditBlocked(f"fixed {description} changed during audit")

        for name, descriptor, record in optional_states:
            if descriptor is None:
                try:
                    os.stat(name, dir_fd=production_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise AuditBlocked(
                        f"cannot revalidate absent optional child {record['path']}"
                    ) from error
                raise AuditBlocked(
                    f"absent optional child appeared during audit: {record['path']}"
                )
            details = os.fstat(descriptor)
            bound = _bound_stat(
                production_fd,
                name,
                f"optional child {record['path']}",
            )
            expected = {key: value for key, value in record.items() if key != "status"}
            if (
                _metadata(details, Path(record["path"])) != expected
                or _metadata(bound, Path(record["path"])) != expected
            ):
                raise AuditBlocked(
                    f"optional child changed during audit: {record['path']}"
                )

        for parent_fd, name, descriptor, initial, record in control_files:
            _revalidate_bound_file(
                parent_fd,
                name,
                descriptor,
                initial,
                record,
            )

        for parent_fd, name, descriptor, initial, record in control_directories:
            current = os.fstat(descriptor)
            bound = _bound_stat(parent_fd, name, f"control directory {record['path']}")
            if (
                _identity(current) != _identity(initial)
                or _identity(bound) != _identity(initial)
                or _metadata(current, Path(record["path"])) != record
                or _metadata(bound, Path(record["path"])) != record
            ):
                raise AuditBlocked(
                    f"Git control directory changed during audit: {record['path']}"
                )

        payload = json.dumps(
            context,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return {
            **context,
            "aggregate_sha256": hashlib.sha256(payload).hexdigest(),
            "status": "audited",
        }
    finally:
        _close_all(
            *(descriptor for _name, descriptor, _record in optional_states),
            *control_descriptors,
            root_fd,
            data_fd,
            production_fd,
            app_fd,
            git_fd,
            migrations_fd,
            versions_fd,
        )


def main() -> int:
    try:
        result = audit()
    except AuditBlocked as error:
        print(f"production storage audit blocked: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
