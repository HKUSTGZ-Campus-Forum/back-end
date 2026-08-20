#!/usr/bin/env python3
"""Emit a secret-free, exact PostgreSQL consistency snapshot as JSON."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import Config


def snapshot(expected_database: str) -> dict[str, object]:
    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    if url.get_backend_name() != "postgresql" or url.database != expected_database:
        raise RuntimeError("configured database does not match --expected-database")

    engine = create_engine(
        Config.SQLALCHEMY_DATABASE_URI,
        **Config.SQLALCHEMY_ENGINE_OPTIONS,
    )
    quote = engine.dialect.identifier_preparer.quote_identifier
    table_counts: dict[str, int] = {}

    with engine.connect() as connection:
        actual_database = connection.execute(
            text("SELECT current_database()")
        ).scalar_one()
        if actual_database != expected_database:
            raise RuntimeError("connected database does not match --expected-database")

        tables = connection.execute(text("""
            SELECT schemaname, tablename
              FROM pg_catalog.pg_tables
             WHERE schemaname <> 'information_schema'
               AND schemaname NOT LIKE 'pg_%'
             ORDER BY schemaname, tablename
        """)).all()
        for schema, table in tables:
            count = connection.execute(
                text(f"SELECT count(*) FROM {quote(schema)}.{quote(table)}")
            ).scalar_one()
            table_counts[f"{schema}.{table}"] = int(count)

        alembic_heads: list[str] = []
        if "public.alembic_version" in table_counts:
            alembic_heads = [
                str(row[0])
                for row in connection.execute(
                    text("SELECT version_num FROM public.alembic_version ORDER BY version_num")
                ).all()
            ]

        constraint_counts = connection.execute(text("""
            SELECT count(*) FILTER (WHERE contype = 'f') AS foreign_keys,
                   count(*) FILTER (WHERE contype = 'f' AND NOT convalidated)
                       AS unvalidated_foreign_keys
              FROM pg_catalog.pg_constraint
             WHERE connamespace IN (
                       SELECT oid
                         FROM pg_catalog.pg_namespace
                        WHERE nspname <> 'information_schema'
                          AND nspname NOT LIKE 'pg_%'
                   )
        """)).one()

        extensions = [
            str(row[0])
            for row in connection.execute(
                text("SELECT extname FROM pg_catalog.pg_extension ORDER BY extname")
            ).all()
        ]
        server_version_num = str(
            connection.execute(text("SHOW server_version_num")).scalar_one()
        )

    return {
        "format": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": expected_database,
        "server_version_num": server_version_num,
        "alembic_heads": alembic_heads,
        "extensions": extensions,
        "foreign_keys": int(constraint_counts.foreign_keys),
        "unvalidated_foreign_keys": int(
            constraint_counts.unvalidated_foreign_keys
        ),
        "table_counts": table_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    payload = json.dumps(
        snapshot(args.expected_database),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    if args.output:
        with open(args.output, "x", encoding="utf-8") as output:
            output.write(payload)
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
