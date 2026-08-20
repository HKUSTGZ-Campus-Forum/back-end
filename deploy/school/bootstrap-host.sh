#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

readonly script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly app_root="/srv/unikorn"
readonly config_root="/etc/unikorn"
readonly redis_config_root="/etc/unikorn-redis"

fail() {
    printf 'bootstrap failed: %s\n' "$*" >&2
    exit 1
}

[[ "${EUID}" -eq 0 ]] || fail "run as root through interactive sudo"
[[ "${script_dir}" == /* && -d "${script_dir}/systemd" ]] || fail "invalid script directory"
systemctl is-active --quiet courseplan.service || fail "CoursePlan must be active before bootstrap"
[[ "$(readlink -f -- /srv/course-scheduler)" == "/srv/course-scheduler" ]] || \
    fail "CoursePlan path is unexpected"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install --yes --no-install-recommends \
    ca-certificates curl git logrotate nginx npm openssl postgresql postgresql-client \
    python3-dev python3-venv redis-server rsync build-essential libpq-dev

if ! id unikorn >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "${app_root}" \
        --no-create-home --shell /usr/sbin/nologin unikorn
fi
readonly passwd_entry="$(getent passwd unikorn)"
[[ "${passwd_entry}" == *":${app_root}:/usr/sbin/nologin" ]] || \
    fail "existing unikorn user has an unexpected home or shell"

install -d -o root -g unikorn -m 0750 "${config_root}"
install -d -o root -g redis -m 0750 "${redis_config_root}"
install -d -o root -g root -m 0755 "${app_root}" "${app_root}/releases"
install -d -o unikorn -g unikorn -m 0750 \
    "${app_root}/backups" "${app_root}/backups/database"
install -d -o redis -g redis -m 0750 /var/lib/unikorn-redis
install -d -o root -g root -m 0700 "${config_root}/nginx-backups"

install -o root -g unikorn -m 0640 \
    "${script_dir}/unikorn.env.example" "${config_root}/unikorn.env.example"
install -o root -g redis -m 0640 \
    "${script_dir}/redis.conf" "${redis_config_root}/redis.conf"
if [[ -f "${config_root}/redis.conf" && ! -L "${config_root}/redis.conf" ]]; then
    unlink -- "${config_root}/redis.conf"
fi
if [[ ! -e "${config_root}/scheduler-auth-policy.conf" ]]; then
    install -o root -g root -m 0644 \
        "${script_dir}/nginx/scheduler-auth-policy.open.conf" \
        "${config_root}/scheduler-auth-policy.conf"
fi
install -o root -g root -m 0644 \
    "${script_dir}/logrotate-unikorn-nginx.conf" /etc/logrotate.d/unikorn-nginx
for unit in "${script_dir}"/systemd/*; do
    [[ -f "${unit}" && ! -L "${unit}" ]] || fail "unsafe systemd unit source"
    install -o root -g root -m 0644 "${unit}" "/etc/systemd/system/$(basename -- "${unit}")"
done

# The migration private key is born on and remains on the school server. Only
# its public certificate may be copied to GitHub as a repository/environment variable.
readonly private_key="${config_root}/migration-private.key"
readonly public_cert="${config_root}/migration-public.crt"
if [[ ! -e "${private_key}" && ! -e "${public_cert}" ]]; then
    key_stage="$(mktemp -d "${config_root}/.migration-key.XXXXXX")"
    trap 'if [[ -n "${key_stage:-}" && -d "${key_stage}" ]]; then find "${key_stage}" -type f -exec shred -u -- {} +; rmdir -- "${key_stage}"; fi' EXIT
    openssl req -x509 -newkey rsa:4096 -sha256 -nodes -days 825 \
        -subj '/CN=UniKorn production migration/' \
        -keyout "${key_stage}/private.key" -out "${key_stage}/public.crt"
    install -o root -g root -m 0600 "${key_stage}/private.key" "${private_key}"
    install -o root -g root -m 0644 "${key_stage}/public.crt" "${public_cert}"
    find "${key_stage}" -type f -exec shred -u -- {} +
    rmdir -- "${key_stage}"
    key_stage=""
    trap - EXIT
elif [[ ! -f "${private_key}" || ! -f "${public_cert}" || -L "${private_key}" || -L "${public_cert}" ]]; then
    fail "migration keypair is incomplete or unsafe"
fi
chmod 0600 "${private_key}"
chmod 0644 "${public_cert}"
openssl x509 -in "${public_cert}" -noout -checkend 1209600 >/dev/null || \
    fail "migration certificate expires within 14 days; rotate it before exporting data"
private_fingerprint="$(openssl pkey -in "${private_key}" -pubout -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
cert_fingerprint="$(openssl x509 -in "${public_cert}" -pubkey -noout | \
    openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
[[ "${private_fingerprint}" == "${cert_fingerprint}" ]] || fail "migration keypair mismatch"

systemctl enable --now postgresql.service
mapfile -t clusters < <(pg_lsclusters --no-header | awk '{print $1 "/" $2}')
[[ "${#clusters[@]}" -eq 1 ]] || fail "expected exactly one PostgreSQL cluster"
readonly cluster_version="${clusters[0]%/*}"
readonly cluster_name="${clusters[0]#*/}"
readonly pg_conf_dir="/etc/postgresql/${cluster_version}/${cluster_name}"
[[ -d "${pg_conf_dir}/conf.d" && ! -L "${pg_conf_dir}" ]] || fail "unexpected PostgreSQL config path"
pg_stage="$(mktemp "${pg_conf_dir}/conf.d/.unikorn.XXXXXX")"
printf "%s\n" "listen_addresses = 'localhost'" >"${pg_stage}"
install -o postgres -g postgres -m 0644 "${pg_stage}" "${pg_conf_dir}/conf.d/99-unikorn.conf"
unlink -- "${pg_stage}"

readonly hba_file="$(runuser -u postgres -- psql --dbname=postgres --no-psqlrc \
    --tuples-only --no-align --command='SHOW hba_file')"
[[ "${hba_file}" == "${pg_conf_dir}/pg_hba.conf" && -f "${hba_file}" && ! -L "${hba_file}" ]] || \
    fail "unexpected pg_hba.conf path"
if ! awk '$1 == "local" && $2 == "prod_unikorn" && $3 == "unikorn" && $4 == "peer" { found=1 } END { exit !found }' "${hba_file}"; then
    hba_stage="$(mktemp "${pg_conf_dir}/.pg_hba.XXXXXX")"
    {
        printf '%s\n' 'local   prod_unikorn   unikorn                                peer'
        sed -n '1,$p' "${hba_file}"
    } >"${hba_stage}"
    install -o postgres -g postgres -m 0640 "${hba_stage}" "${hba_file}"
    unlink -- "${hba_stage}"
fi
systemctl restart postgresql.service

runuser -u postgres -- psql --dbname=postgres --no-psqlrc --set ON_ERROR_STOP=1 <<'SQL'
SELECT 'CREATE ROLE unikorn LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'
 WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'unikorn') \gexec
SELECT 'CREATE DATABASE prod_unikorn OWNER unikorn'
 WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_database WHERE datname = 'prod_unikorn') \gexec
SQL
role_flags="$(runuser -u postgres -- psql --dbname=postgres --no-psqlrc \
    --tuples-only --no-align --command="SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname='unikorn'")"
[[ "${role_flags}" == "f|f|f|f" ]] || fail "database role unikorn has excessive privileges"
db_owner="$(runuser -u postgres -- psql --dbname=postgres --no-psqlrc \
    --tuples-only --no-align --command="SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='prod_unikorn'")"
[[ "${db_owner}" == "unikorn" ]] || fail "prod_unikorn has the wrong owner"
runuser -u unikorn -- psql --dbname=prod_unikorn --no-psqlrc \
    --tuples-only --no-align --command='SELECT current_user' | grep -qx unikorn || \
    fail "PostgreSQL peer authentication failed"

# Keep the distribution Redis unit disabled; UniKorn uses its own loopback-only
# instance on port 6380 and does not touch CoursePlan.
systemctl disable --now redis-server.service >/dev/null 2>&1 || true
systemctl daemon-reload

# The application executables and EnvironmentFile intentionally do not exist
# until a release is built. Validate every directive now using temporary copies
# with only those not-yet-existing paths replaced; deploy-release.sh later
# starts the real units and therefore validates the exact executable paths.
unit_verify_dir="$(mktemp -d /run/unikorn-unit-verify.XXXXXX)"
cleanup_unit_verify() {
    if [[ -n "${unit_verify_dir:-}" && -d "${unit_verify_dir}" && \
          "${unit_verify_dir}" == /run/unikorn-unit-verify.* ]]; then
        find "${unit_verify_dir}" -depth -delete
    fi
}
trap cleanup_unit_verify EXIT
for installed_unit in /etc/systemd/system/unikorn-*; do
    [[ -f "${installed_unit}" && ! -L "${installed_unit}" ]] || \
        fail "unsafe installed systemd unit"
    verify_unit="${unit_verify_dir}/$(basename -- "${installed_unit}")"
    cp -- "${installed_unit}" "${verify_unit}"
    sed -i \
        -e '/^ConditionPathIsSymbolicLink=/d' \
        -e '/^EnvironmentFile=/d' \
        -e 's#^WorkingDirectory=.*#WorkingDirectory=/tmp#' \
        -e 's#^ExecStartPre=.*#ExecStartPre=/usr/bin/true#' \
        -e 's#^ExecStart=.*#ExecStart=/usr/bin/true#' \
        -e 's#^ExecStop=.*#ExecStop=/usr/bin/true#' \
        -e 's#^ReadWritePaths=.*#ReadWritePaths=/tmp#' \
        "${verify_unit}"
done
systemd-analyze verify "${unit_verify_dir}"/*
cleanup_unit_verify
unit_verify_dir=""
trap - EXIT
systemctl enable unikorn-redis.service >/dev/null
systemctl restart unikorn-redis.service
redis-cli -h 127.0.0.1 -p 6380 ping | grep -qx PONG || fail "isolated Redis did not start"
if ss -ltnH 'sport = :6380' | awk '{print $4}' | grep -Evq '^127\.0\.0\.1:|^\[::1\]:'; then
    fail "Redis is listening beyond loopback"
fi
systemctl is-active --quiet courseplan.service || fail "CoursePlan changed during bootstrap"

printf '%s\n' "bootstrap complete; active environment is still intentionally absent"
printf '%s\n' "migration public certificate: ${public_cert}"
openssl x509 -in "${public_cert}" -noout -fingerprint -sha256 -enddate
