#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

readonly script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly backup_root="/etc/unikorn/nginx-backups"
readonly timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
readonly backup_dir="${backup_root}/${timestamp}"
readonly course_config="/etc/nginx/sites-available/course-scheduler"
readonly unikorn_config="/etc/nginx/sites-available/unikorn"
readonly shared_config="/etc/nginx/conf.d/00-unikorn-shared.conf"
readonly course_link="/etc/nginx/sites-enabled/course-scheduler"
readonly unikorn_link="/etc/nginx/sites-enabled/unikorn"

fail() { printf 'Nginx activation failed: %s\n' "$*" >&2; exit 1; }
[[ "${EUID}" -eq 0 ]] || fail "run as root through interactive sudo"
[[ -d "${backup_root}" && ! -L "${backup_root}" ]] || fail "unsafe backup root"
systemctl is-active --quiet courseplan.service || fail "CoursePlan is not active"
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:3000/health >/dev/null || \
    fail "frontend is not healthy"
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8001/readyz >/dev/null || \
    fail "backend is not ready"

install -d -o root -g root -m 0700 "${backup_dir}"
for path in "${course_config}" "${unikorn_config}" "${shared_config}"; do
    if [[ -e "${path}" || -L "${path}" ]]; then
        cp -a -- "${path}" "${backup_dir}/$(basename -- "${path}")"
    else
        : >"${backup_dir}/$(basename -- "${path}").absent"
    fi
done
for link in "${course_link}" "${unikorn_link}"; do
    name="$(basename -- "${link}")"
    if [[ -L "${link}" ]]; then
        readlink -- "${link}" >"${backup_dir}/${name}.link"
    elif [[ -e "${link}" ]]; then
        fail "enabled Nginx path is not a symlink: ${link}"
    else
        : >"${backup_dir}/${name}.link.absent"
    fi
done

restore_previous() {
    set +e
    for path in "${course_config}" "${unikorn_config}" "${shared_config}"; do
        name="$(basename -- "${path}")"
        if [[ -f "${backup_dir}/${name}.absent" ]]; then
            unlink -- "${path}" 2>/dev/null || true
        else
            cp -a -- "${backup_dir}/${name}" "${path}"
        fi
    done
    for link in "${course_link}" "${unikorn_link}"; do
        name="$(basename -- "${link}")"
        if [[ -f "${backup_dir}/${name}.link.absent" ]]; then
            unlink -- "${link}" 2>/dev/null || true
        else
            target="$(sed -n '1p' "${backup_dir}/${name}.link")"
            ln -sfn -- "${target}" "${link}"
        fi
    done
    nginx -t && systemctl reload nginx
}
trap 'status=$?; if (( status != 0 )); then restore_previous; fi; exit "${status}"' EXIT

install -o root -g root -m 0644 "${script_dir}/nginx/00-unikorn-shared.conf" "${shared_config}"
install -o root -g root -m 0644 "${script_dir}/nginx/course-scheduler.conf" "${course_config}"
install -o root -g root -m 0644 "${script_dir}/nginx/unikorn.conf" "${unikorn_config}"
ln -sfn -- "${course_config}" "${course_link}"
ln -sfn -- "${unikorn_config}" "${unikorn_link}"

nginx -t
systemctl reload nginx
split_ready=false
for _attempt in {1..40}; do
    if curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
        -H 'Host: unikorn.hkust-gz.edu.cn' http://127.0.0.1/api/healthz >/dev/null && \
       curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
        -H 'Host: scheduler.unikorn.hkust-gz.edu.cn' http://127.0.0.1/ >/dev/null; then
        split_ready=true
        break
    fi
    sleep 0.25
done
[[ "${split_ready}" == "true" ]] || fail "reloaded Nginx did not serve both host routes"
systemctl is-active --quiet courseplan.service
trap - EXIT
printf 'Nginx split activated; rollback snapshot: %s\n' "${backup_dir}"
