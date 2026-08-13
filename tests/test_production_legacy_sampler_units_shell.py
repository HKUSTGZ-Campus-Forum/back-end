from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"


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


def _run_helper(tmp_path: Path, *, mode: str, assertion: str):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        "[ \"$FAKE_SYSTEMD_MODE\" != query-failure ] || exit 7\n"
        "case \"$FAKE_SYSTEMD_MODE\" in\n"
        "  missing) printf 'LoadState=not-found\\nActiveState=inactive\\nUnitFileState=\\n' ;;\n"
        "  loaded-inactive) printf 'LoadState=loaded\\nActiveState=inactive\\nUnitFileState=enabled\\n' ;;\n"
        "  loaded-failed) printf 'LoadState=loaded\\nActiveState=failed\\nUnitFileState=enabled\\n' ;;\n"
        "  active) printf 'LoadState=loaded\\nActiveState=active\\nUnitFileState=enabled\\n' ;;\n"
        "  disabled) printf 'LoadState=loaded\\nActiveState=inactive\\nUnitFileState=disabled\\n' ;;\n"
        "  static) printf 'LoadState=loaded\\nActiveState=inactive\\nUnitFileState=static\\n' ;;\n"
        "  masked) printf 'LoadState=masked\\nActiveState=inactive\\nUnitFileState=masked\\n' ;;\n"
        "  generated) printf 'LoadState=loaded\\nActiveState=inactive\\nUnitFileState=generated\\n' ;;\n"
        "  transient) printf 'LoadState=loaded\\nActiveState=inactive\\nUnitFileState=transient\\n' ;;\n"
        "  omitted) printf 'LoadState=not-found\\nActiveState=inactive\\n' ;;\n"
        "  duplicate) printf 'LoadState=not-found\\nLoadState=loaded\\nActiveState=inactive\\nUnitFileState=\\n' ;;\n"
        "  unknown) printf 'LoadState=not-found\\nActiveState=inactive\\nUnitFileState=\\nDescription=x\\n' ;;\n"
        "  malformed) printf 'LoadState=not-found\\nActiveState\\nUnitFileState=\\n' ;;\n"
        "  *) exit 8 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    helpers = (
        _helper("read_systemd_unit_state")
        + _helper("assert_unit_inactive")
        + _helper("assert_legacy_sampler_units_absent")
    ).replace("/usr/bin/systemctl", str(systemctl))
    script = (
        "set -Eeuo pipefail\n"
        f"{helpers}\n"
        f"{assertion}\n"
    )
    return subprocess.run(
        ["bash", "-c", script],
        text=True,
        capture_output=True,
        check=False,
        env={"FAKE_SYSTEMD_MODE": mode},
    )


def test_missing_units_with_exact_empty_unit_file_state_are_accepted(tmp_path):
    assert _run_helper(
        tmp_path, mode="missing", assertion="assert_legacy_sampler_units_absent"
    ).returncode == 0


@pytest.mark.parametrize(
    "mode",
    ["loaded-inactive", "active", "disabled", "static", "masked", "generated", "transient"],
)
def test_any_installed_legacy_unit_state_is_rejected(tmp_path, mode):
    result = _run_helper(
        tmp_path, mode=mode, assertion="assert_legacy_sampler_units_absent"
    )

    assert result.returncode != 0
    assert "Refusing user-cron activation" in result.stderr


@pytest.mark.parametrize(
    "mode", ["query-failure", "omitted", "duplicate", "unknown", "malformed"]
)
def test_untrustworthy_legacy_unit_query_is_rejected(tmp_path, mode):
    result = _run_helper(
        tmp_path, mode=mode, assertion="assert_legacy_sampler_units_absent"
    )

    assert result.returncode != 0
    assert "Unable to verify legacy sampler unit" in result.stderr


@pytest.mark.parametrize("mode", ["loaded-inactive", "loaded-failed"])
def test_api_unit_accepts_only_loaded_quiesced_states(tmp_path, mode):
    assert _run_helper(
        tmp_path,
        mode=mode,
        assertion="assert_unit_inactive prod-unikorn-api.service",
    ).returncode == 0


@pytest.mark.parametrize(
    "mode", ["missing", "active", "query-failure", "omitted", "duplicate", "unknown", "malformed"]
)
def test_api_unit_rejects_absent_active_or_untrustworthy_states(tmp_path, mode):
    result = _run_helper(
        tmp_path,
        mode=mode,
        assertion="assert_unit_inactive prod-unikorn-api.service",
    )

    assert result.returncode != 0
