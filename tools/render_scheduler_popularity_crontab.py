"""Render one fail-closed managed scheduler-popularity crontab block."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


BEGIN_MARKER = "# BEGIN UNIKORN SCHEDULER POPULARITY 2610 (MANAGED)"
END_MARKER = "# END UNIKORN SCHEDULER POPULARITY 2610 (MANAGED)"


class ManagedBlockError(ValueError):
    """The existing or replacement managed block is ambiguous."""


_CRON_TZ_ASSIGNMENT = re.compile(r"^[ \t]*CRON_TZ[ \t]*=")


def _reject_external_cron_timezone(
    lines: list[str], indices: tuple[int, int] | None
) -> None:
    """Reject an active CRON_TZ directive outside the owned block.

    Cron environment assignments apply to subsequent jobs. Preserving an
    operator-owned CRON_TZ line could therefore silently move the fixed
    Asia/Shanghai terminal sample. The deploy separately verifies the host
    timezone, so activation fails closed instead of rewriting unrelated jobs.
    """
    begin, end = indices if indices is not None else (-1, -1)
    for index, line in enumerate(lines):
        if begin <= index <= end:
            continue
        if _CRON_TZ_ASSIGNMENT.match(line):
            raise ManagedBlockError(
                "active CRON_TZ outside the managed block would change sampling time"
            )


def _marker_indices(text: str) -> tuple[int, int] | None:
    lines = text.splitlines(keepends=True)
    begin_indices = [
        index for index, line in enumerate(lines) if line.rstrip("\r\n") == BEGIN_MARKER
    ]
    end_indices = [
        index for index, line in enumerate(lines) if line.rstrip("\r\n") == END_MARKER
    ]
    marker_fragments = sum(BEGIN_MARKER in line or END_MARKER in line for line in lines)
    if not begin_indices and not end_indices and marker_fragments == 0:
        return None
    if len(begin_indices) != 1 or len(end_indices) != 1:
        raise ManagedBlockError("managed crontab markers must each appear exactly once")
    if marker_fragments != 2:
        raise ManagedBlockError("managed crontab marker text is malformed or duplicated")
    begin, end = begin_indices[0], end_indices[0]
    if begin >= end:
        raise ManagedBlockError("managed crontab markers are out of order")
    return begin, end


def validate_block(block: str) -> str:
    if "\x00" in block or "\r" in block:
        raise ManagedBlockError("managed crontab block contains unsupported bytes")
    normalized = block if block.endswith("\n") else block + "\n"
    indices = _marker_indices(normalized)
    if indices is None:
        raise ManagedBlockError("replacement block is missing managed markers")
    lines = normalized.splitlines(keepends=True)
    if indices != (0, len(lines) - 1):
        raise ManagedBlockError("replacement block must contain only the managed block")
    return normalized


def render(existing: str, replacement: str | None) -> str:
    if "\x00" in existing:
        raise ManagedBlockError("existing crontab contains a NUL byte")
    indices = _marker_indices(existing)
    lines = existing.splitlines(keepends=True)
    if replacement is not None:
        _reject_external_cron_timezone(lines, indices)
    if indices is not None:
        begin, end = indices
        prefix = "".join(lines[:begin])
        suffix = "".join(lines[end + 1 :])
        return prefix + (validate_block(replacement) if replacement is not None else "") + suffix
    if replacement is None:
        return existing
    block = validate_block(replacement)
    separator = "" if not existing or existing.endswith("\n") else "\n"
    return existing + separator + block


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--replacement", type=Path)
    group.add_argument("--remove", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    with args.existing.open(
        "r", encoding="utf-8", errors="surrogateescape", newline=""
    ) as handle:
        existing = handle.read()
    if args.replacement:
        with args.replacement.open(
            "r", encoding="utf-8", errors="surrogateescape", newline=""
        ) as handle:
            replacement = handle.read()
    else:
        replacement = None
    output = render(existing, replacement)
    with args.output.open(
        "w", encoding="utf-8", errors="surrogateescape", newline=""
    ) as handle:
        handle.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
