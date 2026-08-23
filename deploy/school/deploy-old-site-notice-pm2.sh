#!/usr/bin/env bash
set -Eeuo pipefail

# Old axfff host only. This switches the two legacy browser applications to the
# static migration notice without changing the school deployment or requiring
# a privileged Nginx reload. The existing release symlinks remain the rollback
# boundary.

die() {
    printf 'old-site notice deployment failed: %s\n' "$*" >&2
    exit 1
}

release_id=${1:?Usage: deploy-old-site-notice-pm2.sh RELEASE_ID}
[[ ${release_id} =~ ^legacy-notice-[0-9]{8}T[0-9]{6}Z$ ]] \
    || die "invalid release id: ${release_id}"

readonly forum_root=/data/prod_unikorn/front-end
readonly scheduler_root=/data/course_scheduler
readonly forum_stage="${forum_root}/_incoming/${release_id}"
readonly scheduler_stage="${scheduler_root}/_incoming/${release_id}"
readonly forum_release="${forum_root}/releases/${release_id}"
readonly scheduler_release="${scheduler_root}/releases/${release_id}"
readonly forum_current="${forum_root}/current"
readonly scheduler_current="${scheduler_root}/current"

for command_name in cmp curl grep install ln mv node pm2 readlink sleep; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || die "required command not found: ${command_name}"
done

for staged_file in \
    "${forum_stage}/legacy-notice-server.mjs" \
    "${forum_stage}/old-site-notice.html" \
    "${scheduler_stage}/legacy-notice-server.mjs" \
    "${scheduler_stage}/legacy-notice-package.json" \
    "${scheduler_stage}/old-site-notice.html"; do
    [[ -f ${staged_file} ]] || die "staged file not found: ${staged_file}"
done

[[ -L ${forum_current} ]] || die "forum current link is missing"
[[ -L ${scheduler_current} ]] || die "scheduler current link is missing"
[[ ! -e ${forum_release} ]] || die "forum release already exists: ${forum_release}"
[[ ! -e ${scheduler_release} ]] || die "scheduler release already exists: ${scheduler_release}"

node --check "${forum_stage}/legacy-notice-server.mjs"
grep -Fq 'https://unikorn.hkust-gz.edu.cn/' "${forum_stage}/old-site-notice.html"
grep -Fq '目前新站仅可在校园网内访问，或通过学校 VPN 访问' \
    "${forum_stage}/old-site-notice.html"
grep -Fq 'currently available only on the campus network or through the campus VPN' \
    "${forum_stage}/old-site-notice.html"
cmp -s \
    "${forum_stage}/old-site-notice.html" \
    "${scheduler_stage}/old-site-notice.html" \
    || die "forum and scheduler notices differ"

readonly forum_previous=$(readlink -f "${forum_current}")
readonly scheduler_previous=$(readlink -f "${scheduler_current}")
[[ -d ${forum_previous} ]] || die "forum rollback release is missing: ${forum_previous}"
[[ -d ${scheduler_previous} ]] || die "scheduler rollback release is missing: ${scheduler_previous}"

install -d -m 0755 "${forum_release}/.output/server/nginx"
install -m 0644 \
    "${forum_stage}/legacy-notice-server.mjs" \
    "${forum_release}/.output/server/index.mjs"
install -m 0644 \
    "${forum_stage}/old-site-notice.html" \
    "${forum_release}/.output/server/nginx/old-site-notice.html"
printf 'previous_release=%s\n' "${forum_previous}" >"${forum_release}/.legacy-notice-rollback"

install -d -m 0755 "${scheduler_release}/nginx"
install -m 0644 \
    "${scheduler_stage}/legacy-notice-server.mjs" \
    "${scheduler_release}/server.mjs"
install -m 0644 \
    "${scheduler_stage}/legacy-notice-package.json" \
    "${scheduler_release}/package.json"
install -m 0644 \
    "${scheduler_stage}/old-site-notice.html" \
    "${scheduler_release}/nginx/old-site-notice.html"
printf 'previous_release=%s\n' "${scheduler_previous}" >"${scheduler_release}/.legacy-notice-rollback"

forum_switched=false
scheduler_switched=false

switch_link() {
    local link_path=$1
    local target_path=$2
    local temporary_link="${link_path}.legacy-notice.$$"

    ln -s "${target_path}" "${temporary_link}"
    mv -Tf "${temporary_link}" "${link_path}"
}

wait_for_notice() {
    local url=$1
    local attempt

    for attempt in {1..15}; do
        if curl -fsS --max-time 5 "${url}" 2>/dev/null \
            | grep -Fq 'UniKorn 已迁移 | UniKorn has moved'; then
            return 0
        fi
        sleep 1
    done
    return 1
}

wait_for_http() {
    local url=$1
    local attempt

    for attempt in {1..15}; do
        if curl -fsS --max-time 5 --output /dev/null "${url}" 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

rollback() {
    local exit_code=$?
    local rollback_failed=false
    trap - ERR INT TERM
    set +e
    printf 'Notice health check failed; restoring both legacy applications.\n' >&2
    if [[ ${scheduler_switched} == true ]]; then
        if ! switch_link "${scheduler_current}" "${scheduler_previous}" \
            || ! pm2 restart courseplan --update-env >&2 \
            || ! wait_for_http http://127.0.0.1:3002/; then
            rollback_failed=true
            printf 'Scheduler rollback did not recover a healthy HTTP service.\n' >&2
        fi
    fi
    if [[ ${forum_switched} == true ]]; then
        if ! switch_link "${forum_current}" "${forum_previous}" \
            || ! pm2 restart prod-unikorn-frontend --update-env >&2 \
            || ! wait_for_http http://127.0.0.1:3000/; then
            rollback_failed=true
            printf 'Forum rollback did not recover a healthy HTTP service.\n' >&2
        fi
    fi
    if [[ ${rollback_failed} == true ]]; then
        printf 'ROLLBACK FAILED; PM2 state was not saved. Manual recovery is required.\n' >&2
        exit 70
    fi
    if ! pm2 save --force >&2; then
        printf 'Rollback recovered both services, but persisting PM2 state failed.\n' >&2
        exit 71
    fi
    printf 'Rollback restored and verified both legacy applications.\n' >&2
    exit "${exit_code}"
}
trap rollback ERR INT TERM

switch_link "${forum_current}" "${forum_release}"
forum_switched=true
pm2 restart prod-unikorn-frontend --update-env
wait_for_notice http://127.0.0.1:3000/

switch_link "${scheduler_current}" "${scheduler_release}"
scheduler_switched=true
pm2 restart courseplan --update-env
wait_for_notice http://127.0.0.1:3002/

pm2 save --force
trap - ERR INT TERM

printf 'Legacy migration notice %s deployed.\n' "${release_id}"
printf 'Forum rollback target: %s\n' "${forum_previous}"
printf 'Scheduler rollback target: %s\n' "${scheduler_previous}"
