#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly app_root="/srv/unikorn"
readonly private_key="/etc/unikorn/migration-private.key"
readonly public_cert="/etc/unikorn/migration-public.crt"

package=""
mode=""
writes_frozen=false
promote=false
expected_backend_sha=""
expected_frontend_sha=""

fail() { printf 'production restore failed: %s\n' "$*" >&2; exit 1; }
usage() {
    printf '%s\n' "usage: $0 --package /absolute/package.cms --mode rehearsal|final --expected-backend-sha SHA --expected-frontend-sha SHA [--writes-frozen] [--promote]" >&2
    exit 2
}
while (($#)); do
    case "$1" in
        --package) package="${2:-}"; shift 2 ;;
        --mode) mode="${2:-}"; shift 2 ;;
        --expected-backend-sha) expected_backend_sha="${2:-}"; shift 2 ;;
        --expected-frontend-sha) expected_frontend_sha="${2:-}"; shift 2 ;;
        --writes-frozen) writes_frozen=true; shift ;;
        --promote) promote=true; shift ;;
        *) usage ;;
    esac
done

[[ "${EUID}" -eq 0 ]] || fail "run as root through interactive sudo"
[[ "${mode}" == "rehearsal" || "${mode}" == "final" ]] || usage
[[ "${expected_backend_sha}" =~ ^[0-9a-f]{40}$ && \
   "${expected_frontend_sha}" =~ ^[0-9a-f]{40}$ ]] || \
    fail "full expected source commit SHAs are required"
if [[ "${mode}" == "final" ]]; then
    [[ "${writes_frozen}" == "true" ]] || fail "final restore requires --writes-frozen"
    promote=true
fi
[[ "${package}" == /* && -f "${package}" && ! -L "${package}" ]] || \
    fail "encrypted package must be an absolute, non-symlink file"
package="$(realpath -e -- "${package}")"
[[ -f "${package}" && ! -L "${package}" ]] || fail "unsafe encrypted package"
[[ -f "${private_key}" && ! -L "${private_key}" ]] || fail "school private key is unavailable"
[[ "$(stat -c '%U:%G:%a' -- "${private_key}")" == "root:root:600" ]] || fail "unsafe private-key metadata"
[[ -L "${app_root}/current" ]] || fail "a built application release is required"
readonly current_release="$(readlink -f -- "${app_root}/current")"
[[ "${current_release}" == "${app_root}/releases/"* && -f "${current_release}/release.json" ]] || \
    fail "unsafe current release"
systemctl is-active --quiet courseplan.service || fail "CoursePlan is not active"

restore_root="$(mktemp -d "${app_root}/backups/.restore.XXXXXX")"
chown root:unikorn "${restore_root}"
chmod 0750 "${restore_root}"
cleanup() {
    status=$?
    if [[ -n "${restore_root:-}" && -d "${restore_root}" && "${restore_root}" == "${app_root}/backups/.restore."* ]]; then
        find "${restore_root}" -type f -exec shred -u -n 1 -- {} + 2>/dev/null || true
        find "${restore_root}" -depth -type d -empty -delete 2>/dev/null || true
    fi
    exit "${status}"
}
trap cleanup EXIT

openssl cms -decrypt -binary -inform DER \
    -in "${package}" -inkey "${private_key}" -recip "${public_cert}" \
    -out "${restore_root}/payload.tar"

python3 - "${restore_root}/payload.tar" <<'PY'
import sys
import tarfile

expected = {
    "manifest.json",
    "database.dump",
    "database.dump.list",
    "source-database.json",
    "source-commits.json",
    "production.env",
}
with tarfile.open(sys.argv[1], "r:") as archive:
    members = archive.getmembers()
    names = {member.name for member in members}
    if names != expected:
        raise SystemExit(f"unexpected migration package members: {sorted(names ^ expected)}")
    if any(not member.isfile() or member.name.startswith(('/', '../')) or '/..' in member.name for member in members):
        raise SystemExit("migration package contains an unsafe member")
    archive.extractall(sys.argv[1] + ".contents", filter="data")
PY
readonly contents="${restore_root}/payload.tar.contents"
chown root:unikorn "${contents}" "${contents}/database.dump"
chmod 0750 "${contents}"
chmod 0640 "${contents}/database.dump"

python3 - "${contents}" "${mode}" "${writes_frozen}" \
    "${expected_backend_sha}" "${expected_frontend_sha}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
mode = sys.argv[2]
writes_frozen = sys.argv[3] == "true"
expected_backend = sys.argv[4]
expected_frontend = sys.argv[5]
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
if manifest.get("format") != 1 or manifest.get("source_database") != "prod_unikorn":
    raise SystemExit("invalid migration manifest")
if manifest.get("export_kind") != mode:
    raise SystemExit("package export kind does not match requested restore mode")
if mode == "final" and (not writes_frozen or manifest.get("writes_frozen") is not True):
    raise SystemExit("final package does not attest that old-site writes were frozen")
for name, key in {
    "database.dump": "database_sha256",
    "database.dump.list": "database_list_sha256",
    "source-database.json": "database_snapshot_sha256",
    "source-commits.json": "source_commits_sha256",
    "production.env": "production_env_sha256",
}.items():
    digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
    if digest != manifest.get(key):
        raise SystemExit(f"digest mismatch for {name}")
commits = json.loads((root / "source-commits.json").read_text(encoding="utf-8"))
if commits != {"backend": expected_backend, "frontend": expected_frontend}:
    raise SystemExit("source commits do not match the explicitly approved SHAs")
print(json.dumps({
    "status": "package-verified",
    "export_kind": mode,
    "database_sha256": manifest["database_sha256"],
}, sort_keys=True))
PY

pg_restore --list "${contents}/database.dump" >/dev/null
[[ -s "${contents}/database.dump.list" ]] || fail "packaged pg_restore list is empty"

# Let systemd parse the active file exactly as the services do. The helper
# compares values without printing them and permits only reviewed topology and
# rotated-SSO differences from the decrypted former-production dotenv file.
systemd-run --quiet --wait --collect --pipe \
    --unit="unikorn-environment-migration-check-$$" \
    --property=EnvironmentFile=/etc/unikorn/unikorn.env \
    "${current_release}/backend/.venv/bin/python" \
    "${current_release}/backend/deploy/school/verify-environment-migration.py" \
    "${contents}/production.env"

readonly timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
readonly candidate="unikorn_restore_${timestamp,,}"
[[ "${candidate}" =~ ^[a-z0-9_]+$ && "${#candidate}" -le 63 ]] || fail "invalid candidate database name"
if runuser -u postgres -- psql --dbname=postgres --no-psqlrc --tuples-only --no-align \
    --command="SELECT 1 FROM pg_database WHERE datname='${candidate}'" | grep -qx 1; then
    fail "candidate database already exists"
fi
runuser -u postgres -- createdb --owner=unikorn --template=template0 --encoding=UTF8 "${candidate}"
candidate_created=true
old_renamed=false
candidate_promoted=false
rollback_database=""
recover_restore_error() {
    status=$?
    trap - ERR
    set +e
    if [[ "${candidate_promoted:-false}" == "true" && -n "${rollback_database:-}" ]]; then
        recovery_failed="prod_unikorn_failed_${timestamp,,}"
        runuser -u postgres -- psql --dbname=postgres --no-psqlrc \
            --command="ALTER DATABASE prod_unikorn WITH ALLOW_CONNECTIONS false" >/dev/null 2>&1
        runuser -u postgres -- psql --dbname=postgres --no-psqlrc \
            --command="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='prod_unikorn'" >/dev/null 2>&1
        runuser -u postgres -- psql --dbname=postgres --no-psqlrc \
            --command="ALTER DATABASE prod_unikorn RENAME TO ${recovery_failed}" >/dev/null 2>&1
        runuser -u postgres -- psql --dbname=postgres --no-psqlrc \
            --command="ALTER DATABASE ${rollback_database} RENAME TO prod_unikorn" >/dev/null 2>&1
        runuser -u postgres -- psql --dbname=postgres --no-psqlrc \
            --command="ALTER DATABASE prod_unikorn WITH ALLOW_CONNECTIONS true" >/dev/null 2>&1
    elif [[ "${old_renamed:-false}" == "true" && -n "${rollback_database:-}" ]]; then
        runuser -u postgres -- psql --dbname=postgres --no-psqlrc \
            --command="ALTER DATABASE ${rollback_database} RENAME TO prod_unikorn" >/dev/null 2>&1
        runuser -u postgres -- psql --dbname=postgres --no-psqlrc \
            --command="ALTER DATABASE prod_unikorn WITH ALLOW_CONNECTIONS true" >/dev/null 2>&1
    fi
    if [[ "${candidate_created:-false}" == "true" ]]; then
        runuser -u postgres -- dropdb --if-exists --force "${candidate}" >/dev/null 2>&1 || true
    fi
    exit "${status}"
}
trap recover_restore_error ERR

runuser -u unikorn -- pg_restore --exit-on-error --no-owner --no-acl \
    --dbname="${candidate}" "${contents}/database.dump"

install -d -o unikorn -g unikorn -m 0750 "${restore_root}/target-output"
runuser -u unikorn -- env \
    APP_ENV=development \
    DATABASE_URL="postgresql:///${candidate}" \
    AUTO_INIT_ON_STARTUP=false \
    ENABLE_BACKGROUND_TASKS=false \
    "${current_release}/backend/.venv/bin/python" \
    "${current_release}/backend/deploy/school/database-snapshot.py" \
    --expected-database "${candidate}" \
    --output "${restore_root}/target-output/target-database.json"
"${current_release}/backend/.venv/bin/python" \
    "${current_release}/backend/deploy/school/compare-database-snapshots.py" \
    "${contents}/source-database.json" "${restore_root}/target-output/target-database.json"

# The source snapshot must first match byte-for-byte restored schema/data. Only
# after that gate may the candidate advance to the currently deployed release's
# Alembic heads. This keeps source accounting exact while ensuring a newly
# promoted database is compatible with the application that will open it.
(
    cd -- "${current_release}/backend"
    runuser -u unikorn -- env \
        APP_ENV=development \
        DATABASE_URL="postgresql:///${candidate}" \
        AUTO_INIT_ON_STARTUP=false \
        ENABLE_BACKGROUND_TASKS=false \
        "${current_release}/backend/.venv/bin/python" -m flask --app wsgi db upgrade
)
runuser -u unikorn -- env \
    APP_ENV=development \
    DATABASE_URL="postgresql:///${candidate}" \
    AUTO_INIT_ON_STARTUP=false \
    ENABLE_BACKGROUND_TASKS=false \
    "${current_release}/backend/.venv/bin/python" \
    "${current_release}/backend/deploy/school/database-snapshot.py" \
    --expected-database "${candidate}" \
    --output "${restore_root}/target-output/target-post-migration-database.json"
"${current_release}/backend/.venv/bin/python" \
    "${current_release}/backend/deploy/school/verify-post-migration-snapshot.py" \
    "${contents}/source-database.json" \
    "${restore_root}/target-output/target-post-migration-database.json" \
    --migrations-dir "${current_release}/backend/migrations"

if [[ "${promote}" != "true" ]]; then
    runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 \
        --command="ALTER DATABASE ${candidate} WITH ALLOW_CONNECTIONS false" >/dev/null
    trap - ERR
    candidate_created=false
    printf 'rehearsal restored, migrated, verified, and left offline: database=%s\n' \
        "${candidate}"
    exit 0
fi

if [[ -L "${app_root}/current" ]]; then
    systemctl start unikorn-backup.service
fi
systemctl stop unikorn-background-worker.service unikorn-frontend.service unikorn-backend.service >/dev/null 2>&1 || true
rollback_database="prod_unikorn_rollback_${timestamp,,}"
[[ "${#rollback_database}" -le 63 ]] || fail "rollback database name is too long"

runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 \
    --command="ALTER DATABASE prod_unikorn WITH ALLOW_CONNECTIONS false"
runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 \
    --command="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='prod_unikorn' AND pid <> pg_backend_pid()" >/dev/null
runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 \
    --command="ALTER DATABASE prod_unikorn RENAME TO ${rollback_database}"
old_renamed=true
runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 \
    --command="ALTER DATABASE ${candidate} RENAME TO prod_unikorn"
candidate_promoted=true
candidate_created=false
runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 \
    --command="ALTER DATABASE prod_unikorn WITH ALLOW_CONNECTIONS true"
runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 \
    --command="ALTER DATABASE ${rollback_database} WITH ALLOW_CONNECTIONS false"
trap - ERR

systemctl restart unikorn-backend.service unikorn-frontend.service unikorn-background-worker.service
healthy=false
for _attempt in {1..30}; do
    if curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
        http://127.0.0.1:8001/readyz >/dev/null && \
       curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
        http://127.0.0.1:3000/health >/dev/null; then
        healthy=true
        break
    fi
    sleep 2
done
if [[ "${healthy}" != "true" ]]; then
    systemctl stop unikorn-background-worker.service unikorn-frontend.service unikorn-backend.service >/dev/null 2>&1 || true
    failed_database="prod_unikorn_failed_${timestamp,,}"
    runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 \
        --command="ALTER DATABASE prod_unikorn WITH ALLOW_CONNECTIONS false"
    runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 \
        --command="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='prod_unikorn'" >/dev/null
    runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 \
        --command="ALTER DATABASE prod_unikorn RENAME TO ${failed_database}"
    runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 \
        --command="ALTER DATABASE ${rollback_database} RENAME TO prod_unikorn"
    runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 \
        --command="ALTER DATABASE prod_unikorn WITH ALLOW_CONNECTIONS true"
    systemctl start unikorn-backend.service unikorn-frontend.service unikorn-background-worker.service >/dev/null 2>&1 || true
    fail "promoted database failed health checks and was rolled back"
fi

systemctl is-active --quiet courseplan.service || fail "CoursePlan changed during restore"
printf 'verified %s database promoted; rollback database is offline: %s\n' \
    "${mode}" "${rollback_database}"
