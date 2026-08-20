#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

readonly confirmation="${1:-}"
readonly required="SSO_ACCEPTED_ACCOUNTS_BOUND_SESSIONS_REVOKED"
readonly script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly policy="/etc/unikorn/scheduler-auth-policy.conf"

[[ "${EUID}" -eq 0 ]] || { printf '%s\n' 'run as root through interactive sudo' >&2; exit 1; }
[[ "${confirmation}" == "${required}" ]] || {
    printf 'refusing legacy-auth lockdown; required confirmation: %s\n' "${required}" >&2
    exit 2
}
curl --fail --silent --show-error --max-time 10 \
    -H 'Host: unikorn.hkust-gz.edu.cn' \
    http://127.0.0.1/api/auth/oidc/status | \
    python3 -c 'import json,sys; assert json.load(sys.stdin).get("enabled") is True'

backup="${policy}.before-lockdown.$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -f "${policy}" && ! -L "${policy}" ]]; then
    install -o root -g root -m 0600 "${policy}" "${backup}"
fi
install -o root -g root -m 0644 \
    "${script_dir}/nginx/scheduler-auth-policy.locked.conf" "${policy}"
if ! nginx -t; then
    [[ -f "${backup}" ]] && install -o root -g root -m 0644 "${backup}" "${policy}"
    exit 1
fi
systemctl reload nginx
for endpoint in \
    /api/auth/sign-up/email \
    /api/auth/sign-in/username \
    /api/auth/request-password-reset \
    /api/auth/reset-password \
    /api/auth/get-session; do
    status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
        --max-time 10 -H 'Host: scheduler.unikorn.hkust-gz.edu.cn' \
        "http://127.0.0.1${endpoint}")"
    [[ "${status}" == "404" ]] || { printf 'lockdown failed for %s: %s\n' "${endpoint}" "${status}" >&2; exit 1; }
done
printf 'CoursePlan Better Auth namespace disabled; rollback copy: %s\n' "${backup}"
