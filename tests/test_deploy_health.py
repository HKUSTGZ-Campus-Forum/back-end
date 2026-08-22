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
    assert set(revisions) - parents == {"20260822_sso_onboarding"}


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


def test_dev_deploy_disables_runtime_initializers_and_verifies_exact_migration_head():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy.yml"
    ).read_text(encoding="utf-8")

    backup_at = deploy_workflow.index(
        "app.scripts.create_verified_database_backup"
    )
    expected_heads_at = deploy_workflow.index("expected_candidate_heads=")
    migrate_at = deploy_workflow.index("flask db upgrade heads")
    current_at = deploy_workflow.index("flask db current")
    init_at = deploy_workflow.index("python -m app.scripts.init_db")
    restart_at = deploy_workflow.index('systemctl restart "$service_name"')

    assert (
        backup_at
        < expected_heads_at
        < migrate_at
        < current_at
        < init_at
        < restart_at
    )
    assert deploy_workflow.count(
        "AUTO_INIT_ON_STARTUP=false ENABLE_BACKGROUND_TASKS=false"
    ) >= 3
    assert "Expected exactly one candidate Alembic head" in deploy_workflow
    assert 'if [[ "${live_heads}" != "${expected_candidate_heads}" ]]' in deploy_workflow
    assert "Database did not reach the exact candidate Alembic head" in deploy_workflow


def test_sso_onboarding_migration_uses_rollout_time_for_existing_users():
    migration = (
        VERSION_DIR / "20260822_sso_onboarding.py"
    ).read_text(encoding="utf-8")

    assert "SET onboarding_completed_at = CURRENT_TIMESTAMP" in migration
    assert "WHERE onboarding_completed_at IS NULL" in migration
    assert "COALESCE(updated_at, created_at" not in migration
    assert 'if "onboarding_completed_at" in columns:\n        return' not in migration


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
    sudo_preflight_at = deploy_workflow.index(
        'require_sudo_permission /usr/bin/systemctl stop "${service_name}"'
    )
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


def test_production_deploy_does_not_run_routine_seed_or_legacy_backfill():
    deploy_workflow = (ROOT / ".github" / "workflows" / "deploy-backend-prod.yml").read_text(encoding="utf-8")

    assert "app.scripts.backfill_legacy_scheduler_offerings" not in deploy_workflow
    assert "app.scripts.init_db" not in deploy_workflow


def test_production_api_deploy_is_journaled_backup_first_and_forward_recoverable():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")
    helper = (ROOT / "tools" / "production_deploy_journal.py").read_text(
        encoding="utf-8"
    )
    remote_script = deploy_workflow.split(
        "        script: |", maxsplit=1
    )[1].split("    - name: Verify the public production API", maxsplit=1)[0]

    prepare_at = remote_script.index('prepare --target-sha "${DEPLOY_SHA}"')
    disk_at = remote_script.index(
        'database_size="$(printf', prepare_at
    )
    backup_at = remote_script.index(
        "app.scripts.create_verified_database_backup"
    )
    backup_started_at = remote_script.index("journal_advance FINAL_BACKUP_STARTED")
    backup_verified_at = remote_script.index("journal_advance FINAL_BACKUP_VERIFIED")
    stop_requested_at = remote_script.index("journal_advance SERVICE_STOP_REQUESTED")
    stop_at = remote_script.index(
        'systemctl stop "${service_name}"', stop_requested_at
    )
    checkout_requested_at = remote_script.index(
        "journal_advance CHECKOUT_ACTIVATION_REQUESTED"
    )
    checkout_at = remote_script.index('git checkout --detach "${DEPLOY_SHA}"')
    migration_started_at = remote_script.index("journal_advance MIGRATION_STARTED")
    migrate_at = remote_script.index("flask db upgrade heads")
    start_requested_at = remote_script.index("journal_advance CANDIDATE_START_REQUESTED")
    start_at = remote_script.index('systemctl start "${service_name}"', start_requested_at)
    local_health_at = remote_script.index("Local scheduler API verified")
    healthy_at = remote_script.index("journal_advance HEALTHY")
    attach_at = remote_script.index('git checkout -B production "${DEPLOY_SHA}"')
    committed_at = remote_script.index("journal_advance COMMITTED")

    inspect_at = remote_script.index('"${deploy_journal}" inspect')
    protected_at = remote_script.index(
        'if (( deploy_epoch >= protected_start_epoch'
    )
    api_preflight_at = remote_script.index(
        'require_sudo_permission /usr/bin/systemctl stop "${service_name}"'
    )
    identity_at = remote_script.rindex(
        "assert_recorded_database_identity", 0, migrate_at
    )
    rollback_health_at = remote_script.index('"${local_api_url}"', remote_script.index("restore_api_before_forward_boundary()"))
    archive_abort_at = remote_script.index("archive-aborted", rollback_health_at)

    assert inspect_at < protected_at < api_preflight_at
    assert prepare_at < disk_at < stop_requested_at < stop_at
    assert stop_at < backup_started_at < backup_at < backup_verified_at
    assert backup_verified_at < checkout_requested_at < checkout_at
    assert checkout_at < migration_started_at < migrate_at
    assert identity_at < migrate_at
    assert migrate_at < start_requested_at < start_at < local_health_at
    assert local_health_at < healthy_at < attach_at < committed_at
    assert "minimum_free_reserve_bytes=1073741824" in remote_script
    assert "verify-backup --target-sha" in remote_script
    candidate_stage_at = remote_script.index(
        'candidate_stage="$(mktemp -d /tmp/unikorn-api-candidate.'
    )
    candidate_backup_at = remote_script.index(
        'cd "${candidate_stage}"', backup_started_at
    )
    candidate_cleanup_at = remote_script.index(
        'rm -rf -- "${candidate_stage}"', candidate_backup_at
    )
    assert candidate_stage_at < candidate_backup_at < backup_at < candidate_cleanup_at
    assert 'Production backup directory has unsafe metadata.' in remote_script
    assert 'Refusing to remove an unsafe unbound final backup path.' in remote_script
    backup_root_guard_at = remote_script.index(
        '[[ -L "${backup_dir}" || ! -d "${backup_dir}"'
    )
    retry_remove_at = remote_script.index('rm -- "${database_backup}"')
    assert backup_root_guard_at < backup_started_at < retry_remove_at
    assert "expected_candidate_heads='20260819_campus_oidc'" in remote_script
    assert "requirements.txt requirements.lock" in remote_script
    assert "refusing to mutate the shared production venv" in remote_script
    assert "pip install" not in remote_script
    assert "sync_api_transaction_state" in remote_script
    assert "api_forward_only=true" in remote_script
    assert "archive-aborted" in remote_script
    assert rollback_health_at < archive_abort_at
    assert 'if [[ "${api_recovery_mode}" == "true" ]]' in remote_script
    assert 'skip_sampler_activation=true' in remote_script
    assert 'git cat-file -e "${DEPLOY_SHA}^{commit}"' in remote_script
    assert 'system_identifier FROM pg_control_system()' in remote_script
    # Do not feed external command output or unbounded decimal strings into
    # Bash arithmetic. The tested Python helper uses statvfs and arbitrary-size
    # integers, and every capacity gate invokes it directly under errexit.
    assert "df -PB1 --output" not in remote_script
    assert "--payload-bytes" in remote_script
    assert "--reserve-bytes" in remote_script
    assert remote_script.count('"${candidate_stage}/tools/check_backup_capacity.py"') == 3
    assert "required_bytes=$((" not in remote_script
    assert "available_bytes <" not in remote_script

    # The deploy identity cannot dereference /proc/<www-data pid>/cwd. systemd's
    # effective WorkingDirectory property is readable without widening sudo or
    # procfs permissions and is the source used to start the service process.
    assert 'systemctl show -p WorkingDirectory --value "${service_name}"' in remote_script
    assert 'systemctl show -p RootDirectory --value "${service_name}"' in remote_script
    assert 'systemctl show -p RootImage --value "${service_name}"' in remote_script
    assert remote_script.count('if ! api_main_pid="$(/usr/bin/systemctl show') == 1
    assert remote_script.count('! api_working_directory="$(/usr/bin/systemctl show') == 1
    assert remote_script.count('! api_root_directory="$(/usr/bin/systemctl show') == 1
    assert remote_script.count('! api_root_image="$(/usr/bin/systemctl show') == 1
    assert "Unable to verify the production API service launch context." in remote_script
    assert '"${api_working_directory}" != "${app_dir}"' in remote_script
    assert '-n "${api_root_directory}"' in remote_script
    assert '-n "${api_root_image}"' in remote_script
    assert 'readlink -e "/proc/${api_main_pid}/cwd"' not in remote_script
    assert remote_script.count("assert_api_service_checkout") == 3

    for phase in (
        "PREPARED",
        "SERVICE_STOP_REQUESTED",
        "SERVICE_STOPPED",
        "FINAL_BACKUP_STARTED",
        "FINAL_BACKUP_VERIFIED",
        "CHECKOUT_ACTIVATION_REQUESTED",
        "CANDIDATE_CHECKED_OUT",
        "MIGRATION_STARTED",
        "DB_AT_TARGET",
        "CANDIDATE_START_REQUESTED",
        "CANDIDATE_STARTED",
        "HEALTHY",
        "COMMITTED",
    ):
        assert f'"{phase}"' in helper
    assert "os.O_NOFOLLOW" in helper
    assert "os.fsync" in helper
    assert "verify_backup" in helper
    assert "cannot abort a deployment at or beyond migration start" in helper


def test_production_deploy_does_not_mutate_root_owned_environment_file():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")

    assert "CAMPUS_SSO_CLIENT_SECRET: ${{ secrets.CAMPUS_SSO_CLIENT_SECRET }}" not in deploy_workflow
    assert "CAMPUS_SSO_CLIENT_SECRET,CAMPUS_SSO_ISSUER" not in deploy_workflow
    assert 'output_lines.append(f"{key}={values[key]}")' not in deploy_workflow
    assert "production environment file is not deployment-owned" not in deploy_workflow


def test_production_deploy_activates_bounded_popularity_sampling_with_user_crontab():
    deploy_workflow = (ROOT / ".github" / "workflows" / "deploy-backend-prod.yml").read_text(
        encoding="utf-8"
    )
    flock_at = deploy_workflow.index("flock -n 9")
    trap_at = deploy_workflow.index("trap cleanup_deployment EXIT")
    checkout_at = deploy_workflow.index('git checkout --detach "${DEPLOY_SHA}"')
    migrate_at = deploy_workflow.index("flask db upgrade heads")
    health_at = deploy_workflow.index("Local scheduler API verified")
    release_at = deploy_workflow.index("Building an immutable popularity sampler release")
    baseline_at = deploy_workflow.index("Taking the one permitted deployment baseline")
    activate_call_at = deploy_workflow.rindex("activate_sampling_crontab")
    activate_at = deploy_workflow.index('crontab "${crontab_candidate}"')

    assert trap_at < flock_at < checkout_at < migrate_at < health_at
    assert health_at < release_at < baseline_at < activate_call_at
    assert "restore_sampling_crontab" in deploy_workflow
    assert "Immutable sampler release ready" in deploy_workflow
    assert 'test -f "${sampler_candidate}/tools/render_scheduler_popularity_crontab.py"' in deploy_workflow
    assert 'extract_sampler_release "${sampler_release_stage}"' in deploy_workflow
    assert 'extract_sampler_release "${sampler_verify_stage}"' in deploy_workflow
    assert 'git archive "${DEPLOY_SHA}" -- \\' in deploy_workflow
    assert "              app \\" in deploy_workflow
    assert "              scripts/run_scheduler_popularity_cron.py \\" in deploy_workflow
    assert "              scripts/sample_scheduler_popularity.py \\" in deploy_workflow
    assert "              tools/render_scheduler_popularity_crontab.py | tar -x" in deploy_workflow
    assert deploy_workflow.count('git archive "${DEPLOY_SHA}" | tar') == 1
    assert "sampler_current" not in deploy_workflow
    assert 'chmod -R a-w,a+rX "${sampler_release_stage}"' in deploy_workflow
    assert 'ln -s "${app_dir}/venv" "${sampler_release_stage}/venv"' in deploy_workflow
    assert 'diff -qr --no-dereference' in deploy_workflow
    assert '--exclude=venv --exclude=.unikorn-commit' in deploy_workflow
    assert "trap '' HUP INT TERM" in deploy_workflow
    assert 'trap \'exit_for_signal 1 HUP\' HUP' in deploy_workflow
    assert 'trap \'exit_for_signal 2 INT\' INT' in deploy_workflow
    assert 'trap \'exit_for_signal 15 TERM\' TERM' in deploy_workflow
    assert "protected terminal-sampling window" in deploy_workflow
    assert "deploy_epoch < sample_end_epoch" in deploy_workflow
    assert "deploy_epoch < terminal_cutoff_epoch" in deploy_workflow
    assert "*/5 * 1-31 8,9 *" in deploy_workflow
    assert "59 23 30 9 *" in deploy_workflow
    assert 'test "${env_uid}:${env_gid}:${env_mode}" = "0:33:640"' in deploy_workflow
    assert "systemctl enable" not in deploy_workflow
    assert "Refusing user-cron activation while legacy sampler unit" in deploy_workflow
    assert "unikorn-scheduler-popularity-final.timer" in deploy_workflow
    assert deploy_workflow.count("assert_legacy_sampler_units_absent") == 4
    migration = deploy_workflow.index("flask db upgrade heads")
    pre_migration_guard = deploy_workflow.index(
        "assert_legacy_sampler_units_absent",
        deploy_workflow.index('if [[ "${api_recovery_mode}" != "true" ]]'),
    )
    sampler_start = deploy_workflow.rindex("command -v crontab")
    legacy_guard = deploy_workflow.index(
        "assert_legacy_sampler_units_absent", sampler_start
    )
    release_build = deploy_workflow.index(
        "Building an immutable popularity sampler release", sampler_start
    )
    activation_helper_at = deploy_workflow.index("activate_sampling_crontab()")
    final_legacy_guard = deploy_workflow.index(
        "assert_legacy_sampler_units_absent", activation_helper_at
    )
    assert pre_migration_guard < migration
    assert sampler_start < legacy_guard < release_build
    assert final_legacy_guard < activate_at
    assert release_build < activate_call_at
    assert deploy_workflow.index('test "${journal_phase}" = "COMMITTED"') < activate_call_at
    assert "/usr/bin/systemctl show --all" in deploy_workflow
    assert "-p LoadState -p ActiveState -p UnitFileState" in deploy_workflow
    assert "seen_load_state" in deploy_workflow
    assert "seen_active_state" in deploy_workflow
    assert "seen_unit_file_state" in deploy_workflow
    assert "systemctl is-enabled" not in deploy_workflow
    assert "Unable to verify legacy sampler unit" in deploy_workflow
    assert '-n "${systemd_unit_file_state}"' in deploy_workflow
    assert '"${systemd_load_state}" != "loaded"' in deploy_workflow


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
    assert "date -d" not in deploy_workflow
    assert "readonly sample_end_epoch=\"$(date" not in deploy_workflow
    assert "readonly terminal_cutoff_epoch=\"$(date" not in deploy_workflow
    assert "backend-mutations.lock" in deploy_workflow
    assert "/tmp/unikorn-backend-mutation-production.lock" not in deploy_workflow


def test_production_crontab_install_is_verified_and_rollback_safe():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")
    activation_helper = deploy_workflow.index("activate_sampling_crontab()")
    capture = deploy_workflow.index('crontab -l > "${crontab_before}"')
    race_check = deploy_workflow.index('cmp -s "${crontab_before}" "${crontab_before}.verify"')
    activation_call = deploy_workflow.rindex("activate_sampling_crontab")
    install = deploy_workflow.index('crontab "${crontab_candidate}"', activation_helper)
    readback = deploy_workflow.index('crontab -l > "${crontab_readback}"')
    hash_check = deploy_workflow.index('sha256sum "${crontab_readback}"')
    smoke = deploy_workflow.index('"${sampler_args[@]}" --mode status >/dev/null')
    commit = deploy_workflow.index("crontab_mutated=false", install)
    mutation_guard = deploy_workflow.rindex(
        "crontab_mutated=true", activation_helper, install
    )
    assert mutation_guard < install < readback < hash_check < smoke < commit
    assert capture < race_check < activation_call
    assert "restore_sampling_crontab()" in deploy_workflow
    assert 'crontab "${crontab_before}" || return 1' in deploy_workflow
    assert 'cmp -s "${crontab_before}" "${crontab_stage}/rollback-read"' in deploy_workflow
    assert '"${installed_crontab_sha}"' in deploy_workflow
    original_hash = deploy_workflow.index('original_crontab_sha="$(sha256sum')
    candidate_hash_bound = deploy_workflow.index(
        'installed_crontab_sha="${expected_crontab_sha}"'
    )
    assert capture < original_hash < candidate_hash_bound < activation_call
    assert '"${rollback_current_sha}" == "${original_crontab_sha}"' in deploy_workflow
    assert '"${rollback_current_sha}" != "${installed_crontab_sha}"' in deploy_workflow
    assert "Refusing rollback because the user crontab changed after candidate activation" in deploy_workflow
    assert '--existing "${crontab_before}" --replacement' in deploy_workflow
    assert '--existing "${crontab_before}" --remove' in deploy_workflow


def test_production_redeploy_never_rebaselines_existing_history():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")
    status = deploy_workflow.index('--mode status)"')
    baseline = deploy_workflow.index('--mode baseline)"')
    fresh = deploy_workflow.index('--mode verify-freshness')
    ended = deploy_workflow.index('--mode verify-terminal')
    refuse = deploy_workflow.index("never re-baselining existing history")
    assert status < baseline < fresh < ended < refuse
    assert '[[ "${sampling_state}" == "not_started" ]]' in deploy_workflow
    assert 'elif [[ "${sampling_state}" == "fresh" ]]' in deploy_workflow
    assert 'elif [[ "${sampling_state}" == "ended_complete" ]]' in deploy_workflow


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

    launcher = (ROOT / "scripts" / "run_scheduler_popularity_cron.py").read_text(
        encoding="utf-8"
    )
    assert '"AUTO_INIT_ON_STARTUP": "false"' in launcher
    assert '"ENABLE_BACKGROUND_TASKS": "false"' in launcher
    assert '"PYTHONDONTWRITEBYTECODE": "1"' in launcher
    assert '"PYTHONNOUSERSITE": "1"' in launcher
    assert 'EXPECTED_DATABASE = "prod_unikorn"' in launcher


def test_terminal_sampler_is_bounded_retried_and_exactly_verified():
    launcher = (ROOT / "scripts" / "run_scheduler_popularity_cron.py").read_text(
        encoding="utf-8"
    )
    assert "TERMINAL_LAUNCH_TOLERANCE_SECONDS = 55" in launcher
    assert '"--terminal"' in launcher
    assert '"--commit-deadline"' in launcher
    assert '"--lock-wait-seconds",\n            "0"' in launcher
    assert 'command.append("--verify-terminal")' in launcher


def test_redeploy_baseline_does_not_block_regular_freshness_recovery():
    deploy_workflow = (
        ROOT / ".github" / "workflows" / "deploy-backend-prod.yml"
    ).read_text(encoding="utf-8")
    assert 'if [[ "${sampling_state}" == "not_started" ]]' in deploy_workflow
    assert 'elif [[ "${sampling_state}" == "fresh" ]]' in deploy_workflow
    assert "Taking the one permitted deployment baseline" in deploy_workflow
    assert "never re-baselining existing history" in deploy_workflow


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
