import hashlib
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


def _migration(revision: str, down_revision: str | tuple[str, ...] | None) -> bytes:
    return (
        f"revision = {revision!r}\n"
        f"down_revision = {down_revision!r}\n"
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
    _git(repository, "add", "README.md")
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

    quarantine_root = tmp_path / "quarantine"
    monkeypatch.setattr(reconciliation, "APP_DIR", repository)
    monkeypatch.setattr(reconciliation, "QUARANTINE_ROOT", quarantine_root)
    monkeypatch.setattr(reconciliation, "ALLOWLIST", allowlist)
    monkeypatch.setattr(reconciliation, "ALLOWLIST_SET", frozenset(allowlist))
    monkeypatch.setattr(reconciliation, "_validate_fixed_parent", lambda _path: None)
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
    assert first["aggregate_sha256"] == reconciliation.aggregate_digest(first["files"])
    assert [entry["path"] for entry in first["files"]] == list(payloads)
    assert [entry["revision"] for entry in first["files"]] == ["legacy_a", "legacy_b"]
    assert first["files"][1]["down_revision"] == "legacy_a"
    assert first["live_current_revisions"] == ["committed_head"]
    assert first["live_current_allowlisted_revisions"] == []
    assert first["committed_heads"] == []
    assert first["committed_allowlisted_revision_referenced"] is False
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

    with pytest.raises(reconciliation.ReconciliationBlocked, match="still identifies"):
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
    assert all((destination / path).read_bytes() == payload for path, payload in payloads.items())
    manifest_payload = (destination / "manifest.json").read_bytes()
    manifest = json.loads(manifest_payload)
    assert manifest["aggregate_sha256"] == audited["aggregate_sha256"]
    assert manifest["files"] == audited["files"]
    assert result["manifest_sha256"] == hashlib.sha256(manifest_payload).hexdigest()
    assert destination.stat().st_mode & 0o777 == 0o500


def test_apply_rolls_back_moves_when_a_later_verification_fails(tmp_path, monkeypatch):
    repository, quarantine, payloads = _checkout(tmp_path, monkeypatch)
    audited = reconciliation.audit(repository)
    real_inspect = reconciliation._inspect_file
    calls = 0

    def fail_second_quarantine_inspection(path, relative_path):
        nonlocal calls
        if str(path).startswith(str(quarantine)):
            calls += 1
            if calls == 2:
                raise reconciliation.ReconciliationBlocked("simulated verification failure")
        return real_inspect(path, relative_path)

    monkeypatch.setattr(reconciliation, "_inspect_file", fail_second_quarantine_inspection)
    with pytest.raises(reconciliation.ReconciliationBlocked, match="simulated"):
        reconciliation.apply(
            repository,
            audited["aggregate_sha256"],
            reconciliation.APPLY_CONFIRMATION,
            "98765",
        )

    assert all((repository / path).read_bytes() == payload for path, payload in payloads.items())
    assert not (quarantine / "run-98765").exists()


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
