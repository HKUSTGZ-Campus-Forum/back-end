import fcntl
import os
from pathlib import Path
import subprocess

import pytest

from tools import harden_dev_data_container as hardening

RELEASE_SHA = "a" * 40


def _fixture(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    data = root / "data"
    data.mkdir(mode=0o777)
    data.chmod(0o777)
    dev_parent = data / hardening.DEV_PARENT_NAME
    dev_parent.mkdir(mode=0o777)
    dev_parent.chmod(0o777)
    app = dev_parent / hardening.APP_NAME
    app.mkdir()
    lock = dev_parent / hardening.LOCK_NAME
    lock.touch(mode=0o600)
    lock.chmod(0o600)

    monkeypatch.setattr(hardening, "ROOT_PATH", root)
    monkeypatch.setattr(hardening, "DATA_PATH", data)
    monkeypatch.setattr(hardening, "ROOT_OWNER_UID", os.geteuid())
    monkeypatch.setattr(hardening, "ROOT_GROUP_GID", os.getegid())

    commands = []

    def exact_chmod(command, **kwargs):
        commands.append((command, kwargs))
        assert command == [
            hardening.SUDO_PATH,
            "-n",
            hardening.CHMOD_PATH,
            "1777",
            "--",
            str(data),
        ]
        data.chmod(hardening.TARGET_MODE)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(hardening.subprocess, "run", exact_chmod)
    return root, data, dev_parent, app, lock, commands


def test_audit_then_apply_invokes_only_exact_sudo_chmod(tmp_path, monkeypatch):
    _root, data, dev_parent, app, lock, commands = _fixture(tmp_path, monkeypatch)
    identities = {
        "dev_parent": (dev_parent.stat().st_dev, dev_parent.stat().st_ino),
        "app": (app.stat().st_dev, app.stat().st_ino),
        "lock": (lock.stat().st_dev, lock.stat().st_ino),
    }

    audited = hardening.audit(RELEASE_SHA)
    assert audited["status"] == "requires_hardening"
    assert audited["data_mode"] == "0777"
    assert audited["root_owner_uid"] == os.geteuid()
    assert audited["helper_sha256"]

    result = hardening.apply(
        audited["aggregate_sha256"], hardening.APPLY_CONFIRMATION, RELEASE_SHA
    )
    assert result["status"] == "hardened"
    assert result["before_mode"] == "0777"
    assert result["after_mode"] == "1777"
    assert data.stat().st_mode & 0o7777 == 0o1777
    assert len(commands) == 1
    assert (dev_parent.stat().st_dev, dev_parent.stat().st_ino) == identities["dev_parent"]
    assert (app.stat().st_dev, app.stat().st_ino) == identities["app"]
    assert (lock.stat().st_dev, lock.stat().st_ino) == identities["lock"]


def test_apply_requires_literal_confirmation_and_exact_digest(tmp_path, monkeypatch):
    _root, data, _dev_parent, _app, _lock, commands = _fixture(tmp_path, monkeypatch)
    audited = hardening.audit(RELEASE_SHA)
    with pytest.raises(hardening.HardeningBlocked, match="confirmation"):
        hardening.apply(audited["aggregate_sha256"], "wrong", RELEASE_SHA)
    with pytest.raises(hardening.HardeningBlocked, match="reviewed audit"):
        hardening.apply("0" * 64, hardening.APPLY_CONFIRMATION, RELEASE_SHA)
    assert data.stat().st_mode & 0o7777 == 0o777
    assert commands == []


def test_already_sticky_state_is_auditable_and_apply_is_idempotent(tmp_path, monkeypatch):
    _root, data, _dev_parent, _app, _lock, commands = _fixture(tmp_path, monkeypatch)
    data.chmod(0o1777)
    audited = hardening.audit(RELEASE_SHA)
    assert audited["status"] == "already_sticky"
    result = hardening.apply(
        audited["aggregate_sha256"], hardening.APPLY_CONFIRMATION, RELEASE_SHA
    )
    assert result["status"] == "already_sticky"
    assert result["before_mode"] == "1777"
    assert commands == []


@pytest.mark.parametrize("mode", [0o775, 0o1755, 0o755])
def test_audit_rejects_unreviewed_data_modes(tmp_path, monkeypatch, mode):
    _root, data, _dev_parent, _app, _lock, _commands = _fixture(tmp_path, monkeypatch)
    data.chmod(mode)
    with pytest.raises(hardening.HardeningBlocked, match="observed boundary"):
        hardening.audit(RELEASE_SHA)


def test_audit_rejects_symlinked_data_child_app_and_lock(tmp_path, monkeypatch):
    root, data, dev_parent, app, lock, _commands = _fixture(tmp_path, monkeypatch)
    real_data = tmp_path / "real-data"
    data.rename(real_data)
    data.symlink_to(real_data, target_is_directory=True)
    with pytest.raises(hardening.HardeningBlocked, match="data container"):
        hardening.audit(RELEASE_SHA)

    data.unlink()
    real_data.rename(data)
    real_dev_parent = tmp_path / "real-dev"
    dev_parent.rename(real_dev_parent)
    dev_parent.symlink_to(real_dev_parent, target_is_directory=True)
    with pytest.raises(hardening.HardeningBlocked, match="dev data parent"):
        hardening.audit(RELEASE_SHA)

    dev_parent.unlink()
    real_dev_parent.rename(dev_parent)
    app.rmdir()
    app.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(hardening.HardeningBlocked, match="backend checkout"):
        hardening.audit(RELEASE_SHA)
    app.unlink()
    app.mkdir()

    lock.unlink()
    target = tmp_path / "target"
    target.write_text("unchanged", encoding="utf-8")
    lock.symlink_to(target)
    with pytest.raises(hardening.HardeningBlocked, match="mutation lock"):
        hardening.audit(RELEASE_SHA)
    assert target.read_text(encoding="utf-8") == "unchanged"


def test_audit_uses_exclusive_lock_and_rejects_other_mutation(tmp_path, monkeypatch):
    _root, _data, _dev_parent, _app, lock, _commands = _fixture(tmp_path, monkeypatch)
    descriptor = os.open(lock, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(hardening.HardeningBlocked, match="holds the lock"):
            hardening.audit(RELEASE_SHA)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_child_rename_race_fails_closed_after_sticky_protection(tmp_path, monkeypatch):
    _root, data, dev_parent, _app, _lock, _commands = _fixture(tmp_path, monkeypatch)
    audited = hardening.audit(RELEASE_SHA)
    moved = data / "moved-dev"

    original_run = hardening.subprocess.run

    def chmod_then_rename(command, **kwargs):
        completed = original_run(command, **kwargs)
        dev_parent.rename(moved)
        replacement = data / hardening.DEV_PARENT_NAME
        replacement.mkdir(mode=0o755)
        return completed

    monkeypatch.setattr(hardening.subprocess, "run", chmod_then_rename)
    with pytest.raises(hardening.HardeningBlocked, match="dev data parent pathname"):
        hardening.apply(
            audited["aggregate_sha256"], hardening.APPLY_CONFIRMATION, RELEASE_SHA
        )
    assert data.stat().st_mode & 0o7777 == 0o1777
    assert moved.is_dir()


def test_failure_after_chmod_leaves_auditable_sticky_recovery(tmp_path, monkeypatch):
    _root, data, _dev_parent, _app, _lock, _commands = _fixture(tmp_path, monkeypatch)
    audited = hardening.audit(RELEASE_SHA)

    def interrupt(point, _context):
        if point == "after_sticky_guard":
            raise RuntimeError("simulated process loss")

    monkeypatch.setattr(hardening, "FAILURE_INJECTOR", interrupt)
    with pytest.raises(RuntimeError, match="process loss"):
        hardening.apply(
            audited["aggregate_sha256"], hardening.APPLY_CONFIRMATION, RELEASE_SHA
        )
    assert data.stat().st_mode & 0o7777 == 0o1777
    monkeypatch.setattr(hardening, "FAILURE_INJECTOR", None)
    assert hardening.audit(RELEASE_SHA)["status"] == "already_sticky"


def test_audit_rejects_unsafe_lock_metadata(tmp_path, monkeypatch):
    _root, _data, _dev_parent, _app, lock, _commands = _fixture(tmp_path, monkeypatch)
    lock.chmod(0o644)
    with pytest.raises(hardening.HardeningBlocked, match="unsafe metadata"):
        hardening.audit(RELEASE_SHA)


def test_audit_rejects_wrong_lock_group(tmp_path, monkeypatch):
    _root, _data, _dev_parent, _app, lock, _commands = _fixture(tmp_path, monkeypatch)
    real_fstat = hardening.os.fstat
    lock_identity = (lock.stat().st_dev, lock.stat().st_ino)

    class WrongGroup:
        def __init__(self, source):
            for name in dir(source):
                if name.startswith("st_"):
                    setattr(self, name, getattr(source, name))
            self.st_gid = os.getegid() + 1

    def wrong_lock_group(descriptor):
        details = real_fstat(descriptor)
        if (details.st_dev, details.st_ino) == lock_identity:
            return WrongGroup(details)
        return details

    monkeypatch.setattr(hardening.os, "fstat", wrong_lock_group)
    with pytest.raises(hardening.HardeningBlocked, match="unsafe metadata"):
        hardening.audit(RELEASE_SHA)


def test_release_sha_is_required_and_bound_into_digest(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    with pytest.raises(hardening.HardeningBlocked, match="release SHA"):
        hardening.audit("bad")
    first = hardening.audit(RELEASE_SHA)
    second = hardening.audit("b" * 40)
    assert first["expected_release_sha"] == RELEASE_SHA
    assert first["aggregate_sha256"] != second["aggregate_sha256"]


def test_sudo_failure_preserves_original_mode(tmp_path, monkeypatch):
    _root, data, _dev_parent, _app, _lock, _commands = _fixture(tmp_path, monkeypatch)
    audited = hardening.audit(RELEASE_SHA)

    def denied(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="denied")

    monkeypatch.setattr(hardening.subprocess, "run", denied)
    with pytest.raises(hardening.HardeningBlocked, match="denied"):
        hardening.apply(
            audited["aggregate_sha256"], hardening.APPLY_CONFIRMATION, RELEASE_SHA
        )
    assert data.stat().st_mode & 0o7777 == 0o777


def test_race_before_chmod_is_caught_after_safe_transition(tmp_path, monkeypatch):
    _root, data, dev_parent, _app, _lock, _commands = _fixture(tmp_path, monkeypatch)
    audited = hardening.audit(RELEASE_SHA)
    moved = data / "moved-before"

    def move_before(point, _context):
        if point == "before_sticky_guard":
            dev_parent.rename(moved)
            (data / hardening.DEV_PARENT_NAME).mkdir(mode=0o755)

    monkeypatch.setattr(hardening, "FAILURE_INJECTOR", move_before)
    with pytest.raises(hardening.HardeningBlocked, match="dev data parent pathname"):
        hardening.apply(
            audited["aggregate_sha256"], hardening.APPLY_CONFIRMATION, RELEASE_SHA
        )
    assert data.stat().st_mode & 0o7777 == 0o1777
