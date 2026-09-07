#!/usr/bin/env python3
"""Install the reviewed MakerSpace worker through interactive sudo.

The operator must first approve the accompanying migration-plan.md. This does
not activate the public route, start a worker, migrate SQL or restart UniKorn.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import secrets
import shutil
import socket
import subprocess
import tempfile
import urllib.request

SOURCE = Path(__file__).resolve().parent


def run(*args):
    subprocess.run(args, check=True)


def directory(path, mode=0o700):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError('Refusing symlink directory')
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    os.chown(path, 0, 0); path.chmod(mode)
    return path


def install(source, destination, mode=0o644):
    if Path(destination).is_symlink():
        raise RuntimeError('Refusing symlink destination')
    shutil.copyfile(source, destination)
    os.chown(destination, 0, 0); os.chmod(destination, mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approval-reference', required=True)
    parser.add_argument('--install-docker', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not os.isatty(0):
        raise RuntimeError('Use an interactive sudo terminal')
    if socket.gethostname() != 'tpds-planner-app-ub2403-prod-01':
        raise RuntimeError('Unexpected production host')
    if len(args.approval_reference.strip()) < 12:
        raise RuntimeError('Record the actual current approval reference')
    if not shutil.which('docker'):
        if not args.install_docker:
            raise RuntimeError('Docker is absent; installation must be explicit in the approved plan')
        run('apt-get', 'update')
        run('apt-get', 'install', '-y', 'docker.io', 'bzip2', 'e2fsprogs', 'git', 'iptables')
    run('systemctl', 'enable', '--now', 'docker')
    release = json.loads((SOURCE / 'gvisor-release.json').read_text())
    architecture = platform.machine()
    expected = release['archives'][architecture]
    url = f"https://storage.googleapis.com/gvisor/releases/release/{release['version']}/{architecture}/gvisor.tar.bz2"
    with tempfile.TemporaryDirectory(prefix='makerspace-install-') as temporary:
        archive = Path(temporary) / 'gvisor.tar.bz2'
        urllib.request.urlretrieve(url, archive)
        digest = hashlib.sha512()
        with archive.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError('gVisor archive checksum mismatch')
        run('tar', '-xjf', str(archive), '-C', '/usr/local/bin')
    run('/usr/local/bin/runsc', 'install')
    run('systemctl', 'reload', 'docker')
    if subprocess.run(['getent', 'group', 'unikorn-maker-review'], stdout=subprocess.DEVNULL).returncode:
        run('groupadd', '--system', 'unikorn-maker-review')
    try: pwd.getpwnam('unikorn-maker-fetch')
    except KeyError: run('useradd', '--system', '--no-create-home', '--shell', '/usr/sbin/nologin', 'unikorn-maker-fetch')
    run('usermod', '-a', '-G', 'unikorn-maker-review', 'unikorn')
    root = directory('/srv/unikorn-makerspace', 0o755)
    directory('/run/unikorn-makerspace', 0o711)
    for name in ('volumes', 'backups'): directory(root / name)
    directory(root / 'mounts', 0o711)
    review = directory(root / 'reviews', 0o750)
    run('chown', 'root:unikorn-maker-review', str(review))
    library = directory('/usr/local/libexec/unikorn-makerspace', 0o755)
    for name in ('runtime.py', 'firewall.py', 'static-server.mjs', 'backup.py'):
        install(SOURCE / name, library / name, 0o755)
    config_dir = directory('/etc/unikorn-makerspace', 0o750)
    if not (config_dir / 'worker.json').exists():
        token = secrets.token_urlsafe(48)
        config = json.loads((SOURCE / 'worker.example.json').read_text())
        config['token'] = token
        (config_dir / 'worker.json').write_text(json.dumps(config))
        (config_dir / 'worker.json').chmod(0o600)
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        backend = config_dir / 'backend.env'
        if backend.exists(): raise RuntimeError('Existing backend key file requires manual reconciliation')
        backend.write_text(f'MAKERSPACE_ENCRYPTION_KEY={key}\nMAKERSPACE_WORKER_TOKEN={token}\nMAKERSPACE_HOSTING_ENABLED=false\n')
        run('chown', 'root:unikorn', str(backend)); backend.chmod(0o640)
    run('chown', 'root:unikorn', str(config_dir))
    install(SOURCE / 'github_known_hosts', config_dir / 'github_known_hosts')
    # The fetch account needs only this public host-key file, not this directory
    # of private credentials. Put its separate copy in a public root-owned path.
    install(SOURCE / 'github_known_hosts', library / 'github_known_hosts')
    dropin = directory('/etc/systemd/system/unikorn-backend.service.d', 0o755)
    (dropin / 'makerspace.conf').write_text('[Service]\nEnvironmentFile=/etc/unikorn-makerspace/backend.env\n')
    for name in ('unikorn-makerspace-firewall.service', 'unikorn-makerspace-worker.service', 'unikorn-makerspace-backup.service', 'unikorn-makerspace-backup.timer'):
        install(SOURCE / name, '/etc/systemd/system/' + name)
    if subprocess.run(['docker', 'network', 'inspect', 'unikorn-makerspace'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        run('docker', 'network', 'create', '--driver', 'bridge', '--subnet', '172.30.91.0/24', '--opt', 'com.docker.network.bridge.name=br-makerspace', '--opt', 'com.docker.network.bridge.enable_icc=false', 'unikorn-makerspace')
    run('python3', str(library / 'firewall.py'))
    config = json.loads((config_dir / 'worker.json').read_text())
    for image in set(config['images'].values()): run('docker', 'pull', image)
    run('systemctl', 'daemon-reload')
    run('systemd-analyze', 'verify', '/etc/systemd/system/unikorn-makerspace-worker.service')
    (root / 'installation.json').write_text(json.dumps({'approval_reference': args.approval_reference, 'gvisor_release': release['version'], 'hosting_enabled': False}))
    print('Installed with hosting disabled. Complete the approved migration, Nginx, sandbox verification and paired release plan before activation.')


if __name__ == '__main__':
    main()
