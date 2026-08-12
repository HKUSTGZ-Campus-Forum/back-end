"""Create and verify a PostgreSQL custom-format backup without exposing secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from sqlalchemy.engine import make_url

from app.config import Config


DATABASE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")
LIBPQ_QUERY_ENVIRONMENT = {
    "application_name": "PGAPPNAME",
    "channel_binding": "PGCHANNELBINDING",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "fallback_application_name": "PGFALLBACKAPPNAME",
    "gssencmode": "PGGSSENCMODE",
    "host": "PGHOST",
    "hostaddr": "PGHOSTADDR",
    "keepalives": "PGKEEPALIVES",
    "keepalives_count": "PGKEEPALIVESCOUNT",
    "keepalives_idle": "PGKEEPALIVESIDLE",
    "keepalives_interval": "PGKEEPALIVESINTERVAL",
    "load_balance_hosts": "PGLOADBALANCEHOSTS",
    "passfile": "PGPASSFILE",
    "port": "PGPORT",
    "requirepeer": "PGREQUIREPEER",
    "sslcert": "PGSSLCERT",
    "sslcertmode": "PGSSLCERTMODE",
    "sslcrl": "PGSSLCRL",
    "sslcrldir": "PGSSLCRLDIR",
    "sslkey": "PGSSLKEY",
    "sslmode": "PGSSLMODE",
    "sslpassword": "PGSSLPASSWORD",
    "sslrootcert": "PGSSLROOTCERT",
    "sslsni": "PGSSLSNI",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
    "tcp_user_timeout": "PGTCPUSER_TIMEOUT",
}
class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_verified_backup(
    output_path: Path,
    *,
    expected_database: str,
    config_class=Config,
) -> dict[str, object]:
    if not DATABASE_NAME_RE.fullmatch(expected_database):
        raise BackupError("expected database name is invalid")

    url = make_url(config_class.SQLALCHEMY_DATABASE_URI)
    if url.get_backend_name() != "postgresql" or not url.database:
        raise BackupError("configured database is not PostgreSQL")
    if url.database != expected_database:
        raise BackupError(
            f"database mismatch: configured={url.database!r} expected={expected_database!r}"
        )

    destination = output_path.expanduser().resolve()
    destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if destination.exists():
        raise BackupError("backup destination already exists")

    pg_environment = os.environ.copy()
    for key in tuple(pg_environment):
        if key.startswith("PG"):
            pg_environment.pop(key)
    connection_environment = {
        "PGHOST": url.host,
        "PGPORT": str(url.port) if url.port else None,
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGDATABASE": url.database,
    }
    for key, value in connection_environment.items():
        if value is not None:
            pg_environment[key] = value

    for query_name, environment_name in LIBPQ_QUERY_ENVIRONMENT.items():
        query_value = url.query.get(query_name)
        if query_value is not None:
            pg_environment[environment_name] = str(query_value)

    connect_options = (
        (config_class.SQLALCHEMY_ENGINE_OPTIONS or {})
        .get("connect_args", {})
        .get("options")
    )
    if connect_options:
        pg_environment["PGOPTIONS"] = str(connect_options)

    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    os.chmod(temporary, 0o600)
    try:
        subprocess.run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                f"--file={temporary}",
            ],
            check=True,
            env=pg_environment,
        )
        if temporary.stat().st_size <= 0:
            raise BackupError("pg_dump created an empty archive")
        subprocess.run(
            ["pg_restore", "--list", str(temporary)],
            check=True,
            stdout=subprocess.DEVNULL,
            env=pg_environment,
        )
        size = temporary.stat().st_size
        sha256 = _sha256(temporary)
        os.link(temporary, destination)
        temporary.unlink()
        return {
            "status": "verified",
            "database": expected_database,
            "path": str(destination),
            "size": size,
            "sha256": sha256,
        }
    except subprocess.CalledProcessError as exc:
        raise BackupError(f"backup command failed with exit code {exc.returncode}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a verified PostgreSQL backup.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-database", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = create_verified_backup(
            Path(args.output),
            expected_database=args.expected_database,
        )
    except (BackupError, OSError) as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, sort_keys=True))
        raise SystemExit(1) from exc
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
