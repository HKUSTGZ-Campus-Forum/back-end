from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import subprocess

import pytest


MODULE_PATH = Path(__file__).parents[1] / "tools" / "normalize_runtime_permissions.py"
SPEC = importlib.util.spec_from_file_location("normalize_runtime_permissions", MODULE_PATH)
assert SPEC and SPEC.loader
normalizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(normalizer)


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_normalize_only_changes_tracked_files_and_their_directories(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir(mode=0o755)
    _git(repository, "init")
    package = repository / "app"
    package.mkdir(mode=0o700)
    module = package / "__init__.py"
    module.write_text("value = 1\n", encoding="utf-8")
    executable = repository / "run-tool"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    secret = repository / ".env"
    secret.write_text("SECRET=kept-private\n", encoding="utf-8")
    secret.chmod(0o600)
    _git(repository, "add", "app/__init__.py", "run-tool")
    module.chmod(0o600)
    executable.chmod(0o600)

    result = normalizer.normalize(repository)

    assert result["tracked_files"] == 2
    assert stat.S_IMODE(module.stat().st_mode) == 0o644
    assert stat.S_IMODE(executable.stat().st_mode) == 0o755
    assert stat.S_IMODE(package.stat().st_mode) == 0o755
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600


def test_normalize_rejects_symlink_tracked_entry(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init")
    target = repository / "target"
    target.write_text("target\n", encoding="utf-8")
    (repository / "link").symlink_to(target)
    _git(repository, "add", "link")

    with pytest.raises(
        normalizer.PermissionNormalizationBlocked,
        match="not a regular file",
    ):
        normalizer.normalize(repository)


def test_deploy_normalizes_permissions_before_starting_runtime():
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "deploy.yml"
    ).read_text(encoding="utf-8")
    normalize_index = workflow.index("normalize_runtime_permissions.py")
    activate_index = workflow.index("source venv/bin/activate", normalize_index)
    restart_index = workflow.index('systemctl restart "$service_name"', activate_index)
    assert normalize_index < activate_index < restart_index
