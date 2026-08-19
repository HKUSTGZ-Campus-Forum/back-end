#!/usr/bin/env python3
"""Normalize permissions for Git-tracked runtime files after deployment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys


class PermissionNormalizationBlocked(RuntimeError):
    """The checkout does not match the safe normalization boundary."""


def _tracked_entries(repository: Path) -> list[tuple[int, str]]:
    result = subprocess.run(
        ["git", "-C", str(repository), "ls-files", "--stage", "-z"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise PermissionNormalizationBlocked("cannot enumerate tracked files")
    entries = []
    for raw_entry in result.stdout.split(b"\0"):
        if not raw_entry:
            continue
        try:
            metadata, raw_path = raw_entry.split(b"\t", 1)
            raw_mode, _object_id, raw_stage = metadata.split(b" ", 2)
            path = raw_path.decode("utf-8")
            mode = int(raw_mode, 8)
        except (UnicodeDecodeError, ValueError) as error:
            raise PermissionNormalizationBlocked("invalid Git index entry") from error
        if raw_stage != b"0" or mode not in {0o100644, 0o100755}:
            raise PermissionNormalizationBlocked(
                "tracked entry is staged, unmerged, or not a regular file"
            )
        pure_path = PurePosixPath(path)
        if pure_path.is_absolute() or ".." in pure_path.parts or path.startswith(".git/"):
            raise PermissionNormalizationBlocked("tracked path escapes the checkout")
        entries.append((mode, path))
    if not entries:
        raise PermissionNormalizationBlocked("Git index contains no tracked files")
    return entries


def normalize(repository: Path) -> dict[str, int | str]:
    if repository.is_symlink():
        raise PermissionNormalizationBlocked("repository path must not be a symlink")
    repository = repository.resolve(strict=True)
    details = repository.stat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
        raise PermissionNormalizationBlocked("repository ownership or type is invalid")
    if (
        subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--show-toplevel"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        != str(repository)
    ):
        raise PermissionNormalizationBlocked("repository is not the Git toplevel")

    entries = _tracked_entries(repository)
    directories = {repository}
    files_changed = 0
    for git_mode, relative_path in entries:
        path = repository / relative_path
        file_details = path.lstat()
        if not stat.S_ISREG(file_details.st_mode) or file_details.st_uid != os.geteuid():
            raise PermissionNormalizationBlocked(
                f"tracked file ownership or type is invalid: {relative_path}"
            )
        desired_mode = 0o755 if git_mode == 0o100755 else 0o644
        if stat.S_IMODE(file_details.st_mode) != desired_mode:
            os.chmod(path, desired_mode, follow_symlinks=False)
            files_changed += 1
        parent = path.parent
        while parent != repository:
            if repository not in parent.parents:
                raise PermissionNormalizationBlocked("tracked parent escapes the checkout")
            directories.add(parent)
            parent = parent.parent

    directories_changed = 0
    for directory in sorted(directories, key=lambda item: len(item.parts)):
        directory_details = directory.stat()
        if (
            not stat.S_ISDIR(directory_details.st_mode)
            or directory_details.st_uid != os.geteuid()
        ):
            raise PermissionNormalizationBlocked("tracked directory metadata is invalid")
        if stat.S_IMODE(directory_details.st_mode) != 0o755:
            os.chmod(directory, 0o755)
            directories_changed += 1

    return {
        "status": "normalized",
        "repository": str(repository),
        "tracked_files": len(entries),
        "files_changed": files_changed,
        "directories_changed": directories_changed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        result = normalize(arguments.repository)
    except PermissionNormalizationBlocked as error:
        print(f"permission normalization blocked: {error}", file=sys.stderr)
        return 2
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
