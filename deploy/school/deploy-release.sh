#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

readonly app_root="/srv/unikorn"
readonly releases_root="${app_root}/releases"
readonly node_bin="/opt/node-v20.19.6-linux-x64/bin/node"

backend_source=""
frontend_source=""
backend_sha=""
frontend_sha=""
release_id=""
activate=false

fail() { printf 'release deployment failed: %s\n' "$*" >&2; exit 1; }
usage() {
    printf '%s\n' "usage: $0 --backend-source DIR --frontend-source DIR --backend-sha SHA --frontend-sha SHA [--release-id ID] [--activate]" >&2
    exit 2
}

while (($#)); do
    case "$1" in
        --backend-source) backend_source="${2:-}"; shift 2 ;;
        --frontend-source) frontend_source="${2:-}"; shift 2 ;;
        --backend-sha) backend_sha="${2:-}"; shift 2 ;;
        --frontend-sha) frontend_sha="${2:-}"; shift 2 ;;
        --release-id) release_id="${2:-}"; shift 2 ;;
        --activate) activate=true; shift ;;
        *) usage ;;
    esac
done

[[ "${EUID}" -eq 0 ]] || fail "run as root through interactive sudo"
[[ "${backend_sha}" =~ ^[0-9a-f]{40}$ && "${frontend_sha}" =~ ^[0-9a-f]{40}$ ]] || \
    fail "full 40-character commit SHAs are required"
[[ "${backend_source}" == /* && -d "${backend_source}" && ! -L "${backend_source}" ]] || \
    fail "backend source must be an absolute, non-symlink directory"
[[ "${frontend_source}" == /* && -d "${frontend_source}" && ! -L "${frontend_source}" ]] || \
    fail "frontend source must be an absolute, non-symlink directory"
backend_source="$(realpath -e -- "${backend_source}")"
frontend_source="$(realpath -e -- "${frontend_source}")"
[[ "$(git -C "${backend_source}" rev-parse HEAD)" == "${backend_sha}" ]] || fail "backend SHA mismatch"
[[ "$(git -C "${frontend_source}" rev-parse HEAD)" == "${frontend_sha}" ]] || fail "frontend SHA mismatch"
[[ -z "$(GIT_OPTIONAL_LOCKS=0 git -C "${backend_source}" status --porcelain=v1 --untracked-files=all)" ]] || \
    fail "backend source is not a clean committed checkout"
[[ -z "$(GIT_OPTIONAL_LOCKS=0 git -C "${frontend_source}" status --porcelain=v1 --untracked-files=all)" ]] || \
    fail "frontend source is not a clean committed checkout"

if [[ -z "${release_id}" ]]; then
    release_id="$(date -u +%Y%m%dT%H%M%SZ)-${backend_sha:0:12}-${frontend_sha:0:12}"
fi
[[ "${release_id}" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,40}-[0-9a-f]{7,40}$ ]] || \
    fail "invalid release ID"
readonly release_path="${releases_root}/${release_id}"
readonly stage_path="${releases_root}/.${release_id}.stage.$$"
[[ ! -e "${release_path}" && ! -L "${release_path}" ]] || fail "release already exists"
[[ -d "${releases_root}" && ! -L "${releases_root}" ]] || fail "unsafe releases root"
[[ -x "${node_bin}" ]] || fail "pinned Node runtime is missing"
npm_cli="$(dpkg -L npm | awk '/\/npm-cli\.js$/ {print; exit}')"
[[ -n "${npm_cli}" && -f "${npm_cli}" ]] || fail "npm CLI is not installed"

if [[ "${activate}" == "true" ]]; then
    [[ -f /etc/unikorn/unikorn.env && ! -L /etc/unikorn/unikorn.env ]] || \
        fail "active environment file is absent or unsafe"
    [[ "$(stat -c '%U:%G:%a' /etc/unikorn/unikorn.env)" == "root:unikorn:640" ]] || \
        fail "environment file must be root:unikorn mode 0640"
    systemd-run --quiet --wait --collect --pipe \
        --unit="unikorn-env-check-$$" \
        --property=EnvironmentFile=/etc/unikorn/unikorn.env \
        /usr/bin/python3 -c '
import os
required = ("SECRET_KEY", "JWT_SECRET_KEY", "DATABASE_URL", "REDIS_URL", "FRONTEND_BASE_URL")
missing = [name for name in required if not os.environ.get(name)]
if missing:
    raise SystemExit("missing required environment keys: " + ", ".join(missing))
if os.environ["DATABASE_URL"] != "postgresql:///prod_unikorn":
    raise SystemExit("DATABASE_URL must use local peer authentication for prod_unikorn")
if os.environ["REDIS_URL"] != "redis://127.0.0.1:6380/0":
    raise SystemExit("REDIS_URL must use the isolated loopback instance")
if os.environ["FRONTEND_BASE_URL"] != "https://unikorn.hkust-gz.edu.cn":
    raise SystemExit("FRONTEND_BASE_URL is not the school production URL")
for name in ("SECRET_KEY", "JWT_SECRET_KEY"):
    if len(os.environ[name]) < 32 or len(set(os.environ[name])) < 8:
        raise SystemExit(name + " is not sufficiently strong")
enabled = os.environ.get("CAMPUS_SSO_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
if enabled and not os.environ.get("CAMPUS_SSO_CLIENT_SECRET"):
    raise SystemExit("CAMPUS_SSO_ENABLED requires CAMPUS_SSO_CLIENT_SECRET")
'
fi

install -d -o root -g root -m 0755 "${stage_path}"
cleanup_stage() {
    if [[ -d "${stage_path}" && "${stage_path}" == "${releases_root}/."*".stage.$$" ]]; then
        find "${stage_path}" -depth -delete
    fi
}
trap 'status=$?; if (( status != 0 )); then cleanup_stage; fi; exit "${status}"' EXIT
install -d -o root -g root -m 0755 "${stage_path}/backend" "${stage_path}/frontend"

rsync -a --delete \
    --exclude='.git' --exclude='.env' --exclude='.venv' --exclude='venv' \
    --exclude='.codex-venv' --exclude='.idea' --exclude='__pycache__' \
    "${backend_source}/" "${stage_path}/backend/"
rsync -a --delete \
    --exclude='.git' --exclude='.env' --exclude='.nuxt' --exclude='.output' \
    --exclude='node_modules' --exclude='.idea' \
    "${frontend_source}/" "${stage_path}/frontend/"

python3 -m venv "${stage_path}/backend/.venv"
"${stage_path}/backend/.venv/bin/pip" install --disable-pip-version-check \
    --require-hashes --only-binary=:all: \
    -r "${stage_path}/backend/build-requirements.lock"
"${stage_path}/backend/.venv/bin/pip" install --disable-pip-version-check \
    --require-hashes --no-build-isolation \
    -r "${stage_path}/backend/requirements.lock"
"${stage_path}/backend/.venv/bin/pip" check

(
    cd -- "${stage_path}/frontend"
    export PATH="$(dirname -- "${node_bin}"):${PATH}"
    export CI=1
    export NUXT_TELEMETRY_DISABLED=1
    NODE_ENV=development "${node_bin}" "${npm_cli}" ci --no-audit --no-fund
    NODE_ENV=production \
    NUXT_PUBLIC_API_BASE_URL= \
    NUXT_API_INTERNAL_BASE_URL=http://127.0.0.1:8081 \
    NUXT_PUBLIC_APP_BUILD_VERSION="${frontend_sha}" \
        "${node_bin}" "${npm_cli}" run build
    [[ -f .output/server/index.mjs ]] || fail "Nuxt output is incomplete"
)
find "${stage_path}/frontend/node_modules" -depth -delete

python3 - "${stage_path}/release.json" "${release_id}" "${backend_sha}" "${frontend_sha}" <<'PY'
import datetime
import json
import sys

path, release_id, backend_sha, frontend_sha = sys.argv[1:]
with open(path, "x", encoding="utf-8") as output:
    json.dump({
        "release_id": release_id,
        "backend_sha": backend_sha,
        "frontend_sha": frontend_sha,
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, output, sort_keys=True)
    output.write("\n")
PY

chown -R unikorn:unikorn "${stage_path}"
chmod -R u=rwX,g=rX,o= "${stage_path}"
mv -T -- "${stage_path}" "${release_path}"
trap - EXIT

if [[ "${activate}" != "true" ]]; then
    printf 'release built but not activated: %s\n' "${release_path}"
    exit 0
fi

old_current=""
if [[ -L "${app_root}/current" ]]; then
    old_current="$(readlink -f -- "${app_root}/current")"
    [[ "${old_current}" == "${releases_root}/"* && -d "${old_current}" ]] || \
        fail "current release link is unsafe"
    systemctl start unikorn-backup.service
fi

systemctl stop unikorn-frontend.service unikorn-backend.service >/dev/null 2>&1 || true
if ! systemctl start "unikorn-migrate@${release_id}.service"; then
    [[ -z "${old_current}" ]] || systemctl start unikorn-backend.service unikorn-frontend.service
    fail "Alembic migration failed; current release was not changed"
fi

current_stage="${app_root}/.current.${release_id}.$$"
ln -s -- "${release_path}" "${current_stage}"
mv -Tf -- "${current_stage}" "${app_root}/current"
if [[ -n "${old_current}" ]]; then
    previous_stage="${app_root}/.previous.${release_id}.$$"
    ln -s -- "${old_current}" "${previous_stage}"
    mv -Tf -- "${previous_stage}" "${app_root}/previous"
fi

systemctl daemon-reload
systemctl enable unikorn-backend.service unikorn-frontend.service unikorn-backup.timer >/dev/null
systemctl restart unikorn-backend.service unikorn-frontend.service
healthy=false
for _attempt in {1..30}; do
    if curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
        http://127.0.0.1:8001/readyz >/dev/null && \
       curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
        http://127.0.0.1:3000/health | python3 -c \
        "import json,sys; assert json.load(sys.stdin).get('version') == '${frontend_sha}'"; then
        healthy=true
        break
    fi
    sleep 2
done
if [[ "${healthy}" != "true" ]]; then
    systemctl stop unikorn-frontend.service unikorn-backend.service >/dev/null 2>&1 || true
    if [[ -n "${old_current}" ]]; then
        failed_stage="${app_root}/.failed-current.${release_id}.$$"
        ln -s -- "${old_current}" "${failed_stage}"
        mv -Tf -- "${failed_stage}" "${app_root}/current"
        systemctl start unikorn-backend.service unikorn-frontend.service
    fi
    fail "candidate health failed; application link was reverted (database downgrade is never automatic)"
fi
systemctl enable --now unikorn-backup.timer >/dev/null
printf 'release activated: %s\n' "${release_path}"
