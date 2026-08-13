import ast
from datetime import datetime
import hashlib
from pathlib import Path
import re
from zoneinfo import ZoneInfo


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


def test_production_deploy_pins_remote_host_fingerprint():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")

    assert "fingerprint: ${{ secrets.PROD_SSH_FINGERPRINT }}" in deploy_workflow


def test_dev_migration_checkout_reconciliation_is_two_phase_and_fixed_scope():
    deploy_workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )
    workflow = (
        ROOT / ".github" / "workflows" / "reconcile-dev-migration-checkout.yml"
    ).read_text(encoding="utf-8")
    helper = (
        ROOT / "tools" / "reconcile_dev_migration_checkout.py"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "group: backend-mutations-dev" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "exec 9>/data/dev_unikorn/backend-mutations-dev.lock" in deploy_workflow
    assert "/tmp/unikorn-backend-mutation-dev.lock" not in deploy_workflow
    assert deploy_workflow.index("umask 077") < deploy_workflow.index(
        "exec 9>/data/dev_unikorn/backend-mutations-dev.lock"
    )
    assert "refs/heads/main" in workflow
    assert "--target dev" in workflow
    assert "secrets.DEV_HOST" in workflow
    assert "secrets.DEV_USER" in workflow
    assert "secrets.DEV_SSH_KEY" in workflow
    assert "QUARANTINE_DEV_LEGACY_MIGRATIONS" in workflow
    assert "expected_aggregate_sha256" in workflow
    assert "flock -n 9" not in workflow
    assert "/tmp/unikorn-backend-mutation-dev.lock" not in workflow
    assert "git clean" not in workflow
    assert "rm -rf" not in workflow
    assert "/data/dev_unikorn/back-end" in helper
    assert "/data/dev_unikorn/quarantine/legacy-migrations" in helper
    assert "ast.literal_eval" in helper
    assert "os.rename" in helper
    assert "PREPARED.json" in helper
    assert "COMMITTED.json" in helper
    assert "backend-mutations-dev.lock" in helper
    assert "O_NOFOLLOW" in helper
    assert "fcntl.flock" in helper
    assert "src_dir_fd=" in helper
    assert "dst_dir_fd=" in helper
    assert "os.fsync" in helper
    assert "live_current_revisions" in helper
    assert "live_current_allowlisted_revisions" in helper
    assert "live_current_unknown_revisions" in helper
    assert "repository_sha" in helper
    assert "helper_sha256" in helper
    assert "committed_revisions" in helper
    assert "committed_heads" in helper
    assert "committed_allowlisted_revision_duplicates" in helper
    assert "committed_allowlisted_revision_references" in helper
    assert '"depends_on"' in helper
    assert "os.link" in helper
    assert "SELECT version_num FROM alembic_version" in helper
    assert "dev_unikorn" in helper
    assert '"production"' in helper
    assert "/data/prod_unikorn/back-end" in helper
    assert "000000000000_create_oauth_tables.py" in helper
    assert "git clean" not in helper
    assert "rmtree" not in helper


def test_production_migration_checkout_reconciliation_is_two_phase_and_fixed_scope():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")
    workflow = (
        ROOT / ".github" / "workflows" / "reconcile-prod-migration-checkout.yml"
    ).read_text(encoding="utf-8")
    helper = (
        ROOT / "tools" / "reconcile_dev_migration_checkout.py"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "group: backend-mutations-production" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "refs/heads/main" in workflow
    assert "secrets.PROD_SSH_HOST" in workflow
    assert "secrets.PROD_SSH_USER" in workflow
    assert "secrets.PROD_SSH_KEY" in workflow
    assert "environment: production" in workflow
    assert "--target production" in workflow
    assert "QUARANTINE_PRODUCTION_LEGACY_OAUTH_MIGRATION" in workflow
    assert "expected_aggregate_sha256" in workflow
    assert "git clean" not in workflow
    assert "rm -rf" not in workflow
    assert "/data/prod_unikorn/back-end" in helper
    assert "/data/prod_unikorn/back-end/.git/unikorn-operations/" in helper
    assert "000000000000_create_oauth_tables.py" in helper
    assert "ast.literal_eval" in helper
    assert "PREPARED.json" in helper
    assert "COMMITTED.json" in helper
    assert "SELECT version_num FROM alembic_version" in helper
    assert "prod_unikorn" in helper
    assert "git clean" not in helper
    assert "rmtree" not in helper
    assert "/data/prod_unikorn/back-end/.git/unikorn-operations/" in helper
    assert "backend-mutations.lock" in helper
    assert 'readonly production_git_dir="${app_dir}/.git"' in deploy_workflow
    assert (
        'readonly production_lock_path="${production_ops_dir}/backend-mutations.lock"'
        in deploy_workflow
    )
    assert "git rev-parse --absolute-git-dir" in deploy_workflow
    assert 'mkdir -- "${production_ops_dir}"' in deploy_workflow
    assert deploy_workflow.index("umask 077") < deploy_workflow.index(
        'exec 9<>"${production_lock_path}"'
    )
    assert deploy_workflow.index('exec 9<>"${production_lock_path}"') < (
        deploy_workflow.index("umask 022")
    )
    assert "lock_fd_metadata" in deploy_workflow
    assert "lock_path_metadata" in deploy_workflow
    assert "expected_lock_safety" in deploy_workflow
    assert '[[ -L "${production_lock_path}"' in deploy_workflow
    assert '-L "${production_git_dir}"' in deploy_workflow
    assert "owner_uid=%s, effective_uid=%s, group_gid=%s, effective_gid=%s" in deploy_workflow
    assert "forbidden_write_bits=0022" in deploy_workflow
    assert '-L "${production_ops_dir}"' in deploy_workflow
    assert "verify-transactions" in deploy_workflow
    assert "UNIKORN_BACKEND_MUTATION_LOCK_FD=9" in deploy_workflow
    flock_at = deploy_workflow.index("flock -n 9")
    verify_at = deploy_workflow.index("--mode verify-transactions")
    sudo_preflight_at = deploy_workflow.index("Preflighting non-interactive")
    assert flock_at < verify_at < sudo_preflight_at


def test_dev_data_parent_hardening_is_exact_two_phase_and_non_recursive():
    workflow = (ROOT / ".github" / "workflows" / "harden-dev-data-parent.yml").read_text(
        encoding="utf-8"
    )
    helper = (ROOT / "tools" / "harden_dev_data_parent.py").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "group: backend-mutations-dev" in workflow
    assert "refs/heads/main" in workflow
    assert "expected_aggregate_sha256" in workflow
    assert "HARDEN_DEV_DATA_PARENT_0777_TO_0755" in workflow
    assert "secrets.DEV_HOST" in workflow
    assert 'Path("/data/dev_unikorn")' in helper
    assert 'APP_NAME = "back-end"' in helper
    assert 'LOCK_NAME = "backend-mutations-dev.lock"' in helper
    assert "EXPECTED_MODE = 0o777" in helper
    assert "INTERMEDIATE_MODE = 0o1777" in helper
    assert "TARGET_MODE = 0o755" in helper
    assert "os.O_NOFOLLOW" in helper
    assert "fcntl.flock" in helper
    assert "os.fchmod(parent_fd, TARGET_MODE)" in helper
    assert "os.fsync(parent_fd)" in helper
    assert "rmtree" not in helper
    assert "unlink" not in helper


def test_dev_data_container_hardening_is_exact_two_phase_and_non_recursive():
    workflow = (
        ROOT / ".github" / "workflows" / "harden-dev-data-container.yml"
    ).read_text(encoding="utf-8")
    helper = (ROOT / "tools" / "harden_dev_data_container.py").read_text(
        encoding="utf-8"
    )
    sudoers = (
        ROOT / "deploy" / "sudoers" / "unikorn-harden-dev-data-container.in"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "group: backend-mutations-dev" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "refs/heads/main" in workflow
    assert "expected_aggregate_sha256" in workflow
    assert "HARDEN_DEV_DATA_CONTAINER_0777_TO_1777" in workflow
    assert "secrets.DEV_HOST" in workflow
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
    assert "appleboy/ssh-action@0ff4204d59e8e51228ff73bce53f80d53301dee2" in workflow
    assert "/usr/bin/sudo -n -l /usr/bin/chmod 1777 -- /data" in workflow
    assert 'ROOT_PATH = Path("/")' in helper
    assert 'DATA_PATH = Path("/data")' in helper
    assert 'DEV_PARENT_NAME = "dev_unikorn"' in helper
    assert 'APP_NAME = "back-end"' in helper
    assert 'LOCK_NAME = "backend-mutations-dev.lock"' in helper
    assert "EXPECTED_MODE = 0o777" in helper
    assert "TARGET_MODE = 0o1777" in helper
    assert "os.O_NOFOLLOW" in helper
    assert "os.O_CLOEXEC" in helper
    assert "dir_fd=" in helper
    assert "fcntl.LOCK_EX" in helper
    assert "subprocess.run" in helper
    assert '"/usr/bin/sudo"' in helper
    assert '"/usr/bin/chmod"' in helper
    assert "os.fsync(data_fd)" in helper
    assert "expected_release_sha" in helper
    assert "rmtree" not in helper
    assert "unlink" not in helper
    assert "os.rename" not in helper
    assert "NOPASSWD: /usr/bin/chmod 1777 -- /data" in sudoers


def test_dev_data_parent_accepts_only_safe_shared_root_container():
    helper = (ROOT / "tools" / "harden_dev_data_parent.py").read_text(
        encoding="utf-8"
    )

    assert "safe_non_writable_container" in helper
    assert "safe_shared_container" in helper
    assert "container.st_uid == 0" in helper
    assert "container.st_gid == 0" in helper
    assert "container_mode == 0o1777" in helper


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


def test_production_sampling_cutoff_epochs_are_exact_and_parse_fail_closed():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")

    sample_match = re.search(r"readonly sample_end_epoch=(\d+)", deploy_workflow)
    terminal_match = re.search(
        r"readonly terminal_cutoff_epoch=(\d+)", deploy_workflow
    )
    assert sample_match is not None
    assert terminal_match is not None

    zone = ZoneInfo("Asia/Shanghai")
    expected_sample = int(datetime(2026, 9, 30, 23, 55, tzinfo=zone).timestamp())
    expected_terminal = int(datetime(2026, 9, 30, 23, 59, tzinfo=zone).timestamp())
    assert int(sample_match.group(1)) == expected_sample
    assert int(terminal_match.group(1)) == expected_terminal
    assert expected_terminal - expected_sample == 4 * 60
    assert "terminal_cutoff_epoch <= sample_end_epoch" in deploy_workflow
    assert "timer_next_epoch()" in deploy_workflow
    assert "LC_ALL=C TZ=UTC0 /usr/bin/systemctl show" in deploy_workflow
    assert (
        'LC_ALL=C TZ=UTC0 /usr/bin/date --date="${next_elapse}" +%s'
        in deploy_workflow
    )
    assert "date -d" not in deploy_workflow
    assert 'activate_timer "${final_timer}" "${terminal_cutoff_epoch}"' in deploy_workflow
    assert "readonly sample_end_epoch=\"$(date" not in deploy_workflow
    assert "readonly terminal_cutoff_epoch=\"$(date" not in deploy_workflow
    assert "backend-mutations.lock" in deploy_workflow
    assert "/tmp/unikorn-backend-mutation-production.lock" not in deploy_workflow


def test_production_first_install_loads_every_reset_unit_before_resetting_failures():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")

    staging = deploy_workflow[
        deploy_workflow.index("Staging scheduler popularity sampling units..."):
        deploy_workflow.index("Smoke-testing popularity sampling")
    ]
    reset_at = staging.index("systemctl reset-failed")
    daemon_reload_at = staging.rindex("systemctl daemon-reload", 0, reset_at)
    install_loop_at = staging.rindex("for unit_name in \\", 0, daemon_reload_at)
    install_loop = staging[install_loop_at:staging.index("done", install_loop_at)]
    reset_command = staging[reset_at:staging.index("\n", reset_at)]

    installed_units = set(re.findall(r'"\$\{(\w+)\}"', install_loop))
    reset_units = set(re.findall(r'"\$\{(\w+)\}"', reset_command))

    assert reset_units
    assert reset_units <= installed_units
    assert "final_service" not in installed_units
    assert "final_service" not in reset_units
    assert "final_timer" not in installed_units
    assert '"${unit_stage}/${unit_name}" "/etc/systemd/system/${unit_name}"' in install_loop
    assert install_loop_at < daemon_reload_at < reset_at

    preflight = deploy_workflow[
        deploy_workflow.index("Preflighting non-interactive"):
        deploy_workflow.index("Required sudo permissions are available")
    ]
    assert (
        'require_sudo_permission /usr/bin/systemctl reset-failed \\\n'
        '            "${baseline_service}" "${sample_service}" "${verify_service}"'
        in preflight
    )
    assert (
        'require_sudo_permission /usr/bin/systemctl reset-failed "${final_service}"'
        in preflight
    )

    before_cutoff_at = deploy_workflow.index("if (( deploy_epoch < sample_end_epoch ))")
    terminal_only_at = deploy_workflow.index(
        "elif (( deploy_epoch < terminal_cutoff_epoch ))"
    )
    campaign_complete_at = deploy_workflow.index(
        'else\n            echo "Popularity sampling campaign is complete'
    )

    def assert_terminal_unit_installed_before_activation(branch):
        final_service_install_at = branch.index(
            '"${unit_stage}/${final_service}" "/etc/systemd/system/${final_service}"'
        )
        final_timer_install_at = branch.index(
            '"${unit_stage}/${final_timer}" "/etc/systemd/system/${final_timer}"'
        )
        terminal_reload_at = branch.index("systemctl daemon-reload")
        terminal_reset_at = branch.index('systemctl reset-failed "${final_service}"')
        terminal_activate_at = branch.index('activate_timer "${final_timer}"')

        assert (
            max(final_service_install_at, final_timer_install_at)
            < terminal_reload_at
            < terminal_reset_at
            < terminal_activate_at
        )

    assert deploy_workflow.index("sampler_validated=true") < before_cutoff_at
    assert_terminal_unit_installed_before_activation(
        deploy_workflow[before_cutoff_at:terminal_only_at]
    )
    assert_terminal_unit_installed_before_activation(
        deploy_workflow[terminal_only_at:campaign_complete_at]
    )


def test_production_failure_restores_exact_predeployment_sampler_units_before_timer_state():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")

    capture_at = deploy_workflow.index(
        'unit_path="/etc/systemd/system/${unit_name}"'
    )
    mutation_at = deploy_workflow.index("sampler_units_mutated=true")
    smoke_at = deploy_workflow.index('systemctl start "${baseline_service}"')
    restore_function_at = deploy_workflow.index("restore_sampler_unit_files()")
    restore_call_at = deploy_workflow.index("if ! restore_sampler_unit_files; then")
    timer_rearm_at = deploy_workflow.index(
        'systemctl start "${sample_timer}"', restore_call_at
    )

    assert capture_at < mutation_at < smoke_at
    assert restore_function_at < restore_call_at < timer_rearm_at
    assert 'marker_path="${unit_backup_stage}/${unit_name}.present"' in deploy_workflow
    assert '/usr/bin/install -m 0600' in deploy_workflow
    assert 'sha256sum "${unit_backup_stage}/${unit_name}"' in deploy_workflow
    assert 'Sampler unit changed while it was being snapshotted' in deploy_workflow
    assert (
        '"${backup_path}" "${target_path}" || unit_restore_failed=true'
        in deploy_workflow
    )
    assert (
        'sudo -n /usr/bin/rm -- "${target_path}" || unit_restore_failed=true'
        in deploy_workflow
    )
    assert '"$(sha256sum "${target_path}" | awk' in deploy_workflow
    assert 'New sampler unit override remained after rollback' in deploy_workflow
    assert '"${unit_backup_stage}/${unit_name}.load-state"' in deploy_workflow
    assert '"${unit_backup_stage}/${unit_name}.fragment-path"' in deploy_workflow
    assert 'Sampler unit rollback mismatch for ${unit_name}' in deploy_workflow
    assert 'effective_fragment_matches=false' in deploy_workflow
    assert 'sampler_fragments_restored=true' in deploy_workflow
    assert 'leaving the regular timer stopped' in deploy_workflow
    assert 'preserving sampler unit rollback evidence at ${unit_backup_stage}' in deploy_workflow
    assert deploy_workflow.index(
        "systemctl daemon-reload || unit_restore_failed=true", restore_function_at
    ) < timer_rearm_at
    assert (
        'if [[ ! -f "${unit_path}" || -L "${unit_path}" ]]; then'
        in deploy_workflow
    )

    restored_units = deploy_workflow[
        restore_function_at:deploy_workflow.index(
            "restore_sampling_state()", restore_function_at
        )
    ]
    capture_loop_at = deploy_workflow.rindex("for unit_name in \\", 0, capture_at)
    captured_units = deploy_workflow[
        capture_loop_at:deploy_workflow.index(
            "sampling_state_captured=true", capture_at
        )
    ]
    for unit_variable in (
        "baseline_service",
        "sample_service",
        "verify_service",
        "sample_timer",
    ):
        assert f'"${{{unit_variable}}}"' in restored_units
        assert f'"${{{unit_variable}}}"' in captured_units


def test_production_sampler_unit_rollback_permissions_are_preflighted():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")
    preflight = deploy_workflow[
        deploy_workflow.index("Preflighting non-interactive"):
        deploy_workflow.index("Required sudo permissions are available")
    ]

    assert (
        'require_sudo_permission /usr/bin/install -m 0644 \\\n'
        '              "${unit_backup_stage}/${unit_name}" "/etc/systemd/system/${unit_name}"'
        in preflight
    )
    assert (
        'require_sudo_permission /usr/bin/rm -- "/etc/systemd/system/${unit_name}"'
        in preflight
    )


def test_first_install_rollback_removes_candidate_timer_enablement_before_fragment():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")
    restore = deploy_workflow[
        deploy_workflow.index("restore_sampling_state()"):
        deploy_workflow.index("verify_terminal_timer_untouched()")
    ]

    disable_at = restore.index('systemctl disable "${sample_timer}"')
    fragment_restore_at = restore.index("if ! restore_sampler_unit_files; then")
    assert disable_at < fragment_restore_at
    verify = deploy_workflow[
        deploy_workflow.index("verify_timer_state()"):
        deploy_workflow.index("restore_sampler_unit_files()")
    ]
    assert 'if unit_enabled "${timer_name}"; then' in verify
    assert "remained enabled without its pre-deployment unit" in verify


def test_production_quiesces_regular_sampler_immediately_before_unit_replacement():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")
    staging_at = deploy_workflow.index("Staging scheduler popularity sampling units...")
    install_at = deploy_workflow.index(
        '"${unit_stage}/${unit_name}" "/etc/systemd/system/${unit_name}"',
        staging_at,
    )
    install_loop_at = deploy_workflow.rindex("for unit_name in \\", staging_at, install_at)
    quiesce_start = deploy_workflow.rindex(
        "regular_sampler_quiesced=true", staging_at, install_loop_at
    )
    quiesce = deploy_workflow[quiesce_start:install_at]

    assert quiesce.index("regular_sampler_quiesced=true") < quiesce.index(
        'systemctl stop "${sample_timer}"'
    )
    assert 'systemctl stop "${sample_timer}"' in quiesce
    assert 'systemctl stop "${sample_service}"' in quiesce
    assert 'assert_unit_inactive "${sample_timer}"' in quiesce
    assert 'assert_unit_inactive "${sample_service}"' in quiesce
    assert "regular_sampler_quiesced=true" in quiesce
    restore = deploy_workflow[
        deploy_workflow.index("restore_sampling_state()"):
        deploy_workflow.index("verify_terminal_timer_untouched()")
    ]
    assert 'elif [[ "${regular_sampler_quiesced}" == "true" ]]' in restore
    rearm_at = restore.index('systemctl start "${sample_timer}"')
    assert restore.rindex(
        'if [[ "${sampler_fragments_restored}" == "true" ]]', 0, rearm_at
    ) < rearm_at


def test_production_protected_window_exceeds_remote_command_timeout_margin():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")

    assert "command_timeout: 35m" in deploy_workflow
    assert "terminal_cutoff_epoch - 45 * 60" in deploy_workflow
    assert "exceeding the" in deploy_workflow
    assert "35-minute hard timeout by ten minutes" in deploy_workflow


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
