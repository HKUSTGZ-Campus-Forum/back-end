#!/usr/bin/env python3
"""Audit and add sticky protection to the fixed shared ``/data`` container."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any


ROOT_PATH = Path("/")
DATA_PATH = Path("/data")
DATA_NAME = "data"
DEV_PARENT_NAME = "dev_unikorn"
APP_NAME = "back-end"
LOCK_NAME = "backend-mutations-dev.lock"
ROOT_OWNER_UID = 0
ROOT_GROUP_GID = 0
EXPECTED_MODE = 0o777
TARGET_MODE = 0o1777
APPLY_CONFIRMATION = "HARDEN_DEV_DATA_CONTAINER_0777_TO_1777"
SUDO_PATH = "/usr/bin/sudo"
CHMOD_PATH = "/usr/bin/chmod"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
RELEASE_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
FAILURE_INJECTOR = None


class HardeningBlocked(RuntimeError):
    """The host no longer matches the reviewed hardening boundary."""


def _failure_point(name: str, context: dict[str, Any] | None = None) -> None:
    if FAILURE_INJECTOR is not None:
        FAILURE_INJECTOR(name, context or {})


def _canonical_digest(context: dict[str, Any]) -> str:
    payload = json.dumps(
        context, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _mode(details: os.stat_result) -> int:
    return stat.S_IMODE(details.st_mode)


def _metadata(details: os.stat_result, prefix: str) -> dict[str, Any]:
    return {
        f"{prefix}_owner_uid": details.st_uid,
        f"{prefix}_group_gid": details.st_gid,
        f"{prefix}_mode": f"{_mode(details):04o}",
        f"{prefix}_device": details.st_dev,
        f"{prefix}_inode": details.st_ino,
    }


def _identity(details: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_uid,
        details.st_gid,
        _mode(details),
    )


def _open_directory(path: Path) -> int:
    try:
        return os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError as error:
        raise HardeningBlocked(f"cannot safely open fixed directory {path}") from error


def _open_child(parent_fd: int, name: str, description: str) -> int:
    try:
        return os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise HardeningBlocked(f"cannot safely open fixed {description}") from error


def _require_root(details: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != ROOT_OWNER_UID
        or details.st_gid != ROOT_GROUP_GID
        or _mode(details) & 0o022
    ):
        raise HardeningBlocked(
            "filesystem root has unsafe ownership or permissions: "
            f"owner_uid={details.st_uid}, group_gid={details.st_gid}, "
            f"mode={_mode(details):04o}"
        )


def _require_data(details: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != ROOT_OWNER_UID
        or details.st_gid != ROOT_GROUP_GID
        or _mode(details) not in {EXPECTED_MODE, TARGET_MODE}
    ):
        raise HardeningBlocked(
            "fixed data container does not match the observed boundary: "
            f"owner_uid={details.st_uid}, group_gid={details.st_gid}, "
            f"mode={_mode(details):04o}"
        )


def _require_dev_parent(details: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_gid != os.getegid()
        or _mode(details) != 0o777
    ):
        raise HardeningBlocked(
            "fixed dev data parent has unexpected ownership, type, or mode"
        )


def _require_app(details: os.stat_result) -> None:
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
        raise HardeningBlocked("fixed backend checkout has unexpected ownership or type")


def _open_lock(dev_parent_fd: int) -> int:
    try:
        descriptor = os.open(
            LOCK_NAME,
            os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=dev_parent_fd,
        )
    except OSError as error:
        raise HardeningBlocked("cannot safely open existing dev mutation lock") from error
    details = os.fstat(descriptor)
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_gid != os.getegid()
        or details.st_nlink != 1
        or _mode(details) != 0o600
    ):
        os.close(descriptor)
        raise HardeningBlocked("existing dev mutation lock has unsafe metadata")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise HardeningBlocked("another dev backend mutation holds the lock") from error
    return descriptor


def _stat_bound(name: str, parent_fd: int, description: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise HardeningBlocked(f"cannot revalidate fixed {description}") from error


def _verify_directory(
    actual: os.stat_result,
    expected: tuple[int, int, int, int, int],
    description: str,
) -> None:
    if not stat.S_ISDIR(actual.st_mode) or _identity(actual) != expected:
        raise HardeningBlocked(f"fixed {description} metadata or identity changed")


def _verify_lock(
    actual: os.stat_result,
    expected: tuple[int, int, int, int, int],
    description: str,
) -> None:
    if (
        not stat.S_ISREG(actual.st_mode)
        or actual.st_nlink != 1
        or _identity(actual) != expected
    ):
        raise HardeningBlocked(f"fixed {description} metadata or identity changed")


def _verify_bindings(
    root_fd: int,
    data_fd: int,
    dev_parent_fd: int,
    app_fd: int,
    lock_fd: int,
    *,
    root_identity: tuple[int, int, int, int, int],
    data_identity: tuple[int, int, int, int, int],
    dev_parent_identity: tuple[int, int, int, int, int],
    app_identity: tuple[int, int, int, int, int],
    lock_identity: tuple[int, int, int, int, int],
) -> None:
    _verify_directory(os.fstat(root_fd), root_identity, "filesystem root")
    try:
        root_bound = os.stat(ROOT_PATH, follow_symlinks=False)
    except OSError as error:
        raise HardeningBlocked("cannot revalidate filesystem root") from error
    _verify_directory(root_bound, root_identity, "filesystem root pathname")

    _verify_directory(os.fstat(data_fd), data_identity, "data container")
    _verify_directory(
        _stat_bound(DATA_NAME, root_fd, "data container pathname"),
        data_identity,
        "data container pathname",
    )
    _verify_directory(
        os.fstat(dev_parent_fd), dev_parent_identity, "dev data parent"
    )
    _verify_directory(
        _stat_bound(DEV_PARENT_NAME, data_fd, "dev data parent pathname"),
        dev_parent_identity,
        "dev data parent pathname",
    )
    _verify_directory(os.fstat(app_fd), app_identity, "backend checkout")
    _verify_directory(
        _stat_bound(APP_NAME, dev_parent_fd, "backend checkout pathname"),
        app_identity,
        "backend checkout pathname",
    )
    _verify_lock(os.fstat(lock_fd), lock_identity, "dev mutation lock")
    _verify_lock(
        _stat_bound(LOCK_NAME, dev_parent_fd, "dev mutation lock pathname"),
        lock_identity,
        "dev mutation lock pathname",
    )


def _close_all(*descriptors: int | None) -> None:
    for descriptor in reversed(descriptors):
        if descriptor is not None:
            os.close(descriptor)


def _validate_release_sha(expected_release_sha: str) -> None:
    if not RELEASE_SHA_RE.fullmatch(expected_release_sha):
        raise HardeningBlocked("expected release SHA must be lowercase 40-character Git SHA")


def _open_state(
    expected_release_sha: str,
) -> tuple[tuple[int, int, int, int, int], dict[str, Any]]:
    _validate_release_sha(expected_release_sha)
    root_fd = _open_directory(ROOT_PATH)
    data_fd = dev_parent_fd = app_fd = lock_fd = None
    try:
        root = os.fstat(root_fd)
        _require_root(root)
        data_fd = _open_child(root_fd, DATA_NAME, "data container")
        data = os.fstat(data_fd)
        _require_data(data)
        dev_parent_fd = _open_child(data_fd, DEV_PARENT_NAME, "dev data parent")
        dev_parent = os.fstat(dev_parent_fd)
        _require_dev_parent(dev_parent)
        app_fd = _open_child(dev_parent_fd, APP_NAME, "backend checkout")
        app = os.fstat(app_fd)
        _require_app(app)
        lock_fd = _open_lock(dev_parent_fd)
        lock = os.fstat(lock_fd)

        identities = {
            "root_identity": _identity(root),
            "data_identity": _identity(data),
            "dev_parent_identity": _identity(dev_parent),
            "app_identity": _identity(app),
            "lock_identity": _identity(lock),
        }
        _verify_bindings(
            root_fd,
            data_fd,
            dev_parent_fd,
            app_fd,
            lock_fd,
            **identities,
        )
        context = {
            "schema_version": 1,
            "target": "dev",
            "path": str(DATA_PATH),
            "dev_parent_path": str(DATA_PATH / DEV_PARENT_NAME),
            "app_path": str(DATA_PATH / DEV_PARENT_NAME / APP_NAME),
            "lock_path": str(DATA_PATH / DEV_PARENT_NAME / LOCK_NAME),
            "expected_release_sha": expected_release_sha,
            "target_mode": f"{TARGET_MODE:04o}",
            **_metadata(root, "root"),
            **_metadata(data, "data"),
            **_metadata(dev_parent, "dev_parent"),
            **_metadata(app, "app"),
            **_metadata(lock, "lock"),
            "lock_nlink": lock.st_nlink,
            "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        return (
            (root_fd, data_fd, dev_parent_fd, app_fd, lock_fd),
            {**context, "aggregate_sha256": _canonical_digest(context)},
        )
    except Exception:
        _close_all(root_fd, data_fd, dev_parent_fd, app_fd, lock_fd)
        raise


def audit(expected_release_sha: str) -> dict[str, Any]:
    descriptors, context = _open_state(expected_release_sha)
    try:
        context["status"] = (
            "already_sticky"
            if context["data_mode"] == f"{TARGET_MODE:04o}"
            else "requires_hardening"
        )
        return context
    finally:
        _close_all(*descriptors)


def _identity_from_context(context: dict[str, Any], prefix: str):
    return (
        context[f"{prefix}_device"],
        context[f"{prefix}_inode"],
        context[f"{prefix}_owner_uid"],
        context[f"{prefix}_group_gid"],
        int(context[f"{prefix}_mode"], 8),
    )


def _run_exact_chmod() -> None:
    command = [
        SUDO_PATH,
        "-n",
        CHMOD_PATH,
        "1777",
        "--",
        str(DATA_PATH),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        raise HardeningBlocked("cannot invoke exact allowlisted chmod") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "no diagnostic"
        raise HardeningBlocked(f"exact allowlisted chmod failed: {detail}")


def apply(
    expected_digest: str,
    confirmation: str,
    expected_release_sha: str,
) -> dict[str, Any]:
    if not SHA256_RE.fullmatch(expected_digest):
        raise HardeningBlocked("expected aggregate digest must be lowercase SHA-256")
    if confirmation != APPLY_CONFIRMATION:
        raise HardeningBlocked("apply confirmation is invalid")

    descriptors, before = _open_state(expected_release_sha)
    root_fd, data_fd, dev_parent_fd, app_fd, lock_fd = descriptors
    try:
        if before["aggregate_sha256"] != expected_digest:
            raise HardeningBlocked("current host state does not match the reviewed audit")
        identities = {
            f"{prefix}_identity": _identity_from_context(before, prefix)
            for prefix in ("root", "data", "dev_parent", "app", "lock")
        }
        _verify_bindings(*descriptors, **identities)

        if int(before["data_mode"], 8) == TARGET_MODE:
            return {
                "status": "already_sticky",
                "path": str(DATA_PATH),
                "before_mode": before["data_mode"],
                "after_mode": f"{TARGET_MODE:04o}",
                "aggregate_sha256": expected_digest,
                "owner_uid": before["data_owner_uid"],
                "group_gid": before["data_group_gid"],
            }

        # If a writable-child rename races this point, chmod still establishes
        # sticky protection on /data. The identity postcheck then fails closed
        # and deliberately leaves the safer 1777 mode in place.
        _failure_point("before_sticky_guard", {"path": str(DATA_PATH)})
        _run_exact_chmod()
        os.fsync(data_fd)
        _failure_point("after_sticky_guard", {"path": str(DATA_PATH)})

        guarded_data = (
            identities["data_identity"][0],
            identities["data_identity"][1],
            identities["data_identity"][2],
            identities["data_identity"][3],
            TARGET_MODE,
        )
        _verify_bindings(
            root_fd,
            data_fd,
            dev_parent_fd,
            app_fd,
            lock_fd,
            root_identity=identities["root_identity"],
            data_identity=guarded_data,
            dev_parent_identity=identities["dev_parent_identity"],
            app_identity=identities["app_identity"],
            lock_identity=identities["lock_identity"],
        )
        guarded = os.fstat(data_fd)
        return {
            "status": "hardened",
            "path": str(DATA_PATH),
            "before_mode": before["data_mode"],
            "after_mode": f"{TARGET_MODE:04o}",
            "aggregate_sha256": expected_digest,
            "owner_uid": guarded.st_uid,
            "group_gid": guarded.st_gid,
            "dev_parent_device": identities["dev_parent_identity"][0],
            "dev_parent_inode": identities["dev_parent_identity"][1],
        }
    finally:
        _close_all(*descriptors)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("audit", "apply"))
    parser.add_argument("--expected-aggregate-sha256", default="")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--expected-release-sha", required=True)
    arguments = parser.parse_args()
    try:
        if arguments.mode == "audit":
            if arguments.expected_aggregate_sha256 or arguments.confirmation:
                raise HardeningBlocked("audit does not accept apply controls")
            result = audit(arguments.expected_release_sha)
        else:
            result = apply(
                arguments.expected_aggregate_sha256,
                arguments.confirmation,
                arguments.expected_release_sha,
            )
    except HardeningBlocked as error:
        print(f"hardening blocked: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
