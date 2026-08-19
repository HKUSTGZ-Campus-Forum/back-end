from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "tools" / "reconcile_dev_alembic_version.py"
SPEC = importlib.util.spec_from_file_location("reconcile_dev_alembic_version", MODULE_PATH)
assert SPEC and SPEC.loader
reconciliation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reconciliation)


def _audit_result() -> dict:
    context = {
        "schema_version": 1,
        "target": "dev",
        "database": reconciliation.EXPECTED_DATABASE,
        "legacy_revision": reconciliation.LEGACY_REVISION,
        "canonical_revision": reconciliation.CANONICAL_REVISION,
        "companion_revision": reconciliation.COMPANION_REVISION,
        "schema_check": "passed",
        "helper_sha256": "a" * 64,
        "repository_sha": "b" * 40,
        "legacy_files": [],
        "committed_graph": {
            "heads": [
                reconciliation.COMPANION_REVISION,
                reconciliation.CANONICAL_REVISION,
            ],
            "revision_count": 27,
        },
        "current_revisions": [
            reconciliation.LEGACY_REVISION,
            reconciliation.COMPANION_REVISION,
        ],
    }
    return {
        **context,
        "aggregate_sha256": reconciliation._digest(context),
        "status": "requires_reconciliation",
    }


def test_fixed_reconciliation_boundary_is_narrow():
    assert reconciliation.EXPECTED_DATABASE == "dev_unikorn"
    assert reconciliation.LEGACY_REVISION == "1effc88ae61e"
    assert reconciliation.CANONICAL_REVISION == "5202003d1ec0"
    assert reconciliation.COMPANION_REVISION == "20260807_sched_popularity"
    assert len(reconciliation.LEGACY_FILES) == 12
    assert all(
        reconciliation.SHA256_RE.fullmatch(value)
        for value in reconciliation.LEGACY_FILES.values()
    )


def test_checkout_permission_guard_allows_only_reviewed_group_write(monkeypatch):
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "stat.S_IMODE(details.st_mode) not in {0o755, 0o775}" in source
    assert "details.st_uid != os.geteuid()" in source
    assert "details.st_gid != os.getegid()" in source


def test_apply_requires_exact_digest_confirmation_and_backup(monkeypatch):
    reviewed = _audit_result()
    monkeypatch.setattr(reconciliation, "audit", lambda: reviewed)
    monkeypatch.setattr(
        reconciliation,
        "_database_probe",
        lambda *, apply=False: {
            "before": [reconciliation.LEGACY_REVISION, reconciliation.COMPANION_REVISION],
            "after": [reconciliation.COMPANION_REVISION, reconciliation.CANONICAL_REVISION],
        },
    )

    result = reconciliation.apply(
        reviewed["aggregate_sha256"],
        reconciliation.APPLY_CONFIRMATION,
        "c" * 64,
    )
    assert result["status"] == "reconciled"
    assert result["before"] == [
        reconciliation.LEGACY_REVISION,
        reconciliation.COMPANION_REVISION,
    ]
    assert result["after"] == [
        reconciliation.COMPANION_REVISION,
        reconciliation.CANONICAL_REVISION,
    ]


@pytest.mark.parametrize(
    ("digest", "confirmation", "backup", "message"),
    [
        ("bad", reconciliation.APPLY_CONFIRMATION, "c" * 64, "aggregate digest"),
        ("a" * 64, "wrong", "c" * 64, "confirmation"),
        ("a" * 64, reconciliation.APPLY_CONFIRMATION, "bad", "backup digest"),
    ],
)
def test_apply_rejects_invalid_controls(digest, confirmation, backup, message):
    with pytest.raises(reconciliation.ReconciliationBlocked, match=message):
        reconciliation.apply(digest, confirmation, backup)


def test_apply_rejects_host_drift(monkeypatch):
    reviewed = _audit_result()
    monkeypatch.setattr(reconciliation, "audit", lambda: reviewed)
    with pytest.raises(reconciliation.ReconciliationBlocked, match="does not match"):
        reconciliation.apply(
            "d" * 64,
            reconciliation.APPLY_CONFIRMATION,
            "c" * 64,
        )


def test_database_mutation_script_uses_exact_rows(monkeypatch):
    captured = {}

    class Result:
        returncode = 0
        stdout = json.dumps(
            {
                "database": reconciliation.EXPECTED_DATABASE,
                "before": [reconciliation.LEGACY_REVISION, reconciliation.COMPANION_REVISION],
                "after": [reconciliation.COMPANION_REVISION, reconciliation.CANONICAL_REVISION],
            }
        )
        stderr = ""

    def fake_run(*arguments, env=None):
        captured["arguments"] = arguments
        return Result()

    monkeypatch.setattr(reconciliation, "_run", fake_run)
    result = reconciliation._database_probe(apply=True)
    script = captured["arguments"][2]
    assert "DELETE FROM alembic_version WHERE version_num = :revision" in script
    assert "INSERT INTO alembic_version (version_num) VALUES (:revision)" in script
    assert "rowcount != 1" in script
    assert result["after"][-1] == reconciliation.CANONICAL_REVISION


def test_schema_check_compares_metadata_without_loading_migration_modules(monkeypatch):
    captured = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*arguments, env=None):
        captured["arguments"] = arguments
        return Result()

    monkeypatch.setattr(reconciliation, "_run", fake_run)
    reconciliation._schema_check()
    script = captured["arguments"][2]
    assert "compare_metadata" in script
    assert "MigrationContext.configure" in script
    assert "ScriptDirectory" not in script
    assert "flask" not in captured["arguments"][0]


def test_schema_check_reports_only_structural_diff_representations(monkeypatch):
    class Result:
        returncode = 43
        stdout = '["remove_table: legacy"]\n'
        stderr = "ignored runtime detail"

    monkeypatch.setattr(reconciliation, "_run", lambda *args, **kwargs: Result())
    with pytest.raises(
        reconciliation.ReconciliationBlocked,
        match="remove_table: legacy",
    ):
        reconciliation._schema_check()


def test_workflow_requires_a_verified_backup_before_apply():
    workflow = (
        Path(__file__).parents[1]
        / ".github"
        / "workflows"
        / "reconcile-dev-alembic-version.yml"
    ).read_text(encoding="utf-8")
    assert "create_verified_database_backup" in workflow
    assert "--expected-database dev_unikorn" in workflow
    assert "REPLACE_DEV_LEGACY_ALEMBIC_HEAD" in workflow
    assert "group: backend-mutations-dev" in workflow
    assert "flock -n 9" in workflow
