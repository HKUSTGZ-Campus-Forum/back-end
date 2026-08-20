#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly backup_dir="/srv/unikorn/backups/database"
readonly expected_database="prod_unikorn"
readonly lock_file="${backup_dir}/.backup.lock"

fail() {
    printf 'backup failed: %s\n' "$*" >&2
    exit 1
}

[[ "$(id -un)" == "unikorn" ]] || fail "must run as the unikorn user"
[[ -d "${backup_dir}" && ! -L "${backup_dir}" ]] || fail "unsafe backup directory"
[[ "$(readlink -f -- "${backup_dir}")" == "${backup_dir}" ]] || fail "backup path changed"
[[ "$(stat -c '%U:%G:%a' -- "${backup_dir}")" == "unikorn:unikorn:750" ]] || \
    fail "backup directory must be unikorn:unikorn mode 0750"
[[ -x .venv/bin/python && -f app/scripts/create_verified_database_backup.py ]] || \
    fail "current backend release is incomplete"

exec 9>"${lock_file}"
flock -n 9 || fail "another backup is already running"

readonly timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
readonly archive="${backup_dir}/${expected_database}-${timestamp}.dump"
readonly list_file="${archive}.list"
readonly manifest="${archive}.json"

result="$(.venv/bin/python -m app.scripts.create_verified_database_backup \
    --output "${archive}" --expected-database "${expected_database}")"
archive_sha="$(printf '%s' "${result}" | .venv/bin/python -c \
    'import json,sys; print(json.load(sys.stdin)["sha256"])')"
[[ "${archive_sha}" =~ ^[0-9a-f]{64}$ ]] || fail "backup helper returned an invalid digest"

pg_restore --list "${archive}" >"${list_file}"
[[ -s "${list_file}" ]] || fail "pg_restore produced an empty list"
list_sha="$(sha256sum "${list_file}" | awk '{print $1}')"
actual_sha="$(sha256sum "${archive}" | awk '{print $1}')"
[[ "${actual_sha}" == "${archive_sha}" ]] || fail "archive digest changed"

.venv/bin/python - "${manifest}" "${archive}" "${archive_sha}" "${list_sha}" <<'PY'
import json
import os
import sys

manifest, archive, archive_sha, list_sha = sys.argv[1:]
payload = {
    "status": "verified",
    "database": "prod_unikorn",
    "archive": os.path.basename(archive),
    "size": os.stat(archive).st_size,
    "sha256": archive_sha,
    "pg_restore_list_sha256": list_sha,
}
with open(manifest, "x", encoding="utf-8") as output:
    json.dump(payload, output, sort_keys=True)
    output.write("\n")
PY

printf '%s  %s\n' "${archive_sha}" "$(basename -- "${archive}")" >"${archive}.sha256"
printf '%s  %s\n' "${list_sha}" "$(basename -- "${list_file}")" >"${list_file}.sha256"
chmod 0600 -- "${archive}" "${list_file}" "${manifest}" \
    "${archive}.sha256" "${list_file}.sha256"

# Keep the newest 14 days. Only delete fixed-name backup families in the exact,
# validated backup directory; never follow symlinks or expand a broad path.
while IFS= read -r -d '' expired; do
    name="$(basename -- "${expired}")"
    [[ "${name}" =~ ^prod_unikorn-[0-9]{8}T[0-9]{6}Z\.dump$ ]] || \
        fail "refusing unexpected retention candidate: ${name}"
    for suffix in "" ".list" ".json" ".sha256" ".list.sha256"; do
        candidate="${backup_dir}/${name}${suffix}"
        if [[ -f "${candidate}" && ! -L "${candidate}" ]]; then
            unlink -- "${candidate}"
        fi
    done
done < <(find "${backup_dir}" -xdev -maxdepth 1 -type f \
    -name 'prod_unikorn-????????T??????Z.dump' -mtime +13 -print0)

printf '%s\n' "${result}"
