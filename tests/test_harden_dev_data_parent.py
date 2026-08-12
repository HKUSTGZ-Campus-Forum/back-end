import fcntl
import os
from pathlib import Path

import pytest

from tools import harden_dev_data_parent as hardening


def _fixture(tmp_path: Path, monkeypatch):
    parent = tmp_path / "dev_unikorn"
    parent.mkdir(mode=0o777)
    parent.chmod(0o777)
    app = parent / "back-end"
    app.mkdir()
    lock = parent / hardening.LOCK_NAME
    lock.touch(mode=0o600)
    lock.chmod(0o600)
    monkeypatch.setattr(hardening, "DATA_PARENT", parent)
    return parent, app, lock


def test_audit_then_apply_changes_only_parent_mode(tmp_path, monkeypatch):
    parent, app, lock = _fixture(tmp_path, monkeypatch)
    app_identity = (app.stat().st_dev, app.stat().st_ino)
    lock_identity = (lock.stat().st_dev, lock.stat().st_ino)

    audited = hardening.audit()
    assert audited["status"] == "requires_hardening"
    assert audited["current_mode"] == "0777"
    result = hardening.apply(
        audited["aggregate_sha256"], hardening.APPLY_CONFIRMATION
    )

    assert result["status"] == "hardened"
    assert result["before_mode"] == "0777"
    assert result["after_mode"] == "0755"
    assert parent.stat().st_mode & 0o777 == 0o755
    assert (app.stat().st_dev, app.stat().st_ino) == app_identity
    assert (lock.stat().st_dev, lock.stat().st_ino) == lock_identity


def test_apply_requires_exact_reviewed_digest_and_confirmation(tmp_path, monkeypatch):
    parent, _app, _lock = _fixture(tmp_path, monkeypatch)
    audited = hardening.audit()
    with pytest.raises(hardening.HardeningBlocked, match="confirmation"):
        hardening.apply(audited["aggregate_sha256"], "wrong")
    with pytest.raises(hardening.HardeningBlocked, match="reviewed audit"):
        hardening.apply("0" * 64, hardening.APPLY_CONFIRMATION)
    assert parent.stat().st_mode & 0o777 == 0o777


@pytest.mark.parametrize("unsafe_mode", [0o775, 0o757, 0o700])
def test_audit_rejects_unreviewed_modes(tmp_path, monkeypatch, unsafe_mode):
    parent, _app, _lock = _fixture(tmp_path, monkeypatch)
    parent.chmod(unsafe_mode)
    with pytest.raises(hardening.HardeningBlocked, match="observed boundary"):
        hardening.audit()


def test_intermediate_sticky_guard_is_auditable_and_resumable(tmp_path, monkeypatch):
    parent, _app, _lock = _fixture(tmp_path, monkeypatch)
    first = hardening.audit()

    def crash_after_guard(point, _context):
        if point == "after_sticky_guard":
            raise RuntimeError("simulated process loss")

    monkeypatch.setattr(hardening, "FAILURE_INJECTOR", crash_after_guard)
    with pytest.raises(RuntimeError, match="process loss"):
        hardening.apply(first["aggregate_sha256"], hardening.APPLY_CONFIRMATION)
    assert parent.stat().st_mode & 0o7777 == 0o1777

    monkeypatch.setattr(hardening, "FAILURE_INJECTOR", None)
    resumed = hardening.audit()
    assert resumed["status"] == "requires_completion"
    result = hardening.apply(
        resumed["aggregate_sha256"], hardening.APPLY_CONFIRMATION
    )
    assert result["before_mode"] == "1777"
    assert parent.stat().st_mode & 0o7777 == 0o755


def test_guarded_apply_detects_lock_replacement_before_final_mode(tmp_path, monkeypatch):
    parent, _app, lock = _fixture(tmp_path, monkeypatch)
    audited = hardening.audit()
    original = parent / "original-lock"

    def replace_at_guard(point, _context):
        if point == "after_sticky_guard":
            lock.rename(original)
            lock.write_text("replacement", encoding="utf-8")

    monkeypatch.setattr(hardening, "FAILURE_INJECTOR", replace_at_guard)
    with pytest.raises(hardening.HardeningBlocked, match="dev mutation lock changed"):
        hardening.apply(audited["aggregate_sha256"], hardening.APPLY_CONFIRMATION)
    assert parent.stat().st_mode & 0o7777 == 0o1777
    assert original.stat().st_mode & 0o777 == 0o600


def test_audit_rejects_symlinked_parent_app_and_lock(tmp_path, monkeypatch):
    parent, app, lock = _fixture(tmp_path, monkeypatch)
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    monkeypatch.setattr(hardening, "DATA_PARENT", tmp_path / "parent-link")
    (tmp_path / "parent-link").symlink_to(parent, target_is_directory=True)
    with pytest.raises(hardening.HardeningBlocked, match="safely open"):
        hardening.audit()

    monkeypatch.setattr(hardening, "DATA_PARENT", parent)
    app.rmdir()
    app.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(hardening.HardeningBlocked, match="checkout"):
        hardening.audit()
    app.unlink()
    app.mkdir()

    lock.unlink()
    target = tmp_path / "target"
    target.write_text("unchanged", encoding="utf-8")
    lock.symlink_to(target)
    with pytest.raises(hardening.HardeningBlocked, match="existing dev mutation lock"):
        hardening.audit()
    assert target.read_text(encoding="utf-8") == "unchanged"


def test_audit_rejects_contended_or_replaced_lock(tmp_path, monkeypatch):
    parent, _app, lock = _fixture(tmp_path, monkeypatch)
    descriptor = os.open(lock, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(hardening.HardeningBlocked, match="holds the lock"):
            hardening.audit()
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    lock.chmod(0o644)
    with pytest.raises(hardening.HardeningBlocked, match="unsafe metadata"):
        hardening.audit()
