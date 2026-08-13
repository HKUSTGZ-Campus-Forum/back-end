import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import audit_prod_storage as storage_audit


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _fixture(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir(mode=0o755)
    data = root / storage_audit.DATA_NAME
    data.mkdir(mode=0o777)
    production = data / storage_audit.PRODUCTION_NAME
    production.mkdir(mode=0o775)
    repository = production / storage_audit.APP_NAME
    repository.mkdir(mode=0o775)
    _git(repository, "init", "--quiet", "--initial-branch=production")
    _git(repository, "config", "user.name", "Production Storage Audit Test")
    _git(repository, "config", "user.email", "audit@example.test")
    migration = (
        repository
        / storage_audit.MIGRATIONS_NAME
        / storage_audit.VERSIONS_NAME
        / storage_audit.LEGACY_MIGRATION_NAME
    )
    migration.parent.mkdir(parents=True)
    (repository / "README.md").write_text("tracked\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "--quiet", "-m", "initial")
    migration.write_text("revision = 'legacy'\n", encoding="utf-8")
    (repository / ".git").chmod(0o775)
    for name in storage_audit.OPTIONAL_PRODUCTION_CHILDREN[:2]:
        (production / name).mkdir(mode=0o755)

    monkeypatch.setattr(storage_audit, "ROOT_PATH", root)
    monkeypatch.setattr(storage_audit, "EXPECTED_APP_DIR", repository)
    return root, data, production, repository, migration


def test_audit_reports_complete_bound_hierarchy_and_digest(tmp_path, monkeypatch):
    root, data, production, repository, migration = _fixture(tmp_path, monkeypatch)

    result = storage_audit.audit()

    context = {
        key: value
        for key, value in result.items()
        if key not in {"aggregate_sha256", "status"}
    }
    assert result["aggregate_sha256"] == hashlib.sha256(
        json.dumps(
            context,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert result["status"] == "audited"
    assert result["effective_uid"] == os.geteuid()
    assert result["effective_gid"] == os.getegid()
    assert result["branch"] == "production"
    assert result["head_sha"] == _git(repository, "rev-parse", "HEAD")
    assert result["branch"] == "production"
    assert result["legacy_migration"]["sha256"] == hashlib.sha256(
        migration.read_bytes()
    ).hexdigest()
    assert result["same_device"] is True
    hierarchy = result["hierarchy"]
    assert hierarchy["filesystem root"]["inode"] == root.stat().st_ino
    assert hierarchy["data container"]["mode"] == "0755"
    assert hierarchy["production data parent"]["inode"] == production.stat().st_ino
    assert hierarchy["backend checkout"]["inode"] == repository.stat().st_ino
    assert hierarchy["backend Git directory"]["mode"] == "0775"
    assert hierarchy["migration versions directory"]["inode"] == migration.parent.stat().st_ino
    assert [entry["status"] for entry in result["optional_children"]] == [
        "present",
        "present",
        "absent",
        "absent",
    ]


def test_audit_rejects_symlinked_fixed_components(tmp_path, monkeypatch):
    _root, _data, production, repository, _migration = _fixture(tmp_path, monkeypatch)
    real_git = repository / ".git-real"
    (repository / ".git").rename(real_git)
    (repository / ".git").symlink_to(real_git, target_is_directory=True)

    with pytest.raises(storage_audit.AuditBlocked, match="Git directory"):
        storage_audit.audit()

    (repository / ".git").unlink()
    real_git.rename(repository / ".git")
    real_frontend = production / "front-end-real"
    (production / "front-end").rename(real_frontend)
    (production / "front-end").symlink_to(real_frontend, target_is_directory=True)
    with pytest.raises(storage_audit.AuditBlocked, match="optional fixed child"):
        storage_audit.audit()


def test_audit_rejects_wrong_branch_or_dirty_state(tmp_path, monkeypatch):
    _root, _data, _production, repository, migration = _fixture(tmp_path, monkeypatch)
    _git(repository, "checkout", "-q", "-b", "not-production")
    with pytest.raises(storage_audit.AuditBlocked, match="production branch"):
        storage_audit.audit()

    _git(repository, "checkout", "-q", "production")
    migration.unlink()
    with pytest.raises(storage_audit.AuditBlocked, match="cannot safely open fixed file"):
        storage_audit.audit()


def test_audit_never_invokes_git_or_changes_index(tmp_path, monkeypatch):
    _root, _data, _production, repository, _migration = _fixture(tmp_path, monkeypatch)
    expected_head = _git(repository, "rev-parse", "HEAD")
    index = repository / ".git" / "index"
    before = (index.stat().st_mtime_ns, hashlib.sha256(index.read_bytes()).hexdigest())
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "attacker-git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "attacker-tree"))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.fsmonitor=!false'")

    assert storage_audit.audit()["head_sha"] == expected_head
    after = (index.stat().st_mtime_ns, hashlib.sha256(index.read_bytes()).hexdigest())
    assert after == before
    assert "subprocess" not in Path(storage_audit.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize("race", ["present", "absent"])
def test_audit_rejects_optional_child_changes_before_digest(
    tmp_path, monkeypatch, race
):
    _root, _data, production, _repository, _migration = _fixture(tmp_path, monkeypatch)

    def change_optional_state(point, _context):
        if point != "before_final_revalidation":
            return
        if race == "present":
            current = production / "backups"
            current.rename(production / "backups-original")
            current.mkdir(mode=0o755)
        else:
            (production / "operation-reports").mkdir(mode=0o755)

    monkeypatch.setattr(storage_audit, "FAILURE_INJECTOR", change_optional_state)
    with pytest.raises(storage_audit.AuditBlocked, match="optional child"):
        storage_audit.audit()


def test_audit_rejects_control_file_content_change_before_digest(
    tmp_path, monkeypatch
):
    _root, _data, _production, repository, _migration = _fixture(tmp_path, monkeypatch)
    production_ref = repository / ".git" / "refs" / "heads" / "production"
    initial = production_ref.read_text(encoding="ascii")

    def change_ref(point, _context):
        if point == "before_final_revalidation":
            production_ref.write_text("f" * 40 + "\n", encoding="ascii")

    monkeypatch.setattr(storage_audit, "FAILURE_INJECTOR", change_ref)
    with pytest.raises(storage_audit.AuditBlocked, match="fixed file changed"):
        storage_audit.audit()
    assert initial != production_ref.read_text(encoding="ascii")


def test_workflow_is_read_only_fixed_and_serialized():
    workflow = Path(".github/workflows/audit-prod-storage.yml").read_text(
        encoding="utf-8"
    )

    assert "group: backend-mutations-production" in workflow
    assert "environment: production" in workflow
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in workflow
    assert "tools/audit_prod_storage.py" in workflow
    assert "secrets.PROD_SSH_HOST" in workflow
    assert "secrets.PROD_SSH_USER" in workflow
    assert "secrets.PROD_SSH_KEY" in workflow
    assert "secrets.PROD_SSH_FINGERPRINT" in workflow
    assert "chmod 0500" in workflow
    assert "/usr/bin/sudo -n -l" in workflow
    assert "sudo_policy=matched_authentication_unknown" in workflow
    assert "sudo_policy=no_match_or_unavailable" in workflow
    assert "/usr/bin/chmod 0755 -- /data" in workflow
    assert "/usr/bin/chmod 1777 -- /data" in workflow
    assert "/usr/bin/chown ${effective_identity} -- /data/prod_unikorn" in workflow
    assert (
        "/usr/bin/chown --no-dereference --from=33:33 "
        "${effective_identity} -- /data/prod_unikorn"
        in workflow
    )
    assert 'printf \'identity_all_groups=%s\\n\' "$(id -G)"' in workflow
    assert "getent group" in workflow
    assert "getent passwd" in workflow
    assert "sudo -n /usr/bin/chmod" not in workflow
    assert "sudo -n /usr/bin/chown" not in workflow


def test_audit_rejects_linux_posix_acl_xattrs(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        os,
        "listxattr",
        lambda _descriptor: ["system.posix_acl_default"],
        raising=False,
    )
    with pytest.raises(storage_audit.AuditBlocked, match="unexpected POSIX ACL"):
        storage_audit._reject_posix_acl(123, "test directory")


def test_audit_accepts_linux_objects_without_posix_acl(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "listxattr", lambda _descriptor: [], raising=False)
    storage_audit._reject_posix_acl(123, "test directory")
