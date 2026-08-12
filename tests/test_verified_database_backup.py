from pathlib import Path

import pytest

from app.scripts import create_verified_database_backup as backup


class TestConfig:
    SQLALCHEMY_DATABASE_URI = "postgresql://db-user:db-password@db.example.test:5433/test_db"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"options": "-csearch_path=test_schema"}
    }


class SocketConfig:
    SQLALCHEMY_DATABASE_URI = "postgresql:///test_db"
    SQLALCHEMY_ENGINE_OPTIONS = {}


def test_backup_uses_argv_and_environment_without_logging_credentials(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == "pg_dump":
            output = Path(next(item.split("=", 1)[1] for item in argv if item.startswith("--file=")))
            output.write_bytes(b"PGDMP test archive")
        return None

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    destination = tmp_path / "backup.dump"

    result = backup.create_verified_backup(
        destination,
        expected_database="test_db",
        config_class=TestConfig,
    )

    assert result["status"] == "verified"
    assert result["database"] == "test_db"
    assert result["size"] == len(b"PGDMP test archive")
    assert destination.read_bytes() == b"PGDMP test archive"
    assert calls[0][0][0:4] == [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
    ]
    assert calls[1][0][0:2] == ["pg_restore", "--list"]
    assert all("db-password" not in " ".join(call[0]) for call in calls)
    assert calls[0][1]["env"]["PGPASSWORD"] == "db-password"
    assert calls[0][1]["env"]["PGOPTIONS"] == "-csearch_path=test_schema"
    assert "shell" not in calls[0][1]


def test_backup_refuses_wrong_database_before_running_commands(monkeypatch, tmp_path):
    monkeypatch.setattr(
        backup.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("backup command must not run"),
    )

    with pytest.raises(backup.BackupError, match="database mismatch"):
        backup.create_verified_backup(
            tmp_path / "backup.dump",
            expected_database="prod_unikorn",
            config_class=TestConfig,
        )


def test_backup_refuses_to_overwrite_existing_archive(tmp_path):
    destination = tmp_path / "backup.dump"
    destination.write_bytes(b"existing")

    with pytest.raises(backup.BackupError, match="already exists"):
        backup.create_verified_backup(
            destination,
            expected_database="test_db",
            config_class=TestConfig,
        )


def test_backup_does_not_inherit_conflicting_libpq_connection_settings(monkeypatch, tmp_path):
    captured_environments = []

    def fake_run(argv, **kwargs):
        captured_environments.append(kwargs["env"])
        if argv[0] == "pg_dump":
            output = Path(next(item.split("=", 1)[1] for item in argv if item.startswith("--file=")))
            output.write_bytes(b"PGDMP socket archive")

    inherited_libpq_names = {
        "PGHOST",
        "PGHOSTADDR",
        "PGOPTIONS",
        "PGPASSWORD",
        "PGPORT",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGUSER",
    }
    for name in inherited_libpq_names:
        monkeypatch.setenv(name, "unrelated-value")
    monkeypatch.setattr(backup.subprocess, "run", fake_run)

    backup.create_verified_backup(
        tmp_path / "socket.dump",
        expected_database="test_db",
        config_class=SocketConfig,
    )

    assert captured_environments
    for environment in captured_environments:
        assert environment["PGDATABASE"] == "test_db"
        assert not inherited_libpq_names & environment.keys()
