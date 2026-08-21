#!/usr/bin/env python3
"""Compare source and restored PostgreSQL snapshots without exposing data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: str) -> dict:
    with Path(path).open(encoding="utf-8") as source:
        payload = json.load(source)
    if payload.get("format") != 1 or not isinstance(payload.get("table_counts"), dict):
        raise RuntimeError(f"invalid database snapshot: {path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("target")
    args = parser.parse_args()
    source = load(args.source)
    target = load(args.target)

    mismatches: list[str] = []
    if source["table_counts"] != target["table_counts"]:
        all_tables = sorted(set(source["table_counts"]) | set(target["table_counts"]))
        for table in all_tables:
            left = source["table_counts"].get(table)
            right = target["table_counts"].get(table)
            if left != right:
                mismatches.append(f"row count {table}: source={left} target={right}")
    if source.get("alembic_heads") != target.get("alembic_heads"):
        mismatches.append(
            "Alembic heads differ: "
            f"source={source.get('alembic_heads')} target={target.get('alembic_heads')}"
        )
    if source.get("extensions") != target.get("extensions"):
        mismatches.append(
            "extension sets differ: "
            f"source={source.get('extensions')} target={target.get('extensions')}"
        )
    if target.get("unvalidated_foreign_keys") != 0:
        mismatches.append(
            "target has unvalidated foreign keys: "
            f"{target.get('unvalidated_foreign_keys')}"
        )
    if source.get("foreign_keys") != target.get("foreign_keys"):
        mismatches.append(
            "foreign-key counts differ: "
            f"source={source.get('foreign_keys')} target={target.get('foreign_keys')}"
        )

    if mismatches:
        for mismatch in mismatches:
            print(mismatch)
        raise SystemExit(1)
    print(
        json.dumps(
            {
                "status": "match",
                "tables": len(target["table_counts"]),
                "rows": sum(target["table_counts"].values()),
                "alembic_heads": target.get("alembic_heads", []),
                "foreign_keys": target.get("foreign_keys", 0),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
