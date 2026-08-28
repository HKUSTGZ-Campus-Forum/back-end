#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

require_oidc=false
restart_services=false
[[ "${EUID}" -eq 0 ]] || { printf '%s\n' 'verify-local.sh requires root' >&2; exit 1; }
while (($#)); do
    case "$1" in
        --require-oidc) require_oidc=true ;;
        --restart-services) restart_services=true ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

json_assert() {
    local expression="$1"
    python3 -c "import json,sys; data=json.load(sys.stdin); assert ${expression}"
}

curl_json() {
    curl --fail-with-body --silent --show-error --connect-timeout 3 --max-time 15 "$@"
}

systemctl is-active --quiet courseplan.service
systemctl is-active --quiet unikorn-redis.service
systemctl is-active --quiet unikorn-backend.service
systemctl is-active --quiet unikorn-frontend.service

curl_json http://127.0.0.1:8001/healthz | json_assert 'data.get("status") == "ok"'
curl_json http://127.0.0.1:8001/readyz | json_assert \
    'data.get("status") == "ready" and all(v.get("status") == "ok" for v in data["checks"].values())'
curl_json http://127.0.0.1:3000/health | json_assert 'data.get("status") == "ok"'
curl_json -H 'Host: unikorn.hkust-gz.edu.cn' http://127.0.0.1/api/healthz | \
    json_assert 'data.get("status") == "ok"'

# The signed SISN ingest is available only on the loopback Flask listener.
# Public and SSR Nginx boundaries must conceal it, while an unsigned direct
# request must fail closed at the backend authentication layer.
for endpoint in \
    'http://127.0.0.1/api/scheduler/internal/sisn-ingest' \
    'http://127.0.0.1:8081/api/scheduler/internal/sisn-ingest'; do
    status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
        --connect-timeout 3 --max-time 15 --request POST \
        --header 'Host: unikorn.hkust-gz.edu.cn' \
        --header 'Content-Type: application/json' --data '{}' "${endpoint}")"
    [[ "${status}" == 404 ]] || {
        printf 'SISN ingest is exposed at %s (HTTP %s)\n' "${endpoint}" "${status}" >&2
        exit 1
    }
done
status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --connect-timeout 3 --max-time 15 --request POST \
    --header 'Content-Type: application/json' --data '{}' \
    http://127.0.0.1:8001/scheduler/internal/sisn-ingest)"
[[ "${status}" == 401 ]] || {
    printf 'loopback SISN ingest did not fail closed (HTTP %s)\n' "${status}" >&2
    exit 1
}
oidc_payload="$(curl_json -H 'Host: unikorn.hkust-gz.edu.cn' \
    http://127.0.0.1/api/auth/oidc/status)"
printf '%s' "${oidc_payload}" | json_assert \
    'data.get("flow") == "authorization_code_pkce" and isinstance(data.get("enabled"), bool)'
if [[ "${require_oidc}" == "true" ]]; then
    printf '%s' "${oidc_payload}" | json_assert 'data.get("enabled") is True'
fi

curl --fail --silent --show-error --connect-timeout 3 --max-time 15 \
    -H 'Host: scheduler.unikorn.hkust-gz.edu.cn' http://127.0.0.1/ >/dev/null

for port in 3000 3002 6380 8001 8081; do
    listen_addresses="$(ss -ltnH "sport = :${port}" | awk '{print $4}')"
    [[ -n "${listen_addresses}" ]] || { printf 'port %s is not listening\n' "${port}" >&2; exit 1; }
    if printf '%s\n' "${listen_addresses}" | grep -Evq '^127\.0\.0\.1:|^\[::1\]:'; then
        printf 'port %s is exposed beyond loopback\n' "${port}" >&2
        exit 1
    fi
done

systemd-run --quiet --wait --collect --pipe \
    --unit="unikorn-verify-db-$$" \
    --uid=unikorn \
    --property=NoNewPrivileges=true \
    --property=PrivateTmp=true \
    /usr/bin/psql --dbname=prod_unikorn --no-psqlrc \
    --tuples-only --no-align --command='SELECT current_database()' | \
    grep -qx 'prod_unikorn'
redis-cli -h 127.0.0.1 -p 6380 ping | grep -qx PONG
systemctl start unikorn-backup.service
latest_backup="$(find /srv/unikorn/backups/database -xdev -maxdepth 1 -type f \
    -name 'prod_unikorn-????????T??????Z.dump' -print | sort | tail -n 1)"
[[ -n "${latest_backup}" && -s "${latest_backup}.list" ]] || {
    printf '%s\n' 'verified database backup was not created' >&2
    exit 1
}
(
    cd /srv/unikorn/backups/database
    sha256sum --check "$(basename -- "${latest_backup}").sha256"
    sha256sum --check "$(basename -- "${latest_backup}").list.sha256"
)
pg_restore --list "${latest_backup}" >/dev/null

if [[ "${restart_services}" == "true" ]]; then
    [[ "${EUID}" -eq 0 ]] || { printf '%s\n' '--restart-services requires root' >&2; exit 1; }
    systemctl restart unikorn-redis.service unikorn-backend.service unikorn-frontend.service unikorn-background-worker.service
    systemctl restart courseplan.service
    sleep 3
    rerun_args=()
    [[ "${require_oidc}" == "true" ]] && rerun_args+=(--require-oidc)
    exec "$0" "${rerun_args[@]}"
fi

printf '%s\n' 'local UniKorn, PostgreSQL, Redis, Nginx routing, and CoursePlan checks passed'
