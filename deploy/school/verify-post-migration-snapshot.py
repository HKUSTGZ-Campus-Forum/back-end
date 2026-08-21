#!/usr/bin/env python3
"""Verify a restored source snapshot after target-release Alembic upgrades."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def load_snapshot(path: str) -> dict:
    with Path(path).open(encoding="utf-8") as source:
        payload = json.load(source)
    if payload.get("format") != 1 or not isinstance(payload.get("table_counts"), dict):
        raise RuntimeError(f"invalid database snapshot: {path}")
    return payload


def repository_heads(migrations_dir: str) -> list[str]:
    directory = Path(migrations_dir).resolve(strict=True)
    if not directory.is_dir():
        raise RuntimeError("migrations path is not a directory")
    revisions: set[str] = set()
    down_revisions: set[str] = set()
    for migration in sorted((directory / "versions").glob("*.py")):
        assignments: dict[str, object] = {}
        tree = ast.parse(migration.read_text(encoding="utf-8"), filename=str(migration))
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                assignments[target.id] = ast.literal_eval(node.value)
        revision = assignments.get("revision")
        if revision is None:
            continue
        if not isinstance(revision, str) or revision in revisions:
            raise RuntimeError(f"invalid or duplicate revision in {migration}")
        revisions.add(revision)
        down_revision = assignments.get("down_revision")
        if isinstance(down_revision, str):
            down_revisions.add(down_revision)
        elif isinstance(down_revision, (tuple, list)):
            if not all(isinstance(item, str) for item in down_revision):
                raise RuntimeError(f"invalid down_revision in {migration}")
            down_revisions.update(down_revision)
        elif down_revision is not None:
            raise RuntimeError(f"invalid down_revision in {migration}")
    heads = sorted(revisions - down_revisions)
    if not revisions or not heads:
        raise RuntimeError("could not determine repository Alembic heads")
    return heads


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--migrations-dir", required=True)
    args = parser.parse_args()

    source = load_snapshot(args.source)
    target = load_snapshot(args.target)
    expected_heads = repository_heads(args.migrations_dir)
    source_counts = source["table_counts"]
    target_counts = target["table_counts"]
    mismatches: list[str] = []

    for table, source_count in sorted(source_counts.items()):
        target_count = target_counts.get(table)
        if target_count != source_count:
            mismatches.append(
                f"source table row count {table}: source={source_count} target={target_count}"
            )
    for table in sorted(set(target_counts) - set(source_counts)):
        if target_counts[table] != 0:
            mismatches.append(
                f"target-only migration table is not empty: {table}={target_counts[table]}"
            )
    if target.get("alembic_heads") != expected_heads:
        mismatches.append(
            "target Alembic heads do not match release: "
            f"target={target.get('alembic_heads')} release={expected_heads}"
        )
    if source.get("extensions") != target.get("extensions"):
        mismatches.append(
            "extension sets changed during target migration: "
            f"source={source.get('extensions')} target={target.get('extensions')}"
        )
    if target.get("unvalidated_foreign_keys") != 0:
        mismatches.append(
            "target has unvalidated foreign keys: "
            f"{target.get('unvalidated_foreign_keys')}"
        )
    if target.get("foreign_keys", 0) < source.get("foreign_keys", 0):
        mismatches.append(
            "target lost foreign keys during migration: "
            f"source={source.get('foreign_keys')} target={target.get('foreign_keys')}"
        )

    if mismatches:
        for mismatch in mismatches:
            print(mismatch)
        raise SystemExit(1)
    print(
        json.dumps(
            {
                "status": "post-migration-match",
                "source_tables": len(source_counts),
                "target_tables": len(target_counts),
                "source_rows": sum(source_counts.values()),
                "alembic_heads": expected_heads,
                "foreign_keys": target.get("foreign_keys", 0),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
