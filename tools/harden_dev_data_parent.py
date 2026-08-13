#!/usr/bin/env python3
"""Audit and harden the fixed dev data parent without following symlinks."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


DATA_PARENT = Path("/data/dev_unikorn")
APP_NAME = "back-end"
LOCK_NAME = "backend-mutations-dev.lock"
EXPECTED_MODE = 0o777
INTERMEDIATE_MODE = 0o1777
TARGET_MODE = 0o755
APPLY_CONFIRMATION = "HARDEN_DEV_DATA_PARENT_0777_TO_0755"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
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


def _open_parent() -> tuple[int, dict[str, Any]]:
    container_path = DATA_PARENT.parent
    try:
        container_fd = os.open(
            container_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError as error:
        raise HardeningBlocked("cannot safely open the fixed data container") from error
    container = os.fstat(container_fd)
    container_mode = stat.S_IMODE(container.st_mode)
    safe_non_writable_container = (
        container.st_uid in {0, os.geteuid()} and not container_mode & 0o022
    )
    safe_shared_container = (
        container.st_uid == 0
        and container.st_gid == 0
        and container_mode == 0o1777
    )
    if (
        not stat.S_ISDIR(container.st_mode)
        or not (safe_non_writable_container or safe_shared_container)
    ):
        os.close(container_fd)
        raise HardeningBlocked(
            "fixed data container has unsafe ownership or permissions: "
            f"owner_uid={container.st_uid}, group_gid={container.st_gid}, "
            f"mode={stat.S_IMODE(container.st_mode):04o}"
        )
    try:
        descriptor = os.open(
            DATA_PARENT.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=container_fd,
        )
    except OSError as error:
        os.close(container_fd)
        raise HardeningBlocked("cannot safely open the fixed dev data parent") from error
    os.close(container_fd)
    details = os.fstat(descriptor)
    mode = stat.S_IMODE(details.st_mode)
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_gid != os.getegid()
        or mode not in {EXPECTED_MODE, INTERMEDIATE_MODE, TARGET_MODE}
    ):
        os.close(descriptor)
        raise HardeningBlocked(
            "fixed dev data parent does not match the observed boundary: "
            f"owner_uid={details.st_uid}, effective_uid={os.geteuid()}, "
            f"group_gid={details.st_gid}, effective_gid={os.getegid()}, mode={mode:04o}"
        )
    return descriptor, {
        "container_path": str(container_path),
        "container_owner_uid": container.st_uid,
        "container_group_gid": container.st_gid,
        "container_mode": f"{stat.S_IMODE(container.st_mode):04o}",
        "container_device": container.st_dev,
        "container_inode": container.st_ino,
    }


def _open_app(parent_fd: int) -> tuple[int, int]:
    try:
        descriptor = os.open(
            APP_NAME,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise HardeningBlocked("cannot safely open the fixed backend checkout") from error
    details = os.fstat(descriptor)
    os.close(descriptor)
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
        raise HardeningBlocked("fixed backend checkout has unexpected ownership or type")
    return details.st_dev, details.st_ino


def _acquire_existing_lock(parent_fd: int) -> tuple[int, tuple[int, int]]:
    try:
        descriptor = os.open(
            LOCK_NAME,
            os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise HardeningBlocked("cannot safely open the existing dev mutation lock") from error
    details = os.fstat(descriptor)
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_gid != os.getegid()
        or details.st_nlink != 1
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        os.close(descriptor)
        raise HardeningBlocked("existing dev mutation lock has unsafe metadata")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise HardeningBlocked("another dev backend mutation holds the lock") from error
    bound = os.stat(LOCK_NAME, dir_fd=parent_fd, follow_symlinks=False)
    identity = (details.st_dev, details.st_ino)
    if not stat.S_ISREG(bound.st_mode) or (bound.st_dev, bound.st_ino) != identity:
        os.close(descriptor)
        raise HardeningBlocked("dev mutation lock pathname changed while opening")
    return descriptor, identity


def _context(
    parent_fd: int,
    lock_identity: tuple[int, int],
    container_context: dict[str, Any],
    *,
    current_mode: int | None = None,
) -> dict[str, Any]:
    parent = os.fstat(parent_fd)
    app_device, app_inode = _open_app(parent_fd)
    context = {
        **container_context,
        "schema_version": 1,
        "target": "dev",
        "path": str(DATA_PARENT),
        "owner_uid": parent.st_uid,
        "group_gid": parent.st_gid,
        "current_mode": f"{current_mode if current_mode is not None else stat.S_IMODE(parent.st_mode):04o}",
        "target_mode": f"{TARGET_MODE:04o}",
        "parent_device": parent.st_dev,
        "parent_inode": parent.st_ino,
        "app_device": app_device,
        "app_inode": app_inode,
        "lock_device": lock_identity[0],
        "lock_inode": lock_identity[1],
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    return {**context, "aggregate_sha256": _canonical_digest(context)}


def audit() -> dict[str, Any]:
    parent_fd, container_context = _open_parent()
    lock_fd: int | None = None
    try:
        lock_fd, lock_identity = _acquire_existing_lock(parent_fd)
        context = _context(parent_fd, lock_identity, container_context)
        context["status"] = (
            "already_hardened"
            if context["current_mode"] == f"{TARGET_MODE:04o}"
            else (
                "requires_completion"
                if context["current_mode"] == f"{INTERMEDIATE_MODE:04o}"
                else "requires_hardening"
            )
        )
        return context
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(parent_fd)


def apply(expected_digest: str, confirmation: str) -> dict[str, Any]:
    if not SHA256_RE.fullmatch(expected_digest):
        raise HardeningBlocked("expected aggregate digest must be lowercase SHA-256")
    if confirmation != APPLY_CONFIRMATION:
        raise HardeningBlocked("apply confirmation is invalid")
    parent_fd, container_context = _open_parent()
    lock_fd: int | None = None
    try:
        initial = os.fstat(parent_fd)
        initial_mode = stat.S_IMODE(initial.st_mode)
        if initial_mode == TARGET_MODE:
            raise HardeningBlocked("fixed dev data parent is already hardened")
        lock_fd, lock_identity = _acquire_existing_lock(parent_fd)
        before = _context(
            parent_fd,
            lock_identity,
            container_context,
            current_mode=initial_mode,
        )
        if before["aggregate_sha256"] != expected_digest:
            raise HardeningBlocked("current host state does not match the reviewed audit")

        if initial_mode == EXPECTED_MODE:
            # Establish rename protection before trusting any child pathname.
            # Holding the reviewed lock coordinates trusted workflows; the
            # post-guard inode checks below reject any untrusted rename race.
            os.fchmod(parent_fd, INTERMEDIATE_MODE)
            os.fsync(parent_fd)
            guarded = os.fstat(parent_fd)
            if (
                guarded.st_uid != initial.st_uid
                or guarded.st_gid != initial.st_gid
                or guarded.st_dev != initial.st_dev
                or guarded.st_ino != initial.st_ino
                or stat.S_IMODE(guarded.st_mode) != INTERMEDIATE_MODE
            ):
                raise HardeningBlocked("could not establish the sticky parent guard")
            _failure_point("after_sticky_guard", {"path": str(DATA_PARENT)})

        guarded_lock = os.stat(LOCK_NAME, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(guarded_lock.st_mode)
            or guarded_lock.st_uid != os.geteuid()
            or guarded_lock.st_gid != os.getegid()
            or stat.S_IMODE(guarded_lock.st_mode) != 0o600
            or (guarded_lock.st_dev, guarded_lock.st_ino) != lock_identity
        ):
            raise HardeningBlocked(
                "dev mutation lock changed before the sticky guard became authoritative"
            )
        if _open_app(parent_fd) != (before["app_device"], before["app_inode"]):
            raise HardeningBlocked(
                "fixed backend checkout changed before the sticky guard became authoritative"
            )

        os.fchmod(parent_fd, TARGET_MODE)
        os.fsync(parent_fd)
        after_details = os.fstat(parent_fd)
        if (
            after_details.st_uid != before["owner_uid"]
            or after_details.st_gid != before["group_gid"]
            or after_details.st_dev != before["parent_device"]
            or after_details.st_ino != before["parent_inode"]
            or stat.S_IMODE(after_details.st_mode) != TARGET_MODE
        ):
            raise HardeningBlocked("fixed dev data parent hardening verification failed")
        bound_lock = os.stat(LOCK_NAME, dir_fd=parent_fd, follow_symlinks=False)
        if (bound_lock.st_dev, bound_lock.st_ino) != lock_identity:
            raise HardeningBlocked("dev mutation lock pathname changed during hardening")
        if _open_app(parent_fd) != (before["app_device"], before["app_inode"]):
            raise HardeningBlocked("fixed backend checkout changed during hardening")
        return {
            "status": "hardened",
            "path": str(DATA_PARENT),
            "before_mode": before["current_mode"],
            "after_mode": f"{TARGET_MODE:04o}",
            "aggregate_sha256": expected_digest,
            "owner_uid": after_details.st_uid,
            "group_gid": after_details.st_gid,
        }
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(parent_fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("audit", "apply"))
    parser.add_argument("--expected-aggregate-sha256", default="")
    parser.add_argument("--confirmation", default="")
    arguments = parser.parse_args()
    try:
        if arguments.mode == "audit":
            if arguments.expected_aggregate_sha256 or arguments.confirmation:
                raise HardeningBlocked("audit does not accept apply controls")
            result = audit()
        else:
            result = apply(arguments.expected_aggregate_sha256, arguments.confirmation)
    except HardeningBlocked as error:
        print(f"hardening blocked: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
