import ast
import hashlib
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
VERSION_DIR = ROOT / "migrations" / "versions"


def _load_revision_metadata():
    revisions = {}
    for path in sorted(VERSION_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        metadata = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "revision",
                    "down_revision",
                    "depends_on",
                }:
                    metadata[target.id] = ast.literal_eval(node.value)
        revision = metadata.get("revision")
        if revision:
            assert revision not in revisions, f"duplicate Alembic revision: {revision}"
            revisions[revision] = (
                path,
                metadata.get("down_revision"),
                metadata.get("depends_on"),
            )
    return revisions


def test_alembic_revision_chain_references_existing_revisions():
    revisions = _load_revision_metadata()
    missing = []
    for revision, (path, down_revision, depends_on) in revisions.items():
        parents = (
            *(down_revision if isinstance(down_revision, tuple) else (down_revision,)),
            *(depends_on if isinstance(depends_on, tuple) else (depends_on,)),
        )
        for parent in parents:
            if parent is not None and parent not in revisions:
                missing.append((revision, parent, path.name))

    assert missing == []


def test_alembic_revision_graph_is_acyclic_and_has_expected_heads():
    revisions = _load_revision_metadata()
    visiting = set()
    visited = set()

    def visit(revision):
        assert revision not in visiting, f"cycle in Alembic history at {revision}"
        if revision in visited:
            return
        visiting.add(revision)
        _path, down_revision, depends_on = revisions[revision]
        parents = (
            *(down_revision if isinstance(down_revision, tuple) else (down_revision,)),
            *(depends_on if isinstance(depends_on, tuple) else (depends_on,)),
        )
        for parent in parents:
            if parent is not None:
                visit(parent)
        visiting.remove(revision)
        visited.add(revision)

    for revision in revisions:
        visit(revision)

    parents = {
        parent
        for _path, down_revision, _depends_on in revisions.values()
        for parent in (
            down_revision if isinstance(down_revision, tuple) else (down_revision,)
        )
        if parent is not None
    }
    assert set(revisions) - parents == {
        "20260812_pop_history",
        "20260813_feedback_schema",
    }


def test_cross_branch_dependencies_order_pristine_database_revisions():
    oauth_migration = (
        VERSION_DIR / "create_oauth_tables.py"
    ).read_text(encoding="utf-8")
    academic_migration = (
        VERSION_DIR / "20260529_academic_map.py"
    ).read_text(encoding="utf-8")

    assert "depends_on = '7658cd1e9afd'" in oauth_migration
    assert 'depends_on = "7ddb3557965d"' in academic_migration


def test_migration_manifest_covers_and_authenticates_every_revision():
    manifest_path = VERSION_DIR / "SHA256SUMS"
    entries = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        digest, filename = line.split("  ", maxsplit=1)
        assert filename not in entries
        entries[filename] = digest

    revision_paths = sorted(VERSION_DIR.glob("*.py"))
    assert set(entries) == {path.name for path in revision_paths}
    for path in revision_paths:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entries[path.name]


def test_alembic_revision_ids_fit_existing_version_table_width():
    revision_files = sorted((ROOT / "migrations" / "versions").glob("*.py"))
    too_long = []

    for path in revision_files:
        text = path.read_text(encoding="utf-8")
        revision_match = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if revision_match and len(revision_match.group(1)) > 32:
            too_long.append((path.name, revision_match.group(1)))

    assert too_long == []


def test_course_domain_migration_is_safe_for_auto_initialized_dev_tables():
    migration_path = ROOT / "migrations" / "versions" / "20260607_course_domain_redesign.py"
    migration_text = migration_path.read_text(encoding="utf-8")

    assert "domain_tables.issubset" in migration_text
    assert "return" in migration_text
    assert "_has_unique_constraint_or_index" in migration_text


def test_auto_initialized_migrations_are_idempotent():
    guarded_migrations = [
        (
            "20260529_academic_map.py",
            ['_table_exists("curriculum_programs")', '_index_exists("curriculum_programs"'],
        ),
        (
            "20260531_add_scheduler_fields.py",
            ["column.name not in existing"],
        ),
        (
            "20260531_add_scheduler_section_lecture_map_cart.py",
            ['inspector.has_table("scheduler_sections")', '"idx_scheduler_sections_course" not in indexes'],
        ),
        (
            "20260607_course_domain_redesign.py",
            ["domain_tables.issubset"],
        ),
        (
            "20260807_scheduler_popularity.py",
            [
                '"scheduler_popularity_events" not in inspector.get_table_names()',
                '"idx_user_offering_carts_popularity" not in cart_indexes',
                '"idx_user_section_selections_popularity" not in selection_indexes',
                'INSERT INTO user_section_selections',
            ],
        ),
        (
            "b79b55da2342_.py",
            [
                'if "track" not in columns',
                'inspector.get_unique_constraints("contest_submissions")',
                'inspector.get_indexes("contest_submissions")',
            ],
        ),
        (
            "5202003d1ec0_.py",
            [
                'if "reply_to_message_id" not in columns',
                'inspector.get_foreign_keys("gugu_messages")',
            ],
        ),
    ]

    for filename, guard_texts in guarded_migrations:
        migration_text = (ROOT / "migrations" / "versions" / filename).read_text(encoding="utf-8")
        for guard_text in guard_texts:
            assert guard_text in migration_text


def test_deploy_workflows_fail_on_migration_errors_and_use_committed_revisions():
    workflow_paths = [
        ROOT / ".github" / "workflows" / "deploy.yml",
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml",
    ]

    for path in workflow_paths:
        deploy_workflow = path.read_text(encoding="utf-8")
        assert "set -Eeuo pipefail" in deploy_workflow
        assert "flock -n 9" in deploy_workflow
        assert "sudo /usr/bin/systemctl is-active" not in deploy_workflow
        assert "flask db upgrade heads" in deploy_workflow
        assert "flask db migrate" not in deploy_workflow


def test_dev_database_initialization_syncs_curriculum_requirements():
    init_script = (ROOT / "app" / "scripts" / "init_db.py").read_text(encoding="utf-8")

    assert "sync_curriculum_requirements_from_file" in init_script
    assert init_script.index("init_identity_types()") < init_script.index("init_curriculum_requirements()")


def test_production_deploy_backfills_2024_25_scheduler_offerings():
    deploy_workflow = (ROOT / ".github" / "workflows" / "deploy-backend-prod.yml").read_text(encoding="utf-8")

    assert "python -m app.scripts.backfill_legacy_scheduler_offerings --semesters 2430 2440 --apply" in deploy_workflow
    assert deploy_workflow.index("python -m app.scripts.init_db") < deploy_workflow.index(
        "python -m app.scripts.backfill_legacy_scheduler_offerings"
    )


def test_production_deploy_activates_bounded_popularity_sampling():
    deploy_workflow = (ROOT / ".github" / "workflows" / "deploy-backend-prod.yml").read_text(
        encoding="utf-8"
    )
    sample_timer = (ROOT / "deploy" / "systemd" / "unikorn-scheduler-popularity-sample.timer").read_text(
        encoding="utf-8"
    )
    final_timer = (ROOT / "deploy" / "systemd" / "unikorn-scheduler-popularity-final.timer").read_text(
        encoding="utf-8"
    )

    flock_at = deploy_workflow.index("flock -n 9")
    trap_at = deploy_workflow.index("trap cleanup_deployment EXIT")
    preflight_at = deploy_workflow.index("Preflighting non-interactive")
    checkout_at = deploy_workflow.index("git checkout production")
    migrate_at = deploy_workflow.index("flask db upgrade heads")
    smoke_at = deploy_workflow.index('systemctl start "${baseline_service}"')
    health_at = deploy_workflow.index("Local scheduler API verified")
    activate_at = deploy_workflow.index("activate_timer()")

    assert trap_at < flock_at < preflight_at < checkout_at
    assert checkout_at < migrate_at < smoke_at < health_at < activate_at
    assert "restore_sampling_state" in deploy_workflow
    assert "verify_terminal_timer_untouched" in deploy_workflow
    assert "arm_validated_terminal_timer" in deploy_workflow
    assert "sampler_validated=true" in deploy_workflow
    assert "Immutable sampler release ready" in deploy_workflow
    assert "git archive" in deploy_workflow
    assert '-e "s|__SAMPLER_DIR__|${sampler_candidate}|g"' in deploy_workflow
    assert "sampler_current" not in deploy_workflow
    assert 'chmod -R a-w,a+rX "${sampler_release_stage}"' in deploy_workflow
    assert "Existing SHA-pinned popularity samplers remain available during release mutations" in deploy_workflow
    assert "trap '' HUP INT TERM" in deploy_workflow
    assert 'trap \'exit_for_signal 1 HUP\' HUP' in deploy_workflow
    assert 'trap \'exit_for_signal 2 INT\' INT' in deploy_workflow
    assert 'trap \'exit_for_signal 15 TERM\' TERM' in deploy_workflow
    assert "sample_timer_was_enabled" in deploy_workflow
    assert "verify_timer_state" in deploy_workflow
    assert "sudo -n -l" in deploy_workflow
    assert "assert_unit_inactive" in deploy_workflow
    assert "systemd-analyze verify" in deploy_workflow
    assert "NextElapseUSecRealtime" in deploy_workflow
    assert "protected terminal-sampling window" in deploy_workflow
    assert "deploy_epoch < sample_end_epoch" in deploy_workflow
    assert "deploy_epoch < terminal_cutoff_epoch" in deploy_workflow
    assert 'systemctl start "${verify_service}"' in deploy_workflow
    assert 'deactivate_timer "${sample_timer}"' in deploy_workflow
    assert 'deactivate_timer "${final_timer}"' in deploy_workflow
    assert 'systemctl show -p Result --value "${baseline_service}"' in deploy_workflow
    assert 'systemctl show -p Result --value "${sample_service}"' in deploy_workflow
    assert "OnCalendar=2026-08-* *:0/5:00 Asia/Shanghai" in sample_timer
    assert "OnCalendar=2026-09-* *:0/5:00 Asia/Shanghai" in sample_timer
    assert "OnCalendar=2026-09-30 23:59:00 Asia/Shanghai" in final_timer


def test_popularity_sampler_disables_app_startup_side_effects_before_import():
    sampler = (ROOT / "scripts" / "sample_scheduler_popularity.py").read_text(
        encoding="utf-8"
    )
    app_import = sampler.index("from app import create_app")
    assert sampler.index('os.environ["ENABLE_BACKGROUND_TASKS"] = "false"') < app_import
    assert sampler.index('os.environ["AUTO_INIT_ON_STARTUP"] = "false"') < app_import
    assert sampler.index('os.environ["PYTHONDONTWRITEBYTECODE"] = "1"') < app_import

    for service_name in (
        "unikorn-scheduler-popularity-baseline.service.in",
        "unikorn-scheduler-popularity-sample.service.in",
        "unikorn-scheduler-popularity-final.service.in",
        "unikorn-scheduler-popularity-verify.service.in",
    ):
        service = (ROOT / "deploy" / "systemd" / service_name).read_text(encoding="utf-8")
        assert "Environment=ENABLE_BACKGROUND_TASKS=false" in service
        assert "Environment=AUTO_INIT_ON_STARTUP=false" in service
        assert "Environment=PYTHONDONTWRITEBYTECODE=1" in service
        assert "WorkingDirectory=__SAMPLER_DIR__" in service
        assert "EnvironmentFile=-__APP_ENV_FILE__" in service
        assert "ExecStart=__PYTHON__ __SAMPLER_DIR__/scripts/sample_scheduler_popularity.py" in service
        assert "--expected-database prod_unikorn" in service
        assert "TimeoutStartSec=" in service


def test_terminal_sampler_is_bounded_retried_and_exactly_verified():
    final_service = (
        ROOT / "deploy" / "systemd" / "unikorn-scheduler-popularity-final.service.in"
    ).read_text(encoding="utf-8")

    assert "--terminal --terminal-tolerance-seconds 120 --lock-wait-seconds 5" in final_service
    assert "--verify-terminal" in final_service
    assert "Restart=on-failure" in final_service
    assert "RestartSec=5s" in final_service
    assert "TimeoutStartSec=45s" in final_service
    assert "--at" not in final_service


def test_redeploy_baseline_does_not_block_regular_freshness_recovery():
    baseline_service = (
        ROOT / "deploy" / "systemd" / "unikorn-scheduler-popularity-baseline.service.in"
    ).read_text(encoding="utf-8")
    sample_service = (
        ROOT / "deploy" / "systemd" / "unikorn-scheduler-popularity-sample.service.in"
    ).read_text(encoding="utf-8")

    assert "--baseline" in baseline_service
    assert "--verify-freshness-seconds" not in baseline_service
    assert "--verify-freshness-seconds 600" in sample_service


def test_dispatch_workflows_do_not_bypass_the_allowlisted_operation_runner():
    forbidden_direct_runners = (
        "python -m app.scripts.migrate_scheduler_data",
        "python -m app.scripts.migrate_course_domain",
    )

    for workflow_path in (ROOT / ".github" / "workflows").glob("*.yml"):
        workflow = workflow_path.read_text(encoding="utf-8")
        if "workflow_dispatch" not in workflow:
            continue
        for forbidden_runner in forbidden_direct_runners:
            assert forbidden_runner not in workflow
