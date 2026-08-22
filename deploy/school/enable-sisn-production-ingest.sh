#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

[[ "${EUID}" -eq 0 ]] || {
    printf '%s\n' 'enable-sisn-production-ingest.sh requires root' >&2
    exit 1
}

readonly env_file="/etc/unikorn/unikorn.env"
readonly private_key="/etc/course-scheduler/credentials/sisn_push_private_key"
readonly public_key="/etc/unikorn/sisn-push-public.pem"
readonly archive_dir="/srv/unikorn/sisn-archive"
readonly baseline_path="/srv/unikorn/current/backend/app/data/pending/scheduler_offerings/26-27fall.json"

fail() {
    printf 'SISN production ingest setup failed: %s\n' "$*" >&2
    exit 1
}

systemctl is-active --quiet courseplan.service || fail 'CoursePlan is not active'
systemctl is-active --quiet unikorn-backend.service || fail 'UniKorn backend is not active'
[[ -f "${env_file}" ]] || fail 'UniKorn environment file is missing'
[[ -r "${private_key}" ]] || fail 'CoursePlan SISN signing key is unavailable'
[[ -f "${baseline_path}" ]] || fail 'reviewed 2610 baseline is missing from the active release'

umask 077
readonly env_backup="$(mktemp /etc/unikorn/.unikorn.env.sisn-backup.XXXXXX)"
readonly public_stage="$(mktemp /etc/unikorn/.sisn-public.XXXXXX)"
cleanup() {
    rm -f -- "${env_backup}" "${public_stage}"
}
trap cleanup EXIT

cp --preserve=mode,ownership -- "${env_file}" "${env_backup}"
openssl pkey -in "${private_key}" -pubout -out "${public_stage}" >/dev/null 2>&1
openssl pkey -pubin -in "${public_stage}" -noout >/dev/null 2>&1
install -o root -g unikorn -m 0640 "${public_stage}" "${public_key}"
install -d -o unikorn -g unikorn -m 0750 "${archive_dir}"

python3 - "${env_file}" "${baseline_path}" "${archive_dir}" "${public_key}" <<'PY'
import os
import stat
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
updates = {
    "SISN_SYNC_TERM": "2610",
    "SISN_SYNC_BASELINE_PATH": sys.argv[2],
    "SISN_SYNC_ARCHIVE_DIR": sys.argv[3],
    "SISN_PUSH_INGEST_ENABLED": "true",
    "SISN_PUSH_PUBLIC_KEY_PATH": sys.argv[4],
}
original = path.read_text(encoding="utf-8").splitlines()
seen: set[str] = set()
output: list[str] = []
for line in original:
    key = line.split("=", 1)[0].strip() if "=" in line else ""
    if key in updates:
        if key not in seen:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        continue
    output.append(line)
if output and output[-1]:
    output.append("")
for key, value in updates.items():
    if key not in seen:
        output.append(f"{key}={value}")

metadata = path.stat()
fd, temporary_name = tempfile.mkstemp(prefix=".unikorn.env.sisn.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(output).rstrip("\n") + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(temporary_name, metadata.st_uid, metadata.st_gid)
    os.chmod(temporary_name, stat.S_IMODE(metadata.st_mode))
    os.replace(temporary_name, path)
finally:
    if os.path.exists(temporary_name):
        os.unlink(temporary_name)
PY

if ! systemctl restart unikorn-backend.service; then
    cp --preserve=mode,ownership -- "${env_backup}" "${env_file}"
    systemctl restart unikorn-backend.service || true
    fail 'backend restart failed; environment was restored'
fi

for _attempt in {1..40}; do
    if curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
        http://127.0.0.1:8001/healthz >/dev/null; then
        break
    fi
    sleep 0.25
done
curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
    http://127.0.0.1:8001/healthz >/dev/null || {
        cp --preserve=mode,ownership -- "${env_backup}" "${env_file}"
        systemctl restart unikorn-backend.service || true
        fail 'backend health failed; environment was restored'
    }

status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --connect-timeout 2 --max-time 5 --request POST \
    --header 'Content-Type: application/json' --data '{}' \
    http://127.0.0.1:8001/scheduler/internal/sisn-ingest)"
[[ "${status}" == 401 ]] || {
    cp --preserve=mode,ownership -- "${env_backup}" "${env_file}"
    systemctl restart unikorn-backend.service || true
    fail "signed ingest did not fail closed (HTTP ${status}); environment was restored"
}

systemctl is-active --quiet courseplan.service || fail 'CoursePlan changed during setup'
printf '%s\n' 'SISN production ingest is enabled on the loopback backend and fails closed.'
