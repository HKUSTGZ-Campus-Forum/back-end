import hashlib
import fcntl
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools import reconcile_dev_migration_checkout as reconciliation


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _migration(
    revision: str,
    down_revision: str | tuple[str, ...] | None,
    depends_on: str | tuple[str, ...] | None = None,
) -> bytes:
    return (
        f"revision = {revision!r}\n"
        f"down_revision = {down_revision!r}\n"
        f"depends_on = {depends_on!r}\n"
        "def upgrade():\n"
        "    raise RuntimeError('must never execute')\n"
    ).encode()


def _checkout(tmp_path: Path, monkeypatch):
    repository = tmp_path / "back-end"
    repository.mkdir()
    _git(repository, "init", "--quiet", "--initial-branch=main")
    _git(repository, "config", "user.name", "Checkout Recovery Test")
    _git(repository, "config", "user.email", "recovery@example.test")
    (repository / "README.md").write_text("tracked\n", encoding="utf-8")
    committed_migration = repository / "migrations" / "versions" / "committed.py"
    committed_migration.parent.mkdir(parents=True)
    committed_migration.write_bytes(_migration("committed_head", None))
    _git(repository, "add", "README.md")
    _git(repository, "add", "migrations/versions/committed.py")
    _git(repository, "commit", "--quiet", "-m", "initial")

    allowlist = (
        "migrations/versions/legacy-a.py",
        "migrations/versions/legacy-b.py",
    )
    payloads = {
        allowlist[0]: _migration("legacy_a", None),
        allowlist[1]: _migration("legacy_b", "legacy_a"),
    }
    for relative_path, payload in payloads.items():
        path = repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    quarantine_root = tmp_path / "quarantine" / "legacy-migrations"
    monkeypatch.setattr(reconciliation, "APP_DIR", repository)
    monkeypatch.setattr(reconciliation, "QUARANTINE_ROOT", quarantine_root)
    monkeypatch.setattr(reconciliation, "ALLOWLIST", allowlist)
    monkeypatch.setattr(reconciliation, "ALLOWLIST_SET", frozenset(allowlist))
    monkeypatch.setattr(
        reconciliation,
        "_live_database_revisions",
        lambda _repository: ["committed_head"],
    )
    return repository, quarantine_root, payloads


def test_audit_is_deterministic_and_parses_metadata_without_execution(tmp_path, monkeypatch):
    repository, _quarantine, payloads = _checkout(tmp_path, monkeypatch)

    first = reconciliation.audit(repository)
    second = reconciliation.audit(repository)

    assert first == second
    context = {key: value for key, value in first.items() if key != "aggregate_sha256"}
    assert first["aggregate_sha256"] == reconciliation.aggregate_digest(context)
    assert first["repository_sha"] == _git(repository, "rev-parse", "HEAD")
    assert [entry["path"] for entry in first["files"]] == list(payloads)
    assert [entry["revision"] for entry in first["files"]] == ["legacy_a", "legacy_b"]
    assert first["files"][1]["down_revision"] == "legacy_a"
    assert first["live_current_revisions"] == ["committed_head"]
    assert first["live_current_allowlisted_revisions"] == []
    assert first["committed_revisions"] == ["committed_head"]
    assert first["committed_heads"] == ["committed_head"]
    assert first["helper_sha256"] == hashlib.sha256(
        Path(reconciliation.__file__).read_bytes()
    ).hexdigest()
    assert first["committed_allowlisted_revision_referenced"] is False
    assert first["live_current_unknown_revisions"] == []
    assert first["files"][0]["sha256"] == hashlib.sha256(
        payloads["migrations/versions/legacy-a.py"]
    ).hexdigest()


@pytest.mark.parametrize("dirty_kind", ["tracked", "extra", "missing"])
def test_audit_rejects_every_dirty_state_except_exact_allowlist(
    tmp_path, monkeypatch, dirty_kind
):
    repository, _quarantine, _payloads = _checkout(tmp_path, monkeypatch)
    if dirty_kind == "tracked":
        (repository / "README.md").write_text("changed\n", encoding="utf-8")
    elif dirty_kind == "extra":
        (repository / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
    else:
        (repository / reconciliation.ALLOWLIST[0]).unlink()

    with pytest.raises(reconciliation.ReconciliationBlocked):
        reconciliation.audit(repository)


def test_audit_rejects_symlinks_nonregular_files_and_oversize(tmp_path, monkeypatch):
    repository, _quarantine, _payloads = _checkout(tmp_path, monkeypatch)
    first = repository / reconciliation.ALLOWLIST[0]
    first.unlink()
    first.symlink_to(repository / "README.md")
    with pytest.raises(reconciliation.ReconciliationBlocked, match="regular file"):
        reconciliation.audit(repository)

    first.unlink()
    first.mkdir()
    with pytest.raises(reconciliation.ReconciliationBlocked):
        reconciliation.audit(repository)

    first.rmdir()
    first.write_bytes(b"x" * 9)
    monkeypatch.setattr(reconciliation, "MAX_FILE_BYTES", 8)
    with pytest.raises(reconciliation.ReconciliationBlocked, match="oversized"):
        reconciliation.audit(repository)


def test_nonliteral_metadata_is_rejected_without_execution(tmp_path, monkeypatch):
    repository, _quarantine, _payloads = _checkout(tmp_path, monkeypatch)
    (repository / reconciliation.ALLOWLIST[0]).write_text(
        "revision = __import__('os').environ.clear()\ndown_revision = None\n",
        encoding="utf-8",
    )

    with pytest.raises(reconciliation.ReconciliationBlocked, match="AST literal"):
        reconciliation.audit(repository)
    assert os.environ


def test_apply_requires_reviewed_digest_and_confirmation(tmp_path, monkeypatch):
    repository, quarantine, _payloads = _checkout(tmp_path, monkeypatch)
    digest = reconciliation.audit(repository)["aggregate_sha256"]

    with pytest.raises(reconciliation.ReconciliationBlocked, match="confirmation"):
        reconciliation.apply(repository, digest, "wrong", "123")
    with pytest.raises(reconciliation.ReconciliationBlocked, match="reviewed audit"):
        reconciliation.apply(
            repository,
            "0" * 64,
            reconciliation.APPLY_CONFIRMATION,
            "123",
        )

    assert not quarantine.exists()
    assert all((repository / path).is_file() for path in reconciliation.ALLOWLIST)


def test_apply_blocks_if_live_database_current_revision_is_allowlisted(
    tmp_path, monkeypatch
):
    repository, quarantine, _payloads = _checkout(tmp_path, monkeypatch)
    audited = reconciliation.audit(repository)
    monkeypatch.setattr(
        reconciliation,
        "_live_database_revisions",
        lambda _repository: ["legacy_b"],
    )
    audited = reconciliation.audit(repository)

    with pytest.raises(reconciliation.ReconciliationBlocked, match="still identifies"):
        reconciliation.apply(
            repository,
            audited["aggregate_sha256"],
            reconciliation.APPLY_CONFIRMATION,
            "123",
        )

    assert not quarantine.exists()


def test_context_digest_changes_with_repo_head_and_live_revision_context(
    tmp_path, monkeypatch
):
    repository, _quarantine, _payloads = _checkout(tmp_path, monkeypatch)
    first = reconciliation.audit(repository)

    (repository / "README.md").write_text("new commit\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "--quiet", "-m", "advance head")
    after_head = reconciliation.audit(repository)
    assert after_head["repository_sha"] != first["repository_sha"]
    assert after_head["aggregate_sha256"] != first["aggregate_sha256"]

    after_live = reconciliation.audit(repository, live_revisions=["legacy_a"])
    assert after_live["live_current_revisions"] == ["legacy_a"]
    assert after_live["aggregate_sha256"] != after_head["aggregate_sha256"]


def test_apply_blocks_if_committed_graph_references_allowlisted_revision(
    tmp_path, monkeypatch
):
    repository, quarantine, _payloads = _checkout(tmp_path, monkeypatch)
    committed = repository / "migrations" / "versions" / "committed.py"
    committed.write_bytes(_migration("committed_head", "legacy_b"))
    _git(repository, "add", "migrations/versions/committed.py")
    _git(repository, "commit", "--quiet", "-m", "reference legacy")
    audited = reconciliation.audit(repository)
    assert audited["committed_allowlisted_revision_referenced"] is True

    with pytest.raises(reconciliation.ReconciliationBlocked, match="still references"):
        reconciliation.apply(
            repository,
            audited["aggregate_sha256"],
            reconciliation.APPLY_CONFIRMATION,
            "123",
        )
    assert not quarantine.exists()


def test_apply_blocks_if_committed_graph_depends_on_allowlisted_revision(
    tmp_path, monkeypatch
):
    repository, quarantine, _payloads = _checkout(tmp_path, monkeypatch)
    committed = repository / "migrations" / "versions" / "committed.py"
    committed.write_bytes(_migration("committed_head", None, "legacy_b"))
    _git(repository, "add", "migrations/versions/committed.py")
    _git(repository, "commit", "--quiet", "-m", "depend on legacy")
    audited = reconciliation.audit(repository)
    assert audited["committed_allowlisted_revision_referenced"] is True

    with pytest.raises(reconciliation.ReconciliationBlocked, match="still references"):
        reconciliation.apply(
            repository,
            audited["aggregate_sha256"],
            reconciliation.APPLY_CONFIRMATION,
            "123",
        )
    assert not quarantine.exists()


def test_apply_blocks_unknown_live_current_revision(tmp_path, monkeypatch):
    repository, quarantine, _payloads = _checkout(tmp_path, monkeypatch)
    monkeypatch.setattr(
        reconciliation,
        "_live_database_revisions",
        lambda _repository: ["unrecognized_live_head"],
    )
    audited = reconciliation.audit(repository)
    assert audited["live_current_unknown_revisions"] == ["unrecognized_live_head"]

    with pytest.raises(reconciliation.ReconciliationBlocked, match="outside the committed"):
        reconciliation.apply(
            repository,
            audited["aggregate_sha256"],
            reconciliation.APPLY_CONFIRMATION,
            "123",
        )
    assert not quarantine.exists()


def test_apply_moves_recoverably_writes_manifest_and_leaves_clean_tree(
    tmp_path, monkeypatch
):
    repository, quarantine, payloads = _checkout(tmp_path, monkeypatch)
    audited = reconciliation.audit(repository)

    result = reconciliation.apply(
        repository,
        audited["aggregate_sha256"],
        reconciliation.APPLY_CONFIRMATION,
        "12345",
    )

    destination = quarantine / "run-12345"
    assert result["status"] == "quarantined"
    assert result["file_count"] == len(payloads)
    assert _git(repository, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert all(not (repository / path).exists() for path in reconciliation.ALLOWLIST)
    assert all(
        (destination / "files" / Path(path).name).read_bytes() == payload
        for path, payload in payloads.items()
    )
    prepared_payload = (destination / "PREPARED.json").read_bytes()
    prepared = json.loads(prepared_payload)
    committed_payload = (destination / "COMMITTED.json").read_bytes()
    committed = json.loads(committed_payload)
    assert prepared["aggregate_sha256"] == audited["aggregate_sha256"]
    assert prepared["files"] == audited["files"]
    assert prepared["state"] == "PREPARED"
    assert committed["state"] == "COMMITTED"
    assert committed["prepared_sha256"] == hashlib.sha256(prepared_payload).hexdigest()
    assert result["prepared_sha256"] == hashlib.sha256(prepared_payload).hexdigest()
    assert result["committed_sha256"] == hashlib.sha256(committed_payload).hexdigest()
    assert destination.stat().st_mode & 0o777 == 0o700


def test_apply_rolls_back_moves_when_a_later_verification_fails(tmp_path, monkeypatch):
    repository, quarantine, payloads = _checkout(tmp_path, monkeypatch)
    audited = reconciliation.audit(repository)
    def fail_after_first_move(point, context):
        if point == "after_move" and context["moved_count"] == 1:
            raise reconciliation.ReconciliationBlocked("simulated first-move crash")

    monkeypatch.setattr(reconciliation, "FAILURE_INJECTOR", fail_after_first_move)
    with pytest.raises(reconciliation.ReconciliationBlocked, match="first-move"):
        reconciliation.apply(
            repository,
            audited["aggregate_sha256"],
            reconciliation.APPLY_CONFIRMATION,
            "98765",
        )

    assert all((repository / path).read_bytes() == payload for path, payload in payloads.items())
    assert not (quarantine / "run-98765").exists()


def test_prepared_journal_exists_before_first_move_and_failure_restores(
    tmp_path, monkeypatch
):
    repository, quarantine, payloads = _checkout(tmp_path, monkeypatch)
    audited = reconciliation.audit(repository)

    def fail_after_prepared(point, context):
        if point == "after_prepared":
            prepared = Path(context["destination"]) / "PREPARED.json"
            assert json.loads(prepared.read_text(encoding="utf-8"))["state"] == "PREPARED"
            raise reconciliation.ReconciliationBlocked("simulated after-prepared crash")

    monkeypatch.setattr(reconciliation, "FAILURE_INJECTOR", fail_after_prepared)
    with pytest.raises(reconciliation.ReconciliationBlocked, match="after-prepared"):
        reconciliation.apply(
            repository,
            audited["aggregate_sha256"],
            reconciliation.APPLY_CONFIRMATION,
            "111",
        )
    assert all((repository / path).read_bytes() == payload for path, payload in payloads.items())
    assert not (quarantine / "run-111").exists()


def test_failure_before_commit_restores_every_original_and_removes_transaction(
    tmp_path, monkeypatch
):
    repository, quarantine, payloads = _checkout(tmp_path, monkeypatch)
    audited = reconciliation.audit(repository)

    def fail_before_committed(point, _context):
        if point == "before_committed":
            raise reconciliation.ReconciliationBlocked("simulated before-commit crash")

    monkeypatch.setattr(reconciliation, "FAILURE_INJECTOR", fail_before_committed)
    with pytest.raises(reconciliation.ReconciliationBlocked, match="before-commit"):
        reconciliation.apply(
            repository,
            audited["aggregate_sha256"],
            reconciliation.APPLY_CONFIRMATION,
            "222",
        )
    assert all((repository / path).read_bytes() == payload for path, payload in payloads.items())
    assert not (quarantine / "run-222").exists()


def test_every_source_file_is_synced_before_committed_journal(
    tmp_path, monkeypatch
):
    repository, _quarantine, _payloads = _checkout(tmp_path, monkeypatch)
    audited = reconciliation.audit(repository)
    source_identities = {
        (path.stat().st_dev, path.stat().st_ino)
        for path in (repository / relative_path for relative_path in reconciliation.ALLOWLIST)
    }
    synced_source_identities = set()
    real_fsync = reconciliation.os.fsync

    def track_fsync(descriptor):
        details = os.fstat(descriptor)
        identity = (details.st_dev, details.st_ino)
        if identity in source_identities:
            synced_source_identities.add(identity)
        real_fsync(descriptor)

    def verify_before_committed(point, _context):
        if point == "before_committed":
            assert synced_source_identities == source_identities

    monkeypatch.setattr(reconciliation.os, "fsync", track_fsync)
    monkeypatch.setattr(reconciliation, "FAILURE_INJECTOR", verify_before_committed)
    result = reconciliation.apply(
        repository,
        audited["aggregate_sha256"],
        reconciliation.APPLY_CONFIRMATION,
        "223",
    )
    assert result["status"] == "quarantined"


def test_failure_after_commit_retains_durable_committed_transaction(
    tmp_path, monkeypatch
):
    repository, quarantine, payloads = _checkout(tmp_path, monkeypatch)
    audited = reconciliation.audit(repository)

    def fail_after_committed(point, _context):
        if point == "after_committed":
            raise reconciliation.ReconciliationBlocked("simulated after-commit crash")

    monkeypatch.setattr(reconciliation, "FAILURE_INJECTOR", fail_after_committed)
    with pytest.raises(reconciliation.ManualRecoveryRequired, match="committed transaction"):
        reconciliation.apply(
            repository,
            audited["aggregate_sha256"],
            reconciliation.APPLY_CONFIRMATION,
            "333",
        )
    transaction = quarantine / "run-333"
    assert json.loads((transaction / "COMMITTED.json").read_text())["state"] == "COMMITTED"
    assert all(
        (transaction / "files" / Path(path).name).read_bytes() == payload
        for path, payload in payloads.items()
    )


def test_source_reappearance_conflict_never_deletes_quarantined_original(
    tmp_path, monkeypatch
):
    repository, quarantine, payloads = _checkout(tmp_path, monkeypatch)
    audited = reconciliation.audit(repository)
    first_path = repository / reconciliation.ALLOWLIST[0]

    def create_conflict_then_fail(point, context):
        if point == "after_move" and context["moved_count"] == 1:
            first_path.write_bytes(b"conflicting replacement\n")
            raise reconciliation.ReconciliationBlocked("simulated source conflict")

    monkeypatch.setattr(reconciliation, "FAILURE_INJECTOR", create_conflict_then_fail)
    with pytest.raises(reconciliation.ManualRecoveryRequired, match="transaction retained"):
        reconciliation.apply(
            repository,
            audited["aggregate_sha256"],
            reconciliation.APPLY_CONFIRMATION,
            "444",
        )
    transaction = quarantine / "run-444"
    original = transaction / "files" / first_path.name
    assert first_path.read_bytes() == b"conflicting replacement\n"
    assert original.read_bytes() == payloads[reconciliation.ALLOWLIST[0]]
    assert (transaction / "PREPARED.json").is_file()
    assert not (transaction / "COMMITTED.json").exists()


def test_source_reappearance_at_restore_syscall_never_replaces_conflict(
    tmp_path, monkeypatch
):
    repository, quarantine, payloads = _checkout(tmp_path, monkeypatch)
    audited = reconciliation.audit(repository)
    first_path = repository / reconciliation.ALLOWLIST[0]

    def fail_then_create_late_conflict(point, context):
        if point == "after_move" and context["moved_count"] == 1:
            raise reconciliation.ReconciliationBlocked("start rollback")
        if point == "before_restore_link" and context["name"] == first_path.name:
            first_path.write_bytes(b"late conflicting replacement\n")

    monkeypatch.setattr(reconciliation, "FAILURE_INJECTOR", fail_then_create_late_conflict)
    with pytest.raises(reconciliation.ManualRecoveryRequired, match="transaction retained"):
        reconciliation.apply(
            repository,
            audited["aggregate_sha256"],
            reconciliation.APPLY_CONFIRMATION,
            "445",
        )
    original = quarantine / "run-445" / "files" / first_path.name
    assert first_path.read_bytes() == b"late conflicting replacement\n"
    assert original.read_bytes() == payloads[reconciliation.ALLOWLIST[0]]


def test_allowlist_is_exactly_the_twelve_observed_dev_paths():
    assert reconciliation.ALLOWLIST == (
        "migrations/versions/0e18af78068e_.py",
        "migrations/versions/1effc88ae61e_.py",
        "migrations/versions/6734a89a7bb7_.py",
        "migrations/versions/67c45f677a8a_.py",
        "migrations/versions/6fd25dd56cc7_.py",
        "migrations/versions/70dd1b7c30df_.py",
        "migrations/versions/73e858c1a76c_.py",
        "migrations/versions/8accb2f129c8_.py",
        "migrations/versions/c93a7d7db52a_initial20250821.py",
        "migrations/versions/ca9460bf287f_.py",
        "migrations/versions/d79de51fc5f3_.py",
        "migrations/versions/da5f7cad7d38_.py",
    )


def test_helper_lock_rejects_symlink_and_contended_regular_file(tmp_path, monkeypatch):
    data_directory = tmp_path / "data"
    data_directory.mkdir(mode=0o700)
    app_directory = data_directory / "back-end"
    app_directory.mkdir()
    lock_path = data_directory / "backend-mutations-dev.lock"
    monkeypatch.setattr(reconciliation, "APP_DIR", app_directory)
    monkeypatch.setattr(reconciliation, "LOCK_PATH", lock_path)

    lock_path.symlink_to(data_directory / "target")
    with pytest.raises(reconciliation.ReconciliationBlocked, match="safely open"):
        reconciliation._acquire_lock()
    lock_path.unlink()

    first_descriptor = reconciliation._acquire_lock()
    try:
        with pytest.raises(reconciliation.ReconciliationBlocked, match="holds the lock"):
            reconciliation._acquire_lock()
    finally:
        fcntl.flock(first_descriptor, fcntl.LOCK_UN)
        os.close(first_descriptor)


def test_fixed_parent_accepts_root_or_current_user_but_rejects_writable_owner(
    tmp_path, monkeypatch
):
    parent = tmp_path / "data"
    parent.mkdir(mode=0o755)
    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    real_fstat = reconciliation.os.fstat
    real_details = real_fstat(descriptor)

    class Details:
        st_mode = real_details.st_mode
        st_dev = real_details.st_dev
        st_ino = real_details.st_ino
        st_uid = 0
        st_gid = os.getegid()

    try:
        monkeypatch.setattr(reconciliation.os, "fstat", lambda _descriptor: Details())
        assert reconciliation._validate_fixed_parent_descriptor(
            descriptor, str(parent)
        ) == (real_details.st_dev, real_details.st_ino)

        Details.st_uid = os.geteuid()
        reconciliation._validate_fixed_parent_descriptor(descriptor, str(parent))

        Details.st_uid = max(os.geteuid(), 1) + 1000
        with pytest.raises(reconciliation.ReconciliationBlocked, match="unsafe parent"):
            reconciliation._validate_fixed_parent_descriptor(descriptor, str(parent))

        Details.st_uid = 0
        Details.st_mode = real_details.st_mode | 0o020
        with pytest.raises(reconciliation.ReconciliationBlocked, match="unsafe parent"):
            reconciliation._validate_fixed_parent_descriptor(descriptor, str(parent))
    finally:
        os.close(descriptor)
