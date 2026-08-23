#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

readonly script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly state_root="/var/lib/unikorn-school-deploy"
readonly libexec_root="/usr/local/libexec"

fail() { printf 'school production controller install failed: %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || fail "run as root through interactive sudo"
[[ "${script_dir}" == /* && -d "${script_dir}/systemd" ]] || fail "invalid script directory"
systemctl is-active --quiet courseplan.service || fail "CoursePlan must be active before installation"

python3 "${script_dir}/school-production-controller.py" \
    --validate-file "${script_dir}/school-production-release.json"
python3 -m py_compile "${script_dir}/school-production-controller.py"
bash -n "${script_dir}/deploy-release.sh" "${script_dir}/verify-local.sh"

install -d -o root -g root -m 0755 "${libexec_root}"
install -d -o root -g root -m 0700 "${state_root}"
install -d -o root -g root -m 0700 "${state_root}/home" "${state_root}/cache"
install -o root -g root -m 0755 \
    "${script_dir}/school-production-controller.py" \
    "${libexec_root}/unikorn-school-production-controller"
install -o root -g root -m 0755 \
    "${script_dir}/deploy-release.sh" \
    "${libexec_root}/unikorn-school-deploy-release"
install -o root -g root -m 0755 \
    "${script_dir}/verify-local.sh" \
    "${libexec_root}/unikorn-school-verify-local"

for unit_name in unikorn-school-production-deploy.service unikorn-school-production-deploy.timer; do
    unit_source="${script_dir}/systemd/${unit_name}"
    [[ -f "${unit_source}" && ! -L "${unit_source}" ]] || fail "unsafe unit source: ${unit_name}"
    install -o root -g root -m 0644 "${unit_source}" "/etc/systemd/system/${unit_name}"
done

systemd-analyze verify \
    /etc/systemd/system/unikorn-school-production-deploy.service \
    /etc/systemd/system/unikorn-school-production-deploy.timer
systemctl daemon-reload
systemctl enable --now unikorn-school-production-deploy.timer
systemctl start unikorn-school-production-deploy.service
systemctl is-active --quiet unikorn-school-production-deploy.timer
systemctl is-active --quiet courseplan.service || fail "CoursePlan changed during installation"
printf '%s\n' "school production controller installed and timer enabled"
