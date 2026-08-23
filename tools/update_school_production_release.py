#!/usr/bin/env python3
"""Write the only mutable file on the school-production control branch."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_OUTPUT = Path("deploy/school/school-production-release.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-sha", required=True)
    parser.add_argument("--frontend-sha", required=True)
    parser.add_argument("--database-change-approval-reference")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    for name in ("backend_sha", "frontend_sha"):
        if not SHA_PATTERN.fullmatch(getattr(arguments, name)):
            parser.error(f"--{name.replace('_', '-')} must be a lowercase full commit SHA")
    reference = arguments.database_change_approval_reference
    if reference is not None and not (
        3 <= len(reference) <= 200
        and reference == reference.strip()
        and all(character.isprintable() for character in reference)
    ):
        parser.error("database change approval reference must contain 3-200 single-line characters")
    payload = {
        "backend_sha": arguments.backend_sha,
        "database_change": {
            "approval_reference": reference,
            "approved": reference is not None,
        },
        "frontend_sha": arguments.frontend_sha,
        "schema_version": 1,
    }
    output = arguments.output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    print(f"updated {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
