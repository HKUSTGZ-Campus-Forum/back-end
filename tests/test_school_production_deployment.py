from pathlib import Path
import os
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCHOOL = ROOT / "deploy" / "school"
WORKFLOW = ROOT / ".github" / "workflows" / "export-production-migration.yml"


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


def test_release_build_is_noninteractive_and_survives_atomic_stage_rename():
    deploy = read("deploy-release.sh")
    assert "export CI=1" in deploy
    assert "export NUXT_TELEMETRY_DISABLED=1" in deploy
    assert 'mv -T -- "${stage_path}" "${release_path}"' in deploy
    assert deploy.count("GIT_OPTIONAL_LOCKS=0 git -C") == 2


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
        if unit.name != "unikorn-redis.service":
            assert "EnvironmentFile=/etc/unikorn/unikorn.env" in unit.read_text(encoding="utf-8")


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
