import hashlib
import json
import os
from pathlib import Path

import pytest

from tools import production_deploy_journal as journal


TARGET_SHA = "b" * 40
OLD_SHA = "a" * 40


@pytest.fixture()
def journal_roots(tmp_path, monkeypatch):
    operations_root = tmp_path / "operations"
    operations_root.mkdir(mode=0o700)
    backup_root = tmp_path / "backups"
    backup_root.mkdir(mode=0o750)
    monkeypatch.setattr(journal, "JOURNAL_ROOT", operations_root / "api-deploy")
    monkeypatch.setattr(journal, "BACKUP_ROOT", backup_root)
    monkeypatch.setattr(
        journal,
        "BACKUP_RE",
        journal.re.compile(
            r"^prod_unikorn-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\.dump$"
        ),
    )
    return operations_root, backup_root


def _backup(backup_root: Path, payload: bytes = b"verified backup") -> Path:
    path = backup_root / f"prod_unikorn-20260813T010203Z-{TARGET_SHA[:12]}.dump"
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _advance_to_backup(backup_root: Path) -> tuple[Path, bytes]:
    payload = b"verified backup"
    backup = _backup(backup_root, payload)
    for phase in ("SERVICE_STOP_REQUESTED", "SERVICE_STOPPED"):
        journal.advance(TARGET_SHA, phase)
    journal.advance(
        TARGET_SHA,
        "FINAL_BACKUP_STARTED",
        backup_name=backup.name,
    )
    details = backup.stat()
    journal.advance(
        TARGET_SHA,
        "FINAL_BACKUP_VERIFIED",
        backup_path=str(backup),
        backup_size=len(payload),
        backup_sha256=hashlib.sha256(payload).hexdigest(),
        backup_device=details.st_dev,
        backup_inode=details.st_ino,
        backup_mtime_ns=details.st_mtime_ns,
        database_name="prod_unikorn",
        database_system_identifier="7483928182746123456",
    )
    return backup, payload


def test_journal_advances_exactly_and_same_target_resume_is_idempotent(journal_roots):
    operations_root, backup_root = journal_roots

    prepared = journal.prepare(TARGET_SHA, OLD_SHA)
    assert prepared["phase"] == "PREPARED"
    assert prepared["resumed"] is False
    assert (operations_root / "api-deploy").stat().st_mode & 0o777 == 0o700
    assert (operations_root / "api-deploy" / "ACTIVE.json").stat().st_mode & 0o777 == 0o600

    resumed = journal.prepare(TARGET_SHA, "c" * 40)
    assert resumed["resumed"] is True
    assert resumed["old_sha"] == OLD_SHA

    with pytest.raises(journal.JournalError, match="exactly one step"):
        journal.advance(TARGET_SHA, "SERVICE_STOPPED")

    _advance_to_backup(backup_root)
    for phase in journal.PHASES[5:]:
        committed = journal.advance(TARGET_SHA, phase)
    assert committed["phase"] == "COMMITTED"

    same_target = journal.prepare(TARGET_SHA, "c" * 40)
    assert same_target["phase"] == "COMMITTED"
    assert same_target["old_sha"] == OLD_SHA
    assert not (operations_root / "api-deploy" / "archive").exists()


def test_new_target_archives_committed_transaction(journal_roots):
    operations_root, backup_root = journal_roots
    journal.prepare(TARGET_SHA, OLD_SHA)
    _advance_to_backup(backup_root)
    for phase in journal.PHASES[5:]:
        committed = journal.advance(TARGET_SHA, phase)

    replacement = journal.prepare("c" * 40, TARGET_SHA)

    archive = operations_root / "api-deploy" / "archive"
    archived = list(archive.glob("*-committed.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text())["transaction_id"] == committed["transaction_id"]
    assert replacement["phase"] == "PREPARED"
    assert replacement["old_sha"] == TARGET_SHA


def test_incomplete_different_target_and_forward_abort_are_blocked(journal_roots):
    _operations_root, backup_root = journal_roots
    journal.prepare(TARGET_SHA, OLD_SHA)
    with pytest.raises(journal.JournalError, match="different target"):
        journal.prepare("c" * 40, OLD_SHA)

    _advance_to_backup(backup_root)
    for phase in (
        "CHECKOUT_ACTIVATION_REQUESTED",
        "CANDIDATE_CHECKED_OUT",
        "MIGRATION_STARTED",
    ):
        journal.advance(TARGET_SHA, phase)
    with pytest.raises(journal.JournalError, match="cannot abort"):
        journal.archive_aborted(TARGET_SHA)


def test_pre_migration_abort_is_archived(journal_roots):
    operations_root, _backup_root = journal_roots
    prepared = journal.prepare(TARGET_SHA, OLD_SHA)

    result = journal.archive_aborted(TARGET_SHA)

    assert result["outcome"] == "aborted"
    assert not (operations_root / "api-deploy" / "ACTIVE.json").exists()
    archived = operations_root / "api-deploy" / "archive" / (
        f"{prepared['transaction_id']}-aborted.json"
    )
    assert archived.is_file()


def test_backup_verification_detects_content_and_metadata_changes(journal_roots):
    _operations_root, backup_root = journal_roots
    journal.prepare(TARGET_SHA, OLD_SHA)
    backup, payload = _advance_to_backup(backup_root)

    result = journal.verify_backup(TARGET_SHA)
    assert result["backup_size"] == len(payload)

    backup.write_bytes(b"tampered payload")
    with pytest.raises(journal.JournalError, match="metadata|digest"):
        journal.verify_backup(TARGET_SHA)

    backup.write_bytes(payload)
    backup.chmod(0o644)
    with pytest.raises(journal.JournalError, match="unsafe metadata"):
        journal.verify_backup(TARGET_SHA)


def test_backup_verification_rejects_hardlink_and_changed_identity(journal_roots):
    _operations_root, backup_root = journal_roots
    journal.prepare(TARGET_SHA, OLD_SHA)
    backup, _payload = _advance_to_backup(backup_root)

    link = backup_root / "extra-link.dump"
    os.link(backup, link)
    with pytest.raises(journal.JournalError, match="unsafe metadata"):
        journal.verify_backup(TARGET_SHA)
    link.unlink()

    original = backup.read_bytes()
    backup.unlink()
    backup.write_bytes(original)
    backup.chmod(0o600)
    with pytest.raises(journal.JournalError, match="unsafe metadata"):
        journal.verify_backup(TARGET_SHA)


def test_final_backup_transition_requires_exact_database_identity(journal_roots):
    _operations_root, backup_root = journal_roots
    journal.prepare(TARGET_SHA, OLD_SHA)
    for phase in ("SERVICE_STOP_REQUESTED", "SERVICE_STOPPED"):
        journal.advance(TARGET_SHA, phase)
    backup = _backup(backup_root)
    journal.advance(
        TARGET_SHA,
        "FINAL_BACKUP_STARTED",
        backup_name=backup.name,
    )
    details = backup.stat()
    with pytest.raises(journal.JournalError, match="identity are required"):
        journal.advance(
            TARGET_SHA,
            "FINAL_BACKUP_VERIFIED",
            backup_path=str(backup),
            backup_size=details.st_size,
            backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
            backup_device=details.st_dev,
            backup_inode=details.st_ino,
            backup_mtime_ns=details.st_mtime_ns,
            database_name="prod_unikorn",
        )


def test_final_backup_start_requires_target_bound_planned_name(journal_roots):
    _operations_root, _backup_root = journal_roots
    journal.prepare(TARGET_SHA, OLD_SHA)
    journal.advance(TARGET_SHA, "SERVICE_STOP_REQUESTED")
    journal.advance(TARGET_SHA, "SERVICE_STOPPED")
    with pytest.raises(journal.JournalError, match="planned backup name"):
        journal.advance(TARGET_SHA, "FINAL_BACKUP_STARTED")
    with pytest.raises(journal.JournalError, match="planned backup name"):
        journal.advance(
            TARGET_SHA,
            "FINAL_BACKUP_STARTED",
            backup_name=f"prod_unikorn-20260813T010203Z-{'c' * 12}.dump",
        )


def test_journal_schema_rejects_missing_or_extra_database_identity(journal_roots):
    operations_root, backup_root = journal_roots
    journal.prepare(TARGET_SHA, OLD_SHA)
    _advance_to_backup(backup_root)
    active = operations_root / "api-deploy" / "ACTIVE.json"
    payload = json.loads(active.read_text())
    payload.pop("database_system_identifier")
    active.write_text(json.dumps(payload))
    active.chmod(0o600)
    with pytest.raises(journal.JournalError, match="identity are incomplete"):
        journal.show(TARGET_SHA)

    payload["database_system_identifier"] = "7483928182746123456"
    payload["unexpected"] = "field"
    active.write_text(json.dumps(payload))
    active.chmod(0o600)
    with pytest.raises(journal.JournalError, match="fields are not exact"):
        journal.show(TARGET_SHA)


def test_journal_rejects_symlinked_active_file(journal_roots):
    operations_root, _backup_root = journal_roots
    root = operations_root / "api-deploy"
    root.mkdir(mode=0o700)
    target = operations_root / "forged.json"
    target.write_text("{}")
    os.symlink(target, root / "ACTIVE.json")

    with pytest.raises(journal.JournalError, match="safely open"):
        journal.show()


def test_inspect_distinguishes_absence_from_corrupt_or_unsafe_journal(journal_roots):
    operations_root, _backup_root = journal_roots
    assert journal.inspect() == {"status": "absent"}

    journal.prepare(TARGET_SHA, OLD_SHA)
    inspected = journal.inspect()
    assert inspected["status"] == "active"
    assert inspected["journal"]["target_sha"] == TARGET_SHA

    active = operations_root / "api-deploy" / "ACTIVE.json"
    active.write_text("not-json")
    active.chmod(0o600)
    with pytest.raises(journal.JournalError, match="valid JSON"):
        journal.inspect()
