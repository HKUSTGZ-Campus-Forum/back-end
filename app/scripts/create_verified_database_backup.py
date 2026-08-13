"""Create and verify a PostgreSQL custom-format backup without exposing secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
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


def _descriptor_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _stable_file_identity(details: os.stat_result) -> tuple[int, ...]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_uid,
        details.st_gid,
        details.st_mode,
        details.st_nlink,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


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

    expanded_destination = output_path.expanduser()
    if not expanded_destination.is_absolute():
        expanded_destination = Path.cwd() / expanded_destination
    destination = expanded_destination
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise BackupError("backup parent must be an existing real directory")
    parent_details = destination.parent.stat()
    if (
        parent_details.st_uid != os.geteuid()
        or parent_details.st_gid != os.getegid()
        or stat.S_IMODE(parent_details.st_mode) != 0o750
    ):
        raise BackupError("backup parent has unsafe metadata")
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
    destination_created = False
    durable = False
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
        descriptor = os.open(
            temporary,
            os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_gid != os.getegid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or before.st_size <= 0
            ):
                raise BackupError("temporary backup has unsafe metadata")
            os.fsync(descriptor)
            sha256 = _descriptor_sha256(descriptor)
            after = os.fstat(descriptor)
            if _stable_file_identity(after) != _stable_file_identity(before):
                raise BackupError("temporary backup changed while it was verified")
            size = after.st_size
            os.link(temporary, destination)
            destination_created = True
            temporary.unlink()
            published_before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(published_before.st_mode)
                or published_before.st_uid != os.geteuid()
                or published_before.st_gid != os.getegid()
                or stat.S_IMODE(published_before.st_mode) != 0o600
                or published_before.st_nlink != 1
                or published_before.st_size != size
            ):
                raise BackupError("published backup has unsafe metadata")
            published_sha256 = _descriptor_sha256(descriptor)
            published_after = os.fstat(descriptor)
            if _stable_file_identity(published_after) != _stable_file_identity(
                published_before
            ):
                raise BackupError("published backup changed while it was verified")
            if published_sha256 != sha256:
                raise BackupError("published backup digest does not match")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        destination_descriptor = os.open(
            destination,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            path_details = os.fstat(destination_descriptor)
            if _stable_file_identity(path_details) != _stable_file_identity(
                published_after
            ):
                raise BackupError("published backup path identity changed")
        finally:
            os.close(destination_descriptor)
        parent_descriptor = os.open(
            destination.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
        durable = True
        return {
            "status": "verified",
            "database": expected_database,
            "path": str(destination),
            "size": size,
            "sha256": sha256,
            "device": published_after.st_dev,
            "inode": published_after.st_ino,
            "mtime_ns": published_after.st_mtime_ns,
        }
    except subprocess.CalledProcessError as exc:
        raise BackupError(f"backup command failed with exit code {exc.returncode}") from exc
    finally:
        temporary.unlink(missing_ok=True)
        if destination_created and not durable:
            destination.unlink(missing_ok=True)
            try:
                parent_descriptor = os.open(
                    destination.parent,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                )
                try:
                    os.fsync(parent_descriptor)
                finally:
                    os.close(parent_descriptor)
            except OSError:
                pass


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
