from pathlib import Path
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]
RELEASE_PATHS = (
    "app",
    "scripts/run_scheduler_popularity_cron.py",
    "scripts/sample_scheduler_popularity.py",
    "tools/render_scheduler_popularity_crontab.py",
)


def test_scoped_sampler_archive_contains_runtime_without_tracked_symlinks(tmp_path):
    archive = tmp_path / "sampler.tar"
    result = subprocess.run(
        ["git", "archive", "--format=tar", f"--output={archive}", "HEAD", "--", *RELEASE_PATHS],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    with tarfile.open(archive) as payload:
        members = payload.getmembers()
        names = {member.name for member in members}

    assert "app/__init__.py" in names
    assert "app/services/scheduler_popularity.py" in names
    assert "scripts/run_scheduler_popularity_cron.py" in names
    assert "scripts/sample_scheduler_popularity.py" in names
    assert "tools/render_scheduler_popularity_crontab.py" in names
    assert not any(member.issym() or member.islnk() for member in members)
    assert not any(name.startswith(".codex-venv/") for name in names)
    assert ".codex-feedback-dev.db" not in names
    assert all(
        name == "app"
        or name.startswith("app/")
        or name == "scripts"
        or name == "tools"
        or name in RELEASE_PATHS[1:]
        for name in names
    )
