#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly app_root="/srv/unikorn"
readonly releases_root="${app_root}/releases"
[[ "${EUID}" -eq 0 ]] || { printf '%s\n' 'run as root through interactive sudo' >&2; exit 1; }
[[ -L "${app_root}/current" && -L "${app_root}/previous" ]] || {
    printf '%s\n' 'both current and previous release links are required' >&2
    exit 1
}
current="$(readlink -f -- "${app_root}/current")"
previous="$(readlink -f -- "${app_root}/previous")"
for target in "${current}" "${previous}"; do
    [[ "${target}" == "${releases_root}/"* && -f "${target}/release.json" ]] || {
        printf 'unsafe release target: %s\n' "${target}" >&2
        exit 1
    }
done

# A verified DB backup is mandatory, but schema downgrade remains an explicit
# operator decision. This script never overwrites the database automatically.
systemctl start unikorn-backup.service
systemctl stop unikorn-background-worker.service unikorn-frontend.service unikorn-backend.service
next_current="${app_root}/.rollback-current.$$"
next_previous="${app_root}/.rollback-previous.$$"
ln -s -- "${previous}" "${next_current}"
ln -s -- "${current}" "${next_previous}"
mv -Tf -- "${next_current}" "${app_root}/current"
mv -Tf -- "${next_previous}" "${app_root}/previous"
systemctl start unikorn-backend.service unikorn-frontend.service unikorn-background-worker.service

for _attempt in {1..20}; do
    if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8001/readyz >/dev/null && \
       curl --fail --silent --show-error --max-time 5 http://127.0.0.1:3000/health >/dev/null; then
        printf 'application rollback complete: %s\n' "${previous}"
        exit 0
    fi
    sleep 2
done
printf '%s\n' 'rolled-back application is not healthy; review schema compatibility and the verified backup' >&2
exit 1
