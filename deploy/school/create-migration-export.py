#!/usr/bin/env python3
"""Create a dump and row-count snapshot from one exported PostgreSQL snapshot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import stat

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


LIBPQ_QUERY_ENVIRONMENT = {
    "application_name": "PGAPPNAME",
    "channel_binding": "PGCHANNELBINDING",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "gssencmode": "PGGSSENCMODE",
    "host": "PGHOST",
    "hostaddr": "PGHOSTADDR",
    "passfile": "PGPASSFILE",
    "port": "PGPORT",
    "requirepeer": "PGREQUIREPEER",
    "sslcert": "PGSSLCERT",
    "sslcertmode": "PGSSLCERTMODE",
    "sslcrl": "PGSSLCRL",
    "sslkey": "PGSSLKEY",
    "sslmode": "PGSSLMODE",
    "sslpassword": "PGSSLPASSWORD",
    "sslrootcert": "PGSSLROOTCERT",
    "sslsni": "PGSSLSNI",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}


def connection_environment(url, engine_options: dict) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    fixed = {
        "PGHOST": url.host,
        "PGPORT": str(url.port) if url.port else None,
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGDATABASE": url.database,
    }
    for key, value in fixed.items():
        if value is not None:
            environment[key] = value
    for query_name, environment_name in LIBPQ_QUERY_ENVIRONMENT.items():
        value = url.query.get(query_name)
        if value is not None:
            environment[environment_name] = str(value)
    options = (engine_options or {}).get("connect_args", {}).get("options")
    if options:
        environment["PGOPTIONS"] = str(options)
    return environment


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--expected-database", required=True)
    args = parser.parse_args()

    app_root = Path(args.app_root).resolve(strict=True)
    raw_output_dir = Path(args.output_dir)
    if not raw_output_dir.is_absolute() or raw_output_dir.is_symlink():
        raise SystemExit("output directory must be an absolute, real path")
    output_dir = raw_output_dir.resolve(strict=True)
    if not app_root.is_dir() or not (app_root / "app" / "config.py").is_file():
        raise SystemExit("invalid application root")
    if not output_dir.is_dir() or output_dir.is_symlink():
        raise SystemExit("invalid output directory")
    output_details = output_dir.stat()
    if (
        output_details.st_uid != os.geteuid()
        or output_details.st_gid != os.getegid()
        or stat.S_IMODE(output_details.st_mode) != 0o750
    ):
        raise SystemExit("output directory must be owned by this process with mode 0750")
    for name in ("database.dump", "database.dump.list", "source-database.json"):
        if (output_dir / name).exists():
            raise SystemExit(f"output already exists: {name}")

    sys.path.insert(0, str(app_root))
    from app.config import Config  # pylint: disable=import-outside-toplevel

    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    if url.get_backend_name() != "postgresql" or url.database != args.expected_database:
        raise SystemExit("configured database does not match --expected-database")
    engine = create_engine(
        Config.SQLALCHEMY_DATABASE_URI,
        **Config.SQLALCHEMY_ENGINE_OPTIONS,
    )
    dump_path = output_dir / "database.dump"
    list_path = output_dir / "database.dump.list"
    snapshot_path = output_dir / "source-database.json"
    temporary_dump = output_dir / ".database.dump.incomplete"
    quote = engine.dialect.identifier_preparer.quote_identifier

    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(
                    "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY DEFERRABLE"
                ))
                snapshot_id = str(
                    connection.execute(text("SELECT pg_export_snapshot()"))
                    .scalar_one()
                )
                subprocess.run(
                    [
                        "pg_dump",
                        "--format=custom",
                        "--no-owner",
                        "--no-acl",
                        f"--snapshot={snapshot_id}",
                        f"--file={temporary_dump}",
                    ],
                    check=True,
                    env=connection_environment(url, Config.SQLALCHEMY_ENGINE_OPTIONS),
                )

                table_counts: dict[str, int] = {}
                tables = connection.execute(text("""
                    SELECT schemaname, tablename
                      FROM pg_catalog.pg_tables
                     WHERE schemaname <> 'information_schema'
                       AND schemaname NOT LIKE 'pg_%'
                     ORDER BY schemaname, tablename
                """)).all()
                for schema, table in tables:
                    count = connection.execute(text(
                        f"SELECT count(*) FROM {quote(schema)}.{quote(table)}"
                    )).scalar_one()
                    table_counts[f"{schema}.{table}"] = int(count)

                alembic_heads: list[str] = []
                if "public.alembic_version" in table_counts:
                    alembic_heads = [
                        str(row[0])
                        for row in connection.execute(text(
                            "SELECT version_num FROM public.alembic_version ORDER BY version_num"
                        )).all()
                    ]
                constraints = connection.execute(text("""
                    SELECT count(*) FILTER (WHERE contype = 'f') AS foreign_keys,
                           count(*) FILTER (WHERE contype = 'f' AND NOT convalidated)
                               AS unvalidated_foreign_keys
                      FROM pg_catalog.pg_constraint
                     WHERE connamespace IN (
                               SELECT oid FROM pg_catalog.pg_namespace
                                WHERE nspname <> 'information_schema'
                                  AND nspname NOT LIKE 'pg_%'
                           )
                """)).one()
                extensions = [
                    str(row[0])
                    for row in connection.execute(text(
                        "SELECT extname FROM pg_catalog.pg_extension ORDER BY extname"
                    )).all()
                ]
                server_version_num = str(
                    connection.execute(text("SHOW server_version_num")).scalar_one()
                )
                transaction.commit()
            except Exception:
                transaction.rollback()
                raise

        if not temporary_dump.is_file() or temporary_dump.stat().st_size <= 0:
            raise RuntimeError("pg_dump did not create a non-empty custom archive")
        os.replace(temporary_dump, dump_path)
        with list_path.open("x", encoding="utf-8") as output:
            subprocess.run(
                ["pg_restore", "--list", str(dump_path)],
                check=True,
                stdout=output,
                env=connection_environment(url, Config.SQLALCHEMY_ENGINE_OPTIONS),
            )
        if list_path.stat().st_size <= 0:
            raise RuntimeError("pg_restore produced an empty list")
        payload = {
            "format": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database": args.expected_database,
            "server_version_num": server_version_num,
            "alembic_heads": alembic_heads,
            "extensions": extensions,
            "foreign_keys": int(constraints.foreign_keys),
            "unvalidated_foreign_keys": int(constraints.unvalidated_foreign_keys),
            "table_counts": table_counts,
        }
        with snapshot_path.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
        os.chmod(dump_path, 0o600)
        os.chmod(list_path, 0o600)
        os.chmod(snapshot_path, 0o600)
        print(json.dumps({
            "status": "verified",
            "database_sha256": sha256(dump_path),
            "database_list_sha256": sha256(list_path),
            "database_snapshot_sha256": sha256(snapshot_path),
            "tables": len(table_counts),
            "rows": sum(table_counts.values()),
        }, sort_keys=True))
    except Exception:
        for path in (temporary_dump, dump_path, list_path, snapshot_path):
            path.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
