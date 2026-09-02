from pathlib import Path
import importlib.util
import json
import os
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCHOOL = ROOT / "deploy" / "school"
WORKFLOW = ROOT / ".github" / "workflows" / "export-production-migration.yml"
SCHOOL_RELEASE_WORKFLOW = (
    ROOT / ".github" / "workflows" / "validate-school-production-release.yml"
)
SCHOOL_CANDIDATE_WORKFLOW = (
    ROOT / ".github" / "workflows" / "validate-school-production-candidate.yml"
)


def read(relative: str) -> str:
    return (SCHOOL / relative).read_text(encoding="utf-8")


def test_all_school_shell_scripts_are_executable_and_parse():
    scripts = sorted(SCHOOL.glob("*.sh"))
    assert scripts
    for script in scripts:
        assert script.stat().st_mode & 0o111, script
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_systemd_units_are_loopback_only_hardened_and_restartable():
    backend = read("systemd/unikorn-backend.service")
    frontend = read("systemd/unikorn-frontend.service")
    migrate = read("systemd/unikorn-migrate@.service")
    redis = read("systemd/unikorn-redis.service")
    for unit in (backend, frontend, redis):
        assert "NoNewPrivileges=true" in unit
        assert "ProtectSystem=strict" in unit
        assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in unit
        assert "Restart=on-failure" in unit
    assert "127.0.0.1:8001" in read("gunicorn.conf.py")
    assert "NITRO_HOST=127.0.0.1" in frontend
    assert "bind 127.0.0.1 ::1" in read("redis.conf")
    assert "port 6380" in read("redis.conf")
    assert "/etc/unikorn-redis/redis.conf" in redis
    assert "courseplan.service" not in backend + frontend + redis
    assert ".venv/bin/python -m gunicorn" in backend
    assert ".venv/bin/python -m flask db upgrade" in migrate
    assert ".venv/bin/gunicorn" not in backend
    assert ".venv/bin/flask" not in migrate
    assert "NUXT_TELEMETRY_DISABLED=1" in frontend
    assert "ReadWritePaths=/srv/unikorn/sisn-archive" in backend


def test_release_build_is_noninteractive_and_survives_atomic_stage_rename():
    deploy = read("deploy-release.sh")
    assert "export CI=1" in deploy
    assert "export NUXT_TELEMETRY_DISABLED=1" in deploy
    assert 'mv -T -- "${stage_path}" "${release_path}"' in deploy
    assert deploy.count("GIT_OPTIONAL_LOCKS=0 git -C") == 2
    assert "/run/lock/unikorn-school-production-deploy.lock" in deploy
    assert "another school production deployment is active" in deploy


def load_school_controller():
    path = SCHOOL / "school-production-controller.py"
    spec = importlib.util.spec_from_file_location("school_production_controller", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_school_production_manifest_is_strict_and_paired():
    controller = load_school_controller()
    manifest = json.loads(read("school-production-release.json"))
    assert controller.parse_manifest_text(json.dumps(manifest)) == manifest
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["backend_sha"])
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["frontend_sha"])

    unexpected = {**manifest, "command": "whoami"}
    with __import__("pytest").raises(controller.ReleaseBlocked):
        controller.parse_manifest_text(json.dumps(unexpected))

    approved_without_reference = {
        **manifest,
        "database_change": {"approved": True, "approval_reference": None},
    }
    with __import__("pytest").raises(controller.ReleaseBlocked):
        controller.parse_manifest_text(json.dumps(approved_without_reference))


def test_school_production_controller_has_fixed_trust_boundaries():
    controller = read("school-production-controller.py")
    installer = read("install-school-production-controller.sh")
    verifier = read("verify-local.sh")
    service = read("systemd/unikorn-school-production-deploy.service")
    timer = read("systemd/unikorn-school-production-deploy.timer")
    caller_workflow = SCHOOL_RELEASE_WORKFLOW.read_text(encoding="utf-8")
    candidate_workflow = SCHOOL_CANDIDATE_WORKFLOW.read_text(encoding="utf-8")
    workflow = caller_workflow + candidate_workflow

    assert 'CONTROL_BRANCH = "school-production"' in controller
    assert 'STATUS_CONTEXT = "school-production/validated"' in controller
    assert 'SENSITIVE_PREFIXES = ("migrations/", "app/data/")' in controller
    assert "merge-base" in controller
    assert "--is-ancestor" in controller
    assert "school-production may change only the release manifest" in controller
    assert "database-change approval is set" in controller
    assert "pause_sisn_timers" in controller
    assert "/usr/local/libexec/unikorn-school-deploy-release" in controller
    assert "eval(" not in controller
    assert "shell=True" not in controller
    assert controller.index("success.get(\"control_sha\")") < controller.index(
        "fetch_frontend_repository(frontend)"
    )

    assert "install -o root -g root -m 0755" in installer
    assert "systemd-analyze verify" in installer
    assert "systemctl is-active --quiet courseplan.service" in installer
    assert "ConditionPathExists=/usr/local/libexec/unikorn-school-production-controller" in service
    assert "ConditionPathIsExecutable" not in service
    assert "NoNewPrivileges=true" in service
    assert "PrivateTmp=true" in service
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK" in service
    assert "RestrictSUIDSGID=true" in service
    assert "runuser" not in verifier
    assert 'systemd-run --quiet --wait --collect --pipe' in verifier
    assert '--uid=unikorn' in verifier
    assert "ss -ltnH" in verifier
    assert "OnUnitInactiveSec=2min" in timer
    assert "RandomizedDelaySec=15s" in timer
    assert "Persistent=true" in timer

    assert "branches:" in workflow and "school-production" in workflow
    assert "statuses: write" in workflow
    assert "school-production/validated" in workflow
    assert "validate-school-production-candidate.yml@main" in caller_workflow
    assert "workflow_call:" in candidate_workflow
    backend_test_step = workflow.split("- name: Test backend candidate", 1)[1].split(
        "- name: Verify backend migrations on pristine PostgreSQL", 1
    )[0]
    assert "PRISTINE_POSTGRES_DATABASE_URL" not in backend_test_step
    assert "Verify backend migrations on pristine PostgreSQL" in workflow
    assert "Verify immutable backend runtime dependencies" in workflow
    assert "npm run i18n:check" in workflow
    assert "npm test" in workflow
    assert "python -m pytest tests/ -q" in workflow
    assert "merge-base --is-ancestor" in workflow


def test_school_production_git_fetch_failure_is_retryable(monkeypatch, tmp_path):
    controller = load_school_controller()

    def fail_fetch(*_arguments, **_keywords):
        raise subprocess.CalledProcessError(128, ["git", "fetch"])

    monkeypatch.setattr(controller, "git", fail_fetch)
    with __import__("pytest").raises(controller.ReleaseWaiting):
        controller.fetch_repository(tmp_path / "backend.git", "refs/heads/main")


def test_active_manifest_skips_frontend_fetch(monkeypatch, tmp_path):
    controller = load_school_controller()
    control_sha = "a" * 40
    (tmp_path / "last-success.json").write_text(
        json.dumps({"control_sha": control_sha}), encoding="utf-8"
    )
    monkeypatch.setattr(controller, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(controller.os, "geteuid", lambda: 0)
    monkeypatch.setattr(controller, "ensure_bare_repository", lambda *_args: None)
    monkeypatch.setattr(controller, "fetch_backend_repository", lambda *_args: None)
    monkeypatch.setattr(controller, "git", lambda *_args, **_kwargs: control_sha)

    def unexpected_frontend_fetch(*_arguments):
        raise AssertionError("an already-active manifest must not fetch the frontend")

    monkeypatch.setattr(controller, "fetch_frontend_repository", unexpected_frontend_fetch)
    controller.deploy_if_ready()


def test_school_release_update_helper_writes_exact_manifest(tmp_path):
    output = tmp_path / "release.json"
    backend_sha = "a" * 40
    frontend_sha = "b" * 40
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "update_school_production_release.py"),
            "--backend-sha",
            backend_sha,
            "--frontend-sha",
            frontend_sha,
            "--output",
            str(output),
        ],
        check=True,
    )
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "backend_sha": backend_sha,
        "database_change": {"approval_reference": None, "approved": False},
        "frontend_sha": frontend_sha,
        "schema_version": 1,
    }


def test_background_worker_overrides_shared_web_scheduler_setting():
    unit = (
        ROOT / "deploy" / "school" / "systemd" / "unikorn-background-worker.service"
    ).read_text(encoding="utf-8")

    assert "EnvironmentFile=/etc/unikorn/unikorn.env" in unit
    assert (
        "ExecStart=/usr/bin/env ENABLE_BACKGROUND_TASKS=true "
        "/srv/unikorn/current/backend/.venv/bin/python -m app.background_worker"
    ) in unit
    assert "Environment=ENABLE_BACKGROUND_TASKS=true" not in unit


def git_commit(repository: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(repository), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=UniKorn Test",
            "-c",
            "user.email=unikorn-test@example.invalid",
            "commit",
            "-m",
            message,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_school_controller_blocks_non_manifest_control_changes(tmp_path):
    controller = load_school_controller()
    repository = tmp_path / "backend"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-b", "main"], check=True)
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    base = git_commit(repository, "base")
    subprocess.run(
        ["git", "-C", str(repository), "update-ref", "refs/remotes/origin/main", base],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "switch", "-c", "school-production"],
        check=True,
        capture_output=True,
    )
    manifest = repository / "deploy" / "school" / "school-production-release.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}\n", encoding="utf-8")
    valid_control = git_commit(repository, "release")
    controller.verify_control_branch(repository, valid_control)

    (repository / "README.md").write_text("changed\n", encoding="utf-8")
    invalid_control = git_commit(repository, "change control code")
    with __import__("pytest").raises(
        controller.ReleaseBlocked,
        match="may change only the release manifest",
    ):
        controller.verify_control_branch(repository, invalid_control)


def test_school_controller_requires_database_approval_and_forward_motion(tmp_path):
    controller = load_school_controller()
    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    for repository in (backend, frontend):
        repository.mkdir()
        subprocess.run(["git", "-C", str(repository), "init", "-b", "main"], check=True)
        (repository / "README.md").write_text("base\n", encoding="utf-8")
    backend_old = git_commit(backend, "backend base")
    frontend_sha = git_commit(frontend, "frontend base")
    migration = backend / "migrations" / "versions" / "release.py"
    migration.parent.mkdir(parents=True)
    migration.write_text("revision = 'release'\n", encoding="utf-8")
    backend_new = git_commit(backend, "add migration")
    for repository, sha in ((backend, backend_new), (frontend, frontend_sha)):
        subprocess.run(
            ["git", "-C", str(repository), "update-ref", "refs/remotes/origin/main", sha],
            check=True,
        )

    manifest = {
        "schema_version": 1,
        "backend_sha": backend_new,
        "frontend_sha": frontend_sha,
        "database_change": {"approved": False, "approval_reference": None},
    }
    current = {"backend_sha": backend_old, "frontend_sha": frontend_sha}
    with __import__("pytest").raises(controller.ReleaseBlocked, match="without recorded approval"):
        controller.validate_transition(backend, frontend, manifest, current)

    manifest["database_change"] = {
        "approved": True,
        "approval_reference": "approved migration plan #123",
    }
    assert controller.validate_transition(backend, frontend, manifest, current) == [
        "migrations/versions/release.py"
    ]

    manifest["backend_sha"] = backend_old
    with __import__("pytest").raises(controller.ReleaseBlocked, match="backend transition"):
        controller.validate_transition(
            backend,
            frontend,
            manifest,
            {"backend_sha": backend_new, "frontend_sha": frontend_sha},
        )


def test_nginx_splits_hosts_strips_api_prefix_and_sanitizes_proxy_headers():
    shared = read("nginx/00-unikorn-shared.conf")
    unikorn = read("nginx/unikorn.conf")
    scheduler = read("nginx/course-scheduler.conf")
    assert "server_name unikorn.hkust-gz.edu.cn;" in unikorn
    assert "server_name scheduler.unikorn.hkust-gz.edu.cn;" in scheduler
    assert "127.0.0.1:3002" in scheduler
    assert re.search(r"location /api/\s*\{.*?proxy_pass http://127\.0\.0\.1:8001/;", unikorn, re.S)
    assert "listen 127.0.0.1:8081" in unikorn
    assert "10\\.121\\.10\\.250:https" in shared
    assert "X-Forwarded-Proto $unikorn_external_scheme" in unikorn
    assert "TRUSTED_PROXY_PROTO_HOPS=1" in read("unikorn.env.example")
    assert unikorn.count("location = /api/scheduler/internal/sisn-ingest") == 2
    assert re.search(
        r"location = /api/scheduler/internal/sisn-ingest\s*\{\s*return 404;",
        unikorn,
    )
    activation = read("activate-nginx.sh")
    assert "for _attempt in {1..40}" in activation
    assert "reloaded Nginx did not serve both host routes" in activation


def test_access_logs_exclude_queries_and_redirect_preserves_them():
    shared = read("nginx/00-unikorn-shared.conf")
    gunicorn = read("gunicorn.conf.py")
    redirect = read("nginx/old-site-redirect.conf")
    assert "$uri" in shared
    assert "$request_uri" not in shared
    assert "%(U)s" in gunicorn
    assert "%(q)s" not in gunicorn
    assert "https://unikorn.hkust-gz.edu.cn$request_uri" in redirect
    assert "return 308" in redirect


def test_environment_values_are_never_shell_sourced():
    scripts = "\n".join(path.read_text(encoding="utf-8") for path in SCHOOL.glob("*.sh"))
    assert "source /etc/unikorn/unikorn.env" not in scripts
    assert ". /etc/unikorn/unikorn.env" not in scripts
    assert "--property=EnvironmentFile=/etc/unikorn/unikorn.env" in read("deploy-release.sh")
    for unit in (SCHOOL / "systemd").glob("*.service"):
        if unit.name not in {
            "unikorn-redis.service",
            "unikorn-school-production-deploy.service",
        }:
            assert "EnvironmentFile=/etc/unikorn/unikorn.env" in unit.read_text(encoding="utf-8")


def test_sisn_production_ingest_is_loopback_only_signed_and_archived():
    environment = read("unikorn.env.example")
    assert "SISN_SYNC_TERM=2610" in environment
    assert "SISN_SYNC_ARCHIVE_DIR=/srv/unikorn/sisn-archive" in environment
    assert "SISN_PUSH_INGEST_ENABLED=true" in environment
    assert "SISN_PUSH_PUBLIC_KEY_PATH=/etc/unikorn/sisn-push-public.pem" in environment

    setup = read("enable-sisn-production-ingest.sh")
    assert "/etc/course-scheduler/credentials/sisn_push_private_key" in setup
    assert "openssl pkey" in setup
    assert "install -o root -g unikorn -m 0640" in setup
    assert "install -d -o unikorn -g unikorn -m 0750" in setup
    assert "sisn-archive.conf" in setup
    assert 'ReadWritePaths=${archive_dir}' in setup
    assert "systemctl daemon-reload" in setup
    assert "cp --preserve=mode,ownership" in setup
    assert "signed ingest did not fail closed" in setup
    assert "source /etc/unikorn/unikorn.env" not in setup

    verification = read("verify-local.sh")
    assert "http://127.0.0.1:8001/scheduler/internal/sisn-ingest" in verification
    assert "http://127.0.0.1/api/scheduler/internal/sisn-ingest" in verification
    assert "http://127.0.0.1:8081/api/scheduler/internal/sisn-ingest" in verification


def test_backup_is_custom_format_verified_hashed_and_retained():
    backup = read("create-database-backup.sh")
    timer = read("systemd/unikorn-backup.timer")
    assert "create_verified_database_backup" in backup
    assert "pg_restore --list" in backup
    assert "sha256sum" in backup
    assert "-mtime +13" in backup
    assert "Persistent=true" in timer
    assert "OnCalendar=" in timer


def test_restore_compares_every_table_alembic_and_foreign_keys_before_promotion():
    snapshot = read("database-snapshot.py")
    compare = read("compare-database-snapshots.py")
    post_migration = read("verify-post-migration-snapshot.py")
    restore = read("restore-production.sh")
    assert "pg_catalog.pg_tables" in snapshot
    assert "SELECT count(*)" in snapshot
    assert "alembic_version" in snapshot
    assert "unvalidated_foreign_keys" in snapshot
    compare_position = restore.index("compare-database-snapshots.py")
    promote_position = restore.index(
        "ALTER DATABASE prod_unikorn WITH ALLOW_CONNECTIONS false",
        compare_position,
    )
    assert compare_position < promote_position
    assert "--writes-frozen" in restore
    assert "--expected-backend-sha" in restore
    assert "--expected-frontend-sha" in restore
    assert "source commits do not match the explicitly approved SHAs" in restore
    assert "verify-environment-migration.py" in restore
    environment_check = read("verify-environment-migration.py")
    assert "dotenv_values" in environment_check
    assert "unexpected changed keys" in environment_check
    assert '"CAMPUS_SSO_CLIENT_SECRET"' in environment_check
    assert '"ALIBABA_CLOUD_ACCESS_KEY_SECRET"' not in environment_check.split(
        "ALLOWED_TO_CHANGE", 1
    )[1].split("})", 1)[0]
    assert "--exit-on-error --no-owner --no-acl" in restore
    exact_compare_position = restore.index("compare-database-snapshots.py")
    migration_position = restore.index("-m flask --app wsgi db upgrade")
    post_migration_position = restore.index("verify-post-migration-snapshot.py")
    assert exact_compare_position < migration_position < post_migration_position < promote_position
    assert "ast.parse" in post_migration
    assert "source table row count" in post_migration
    assert "target-only migration table count differs from declared seed" in post_migration
    assert "target Alembic heads do not match release" in post_migration
    rehearsal_gate = restore.index("ALTER DATABASE ${candidate} WITH ALLOW_CONNECTIONS false")
    assert post_migration_position < rehearsal_gate < promote_position


def test_post_migration_snapshot_allows_only_empty_new_tables(tmp_path):
    source = {
        "format": 1,
        "table_counts": {"public.alembic_version": 1, "public.users": 7},
        "alembic_heads": ["20260819_campus_oidc"],
        "extensions": ["plpgsql"],
        "foreign_keys": 10,
        "unvalidated_foreign_keys": 0,
    }
    target = {
        **source,
        "table_counts": {
            "public.alembic_version": 1,
            "public.users": 7,
            "public.sisn_sync_runs": 0,
            "public.scheduler_plans": 0,
            "public.scheduler_plan_courses": 0,
            "public.scheduler_plan_sections": 0,
            "public.meetcampus_worlds": 1,
            "public.meetcampus_scenes": 12,
            "public.meetcampus_scene_connections": 22,
            "public.meetcampus_residents": 20,
            "public.meetcampus_resident_states": 20,
            "public.meetcampus_activity_definitions": 21,
            "public.meetcampus_observations": 0,
            "public.meetcampus_decisions": 0,
            "public.meetcampus_journeys": 0,
            "public.meetcampus_activity_sessions": 0,
            "public.meetcampus_activity_participants": 0,
            "public.meetcampus_resident_plans": 0,
            "public.home_carousel_slides": 3,
            "public.agent_conversations": 0,
            "public.agent_messages": 0,
        },
        "alembic_heads": ["20260826_agent_chat"],
        "foreign_keys": 21,
    }
    source_path = tmp_path / "source.json"
    target_path = tmp_path / "target.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    target_path.write_text(json.dumps(target), encoding="utf-8")
    helper = SCHOOL / "verify-post-migration-snapshot.py"
    subprocess.run(
        [
            sys.executable,
            str(helper),
            str(source_path),
            str(target_path),
            "--migrations-dir",
            str(ROOT / "migrations"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    target["table_counts"]["public.sisn_sync_runs"] = 1
    target_path.write_text(json.dumps(target), encoding="utf-8")
    failed = subprocess.run(
        [
            sys.executable,
            str(helper),
            str(source_path),
            str(target_path),
            "--migrations-dir",
            str(ROOT / "migrations"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert "target-only migration table count differs from declared seed" in failed.stdout


def test_migration_dump_and_source_counts_share_one_exported_snapshot():
    exporter = read("create-migration-export.py")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "SERIALIZABLE READ ONLY DEFERRABLE" in exporter
    assert "SELECT pg_export_snapshot()" in exporter
    assert 'f"--snapshot={snapshot_id}"' in exporter
    assert "SELECT count(*)" in exporter
    assert "create-migration-export.py" in workflow


def test_export_artifact_is_encrypted_and_actions_are_sha_pinned():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "openssl cms -encrypt" in workflow
    assert "production.env" in workflow
    assert "Upload encrypted package only" in workflow
    assert "path: ${{ steps.encrypted-export.outputs.artifact_path }}" in workflow
    assert "database.dump\n" not in workflow.split("Upload encrypted package only", 1)[1]
    uses = re.findall(r"uses:\s*([^\s]+)", workflow)
    assert uses
    for action in uses:
        ref = action.rsplit("@", 1)[1]
        assert re.fullmatch(r"[0-9a-f]{40}", ref), action


def test_courseplan_auth_lockdown_is_gated_and_covers_entire_namespace():
    lockdown = read("disable-courseplan-legacy-auth.sh")
    policy = read("nginx/scheduler-auth-policy.locked.conf")
    assert "SSO_ACCEPTED_ACCOUNTS_BOUND_SESSIONS_REVOKED" in lockdown
    assert 'get("enabled") is True' in lockdown
    assert "location ^~ /api/auth/" in policy
    assert "return 404" in policy


def test_bootstrap_preserves_courseplan_and_uses_postgresql_peer_auth():
    bootstrap = read("bootstrap-host.sh")
    assert "systemctl is-active --quiet courseplan.service" in bootstrap
    assert "local   prod_unikorn   unikorn" in bootstrap
    assert "peer" in bootstrap
    assert "postgresql:///prod_unikorn" in read("unikorn.env.example")
    assert "PASSWORD" not in read("unikorn.env.example").split("DATABASE_URL", 1)[0]
    assert "/run/unikorn-unit-verify." in bootstrap
    assert "systemd-analyze verify" in bootstrap
    assert "ExecStart=/usr/bin/true" in bootstrap
    assert 'redis_config_root="/etc/unikorn-redis"' in bootstrap
    assert "ca-certificates curl git logrotate" in bootstrap
    assert "unlink -- /etc/logrotate.d/unikorn-nginx" in bootstrap
    assert not (SCHOOL / "logrotate-unikorn-nginx.conf").exists()


def test_environment_migration_compares_values_without_printing_them(tmp_path):
    source = tmp_path / "production.env"
    source.write_text(
        'OSS_BUCKET_NAME="bucket with spaces # and $ signs"\n'
        'FRONTEND_BASE_URL=https://unikorn.axfff.com\n',
        encoding="utf-8",
    )
    helper = SCHOOL / "verify-environment-migration.py"
    environment = {
        "PATH": os.environ["PATH"],
        "OSS_BUCKET_NAME": "bucket with spaces # and $ signs",
        "FRONTEND_BASE_URL": "https://unikorn.hkust-gz.edu.cn",
    }
    subprocess.run(
        [sys.executable, str(helper), str(source)],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    environment["OSS_BUCKET_NAME"] = "wrong-secret-value"
    failed = subprocess.run(
        [sys.executable, str(helper), str(source)],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert "OSS_BUCKET_NAME" in failed.stderr
    assert "wrong-secret-value" not in failed.stderr
    assert "bucket with spaces" not in failed.stderr
