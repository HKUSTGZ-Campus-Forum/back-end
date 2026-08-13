from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"


def _service_helper() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        r"^          assert_api_service_checkout\(\) \{\n(.*?)^          \}\n",
        workflow,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    body = "\n".join(
        line.removeprefix("            ") for line in match.group(1).splitlines()
    )
    return f"assert_api_service_checkout() {{\n{body}\n}}\n"


def _run_helper(tmp_path: Path, *, failing_property: str | None = None):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        "property=\"\"\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = -p ]; then property=\"$2\"; shift 2; else shift; fi\n"
        "done\n"
        f"if [ \"$property\" = {failing_property or '__never__'} ]; then exit 7; fi\n"
        "case \"$property\" in\n"
        "  MainPID) echo 123 ;;\n"
        "  WorkingDirectory) echo /data/prod_unikorn/back-end ;;\n"
        "  RootDirectory|RootImage) echo ;;\n"
        "  *) exit 8 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    helper = _service_helper().replace("/usr/bin/systemctl", str(systemctl))
    script = (
        "set -Eeuo pipefail\n"
        'service_name="prod-unikorn-api.service"\n'
        'app_dir="/data/prod_unikorn/back-end"\n'
        f"{helper}\n"
        "assert_api_service_checkout\n"
    )
    return subprocess.run(
        ["bash", "-c", script], text=True, capture_output=True, check=False
    )


def test_service_checkout_helper_accepts_exact_launch_context(tmp_path):
    assert _run_helper(tmp_path).returncode == 0


def test_service_checkout_helper_fails_when_empty_property_query_fails(tmp_path):
    result = _run_helper(tmp_path, failing_property="RootImage")

    assert result.returncode != 0
    assert "Unable to verify" in result.stderr
