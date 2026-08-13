from pathlib import Path
import hashlib
import os
import re
import subprocess

from tools import production_deploy_journal as journal


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
TARGET_SHA = "b" * 40
OLD_SHA = "a" * 40


def _helper(name: str) -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        rf"^          {name}\(\) \{{\n(.*?)^          \}}\n",
        workflow,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    body = "\n".join(
        line.removeprefix("            ") for line in match.group(1).splitlines()
    )
    return f"{name}() {{\n{body}\n}}\n"


def _fake_crontab(tmp_path: Path) -> tuple[Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state = tmp_path / "live-crontab"
    log = tmp_path / "crontab-log"
    executable = fake_bin / "crontab"
    executable.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  -l)\n"
        "    if [ -f \"$FAKE_CRONTAB_STATE\" ]; then cat \"$FAKE_CRONTAB_STATE\"; exit 0; fi\n"
        "    echo 'no crontab for deployer' >&2\n"
        "    exit 1\n"
        "    ;;\n"
        "  -r) rm -f \"$FAKE_CRONTAB_STATE\"; printf 'remove\\n' >> \"$FAKE_CRONTAB_LOG\" ;;\n"
        "  *) cp -- \"$1\" \"$FAKE_CRONTAB_STATE\"; printf 'install\\n' >> \"$FAKE_CRONTAB_LOG\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return fake_bin, state, log


def _activation_script(
    tmp_path: Path, *, guard_succeeds: bool, with_cleanup_trap: bool
) -> subprocess.CompletedProcess[str]:
    fake_bin, state, log = _fake_crontab(tmp_path)
    state.write_text("# original\n", encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()
    before = stage / "before"
    before.write_bytes(state.read_bytes())
    candidate = stage / "candidate"
    candidate.write_text("# replacement\n", encoding="utf-8")
    readback = stage / "readback"
    expected_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
    helpers = _helper("restore_sampling_crontab") + _helper(
        "activate_sampling_crontab"
    )
    guard_body = ":" if guard_succeeds else "return 17"
    trap = "trap 'restore_sampling_crontab' EXIT\n" if with_cleanup_trap else ""
    script = (
        "set -Eeuo pipefail\n"
        f"{helpers}\n"
        f"assert_legacy_sampler_units_absent() {{ {guard_body}; }}\n"
        "sleep() { :; }\n"
        "sampler_args=(true)\n"
        "crontab_existed=true\n"
        "crontab_mutated=false\n"
        f"crontab_stage={stage!s}\n"
        f"crontab_before={before!s}\n"
        f"crontab_candidate={candidate!s}\n"
        f"crontab_readback={readback!s}\n"
        f"original_crontab_sha={hashlib.sha256(before.read_bytes()).hexdigest()}\n"
        f"installed_crontab_sha={expected_sha}\n"
        f"expected_crontab_sha={expected_sha}\n"
        f"{trap}"
        "activate_sampling_crontab\n"
        "test \"${crontab_mutated}\" = false\n"
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "FAKE_CRONTAB_STATE": str(state),
            "FAKE_CRONTAB_LOG": str(log),
        }
    )
    return subprocess.run(
        ["bash", "-c", script],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def _advance_journal_to_committed(tmp_path: Path, monkeypatch):
    operations_root = tmp_path / "operations"
    operations_root.mkdir(mode=0o700)
    backup_root = tmp_path / "backups"
    backup_root.mkdir(mode=0o750)
    monkeypatch.setattr(journal, "JOURNAL_ROOT", operations_root / "api-deploy")
    monkeypatch.setattr(journal, "BACKUP_ROOT", backup_root)
    backup = backup_root / f"prod_unikorn-20260813T010203Z-{TARGET_SHA[:12]}.dump"
    payload = b"verified backup"
    backup.write_bytes(payload)
    backup.chmod(0o600)

    journal.prepare(TARGET_SHA, OLD_SHA)
    journal.advance(TARGET_SHA, "SERVICE_STOP_REQUESTED")
    journal.advance(TARGET_SHA, "SERVICE_STOPPED")
    journal.advance(TARGET_SHA, "FINAL_BACKUP_STARTED", backup_name=backup.name)
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
    for phase in journal.PHASES[5:]:
        journal.advance(TARGET_SHA, phase)


def test_committed_sampler_preflight_failure_is_non_mutating_and_retryable(
    tmp_path, monkeypatch
):
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    first = _activation_script(
        first_dir, guard_succeeds=False, with_cleanup_trap=True
    )
    assert first.returncode != 0
    assert (first_dir / "live-crontab").read_text(encoding="utf-8") == "# original\n"
    assert not (first_dir / "crontab-log").exists()

    _advance_journal_to_committed(tmp_path, monkeypatch)
    resumed = journal.prepare(TARGET_SHA, "c" * 40)
    assert resumed["phase"] == "COMMITTED"
    assert resumed["resumed"] is True

    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second = _activation_script(
        second_dir, guard_succeeds=True, with_cleanup_trap=False
    )
    assert second.returncode == 0, second.stderr
    assert (second_dir / "live-crontab").read_text(encoding="utf-8") == "# replacement\n"
    assert (second_dir / "crontab-log").read_text(encoding="utf-8") == "install\n"
