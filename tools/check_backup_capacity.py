"""Fail-closed capacity check for production database backups."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


class CapacityCheckError(ValueError):
    """The capacity request or target cannot be verified safely."""


def parse_nonnegative_decimal(raw: str, *, field: str) -> int:
    if not raw or not raw.isascii() or not raw.isdecimal():
        raise CapacityCheckError(f"{field} must be an ASCII non-negative decimal integer")
    return int(raw, 10)


def required_capacity(*, payload_bytes: int, reserve_bytes: int) -> int:
    if payload_bytes < 0 or reserve_bytes < 0:
        raise CapacityCheckError("capacity values must be non-negative")
    return payload_bytes + reserve_bytes


def available_capacity(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        stats = os.fstatvfs(fd)
        return stats.f_bavail * stats.f_frsize
    finally:
        os.close(fd)


def check_capacity(
    *, path: Path, payload_bytes: int, reserve_bytes: int, purpose: str
) -> dict[str, int | str]:
    available_bytes = available_capacity(path)
    required_bytes = required_capacity(
        payload_bytes=payload_bytes, reserve_bytes=reserve_bytes
    )
    if available_bytes < required_bytes:
        raise CapacityCheckError(
            f"Insufficient disk for {purpose}: "
            f"available={available_bytes}, required={required_bytes}."
        )
    return {
        "available_bytes": available_bytes,
        "path": str(path),
        "required_bytes": required_bytes,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--payload-bytes", required=True)
    parser.add_argument("--reserve-bytes", required=True)
    parser.add_argument("--purpose", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = check_capacity(
            path=args.path,
            payload_bytes=parse_nonnegative_decimal(
                args.payload_bytes, field="payload-bytes"
            ),
            reserve_bytes=parse_nonnegative_decimal(
                args.reserve_bytes, field="reserve-bytes"
            ),
            purpose=args.purpose,
        )
    except (CapacityCheckError, OSError) as exc:
        print(f"Production backup capacity check failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
