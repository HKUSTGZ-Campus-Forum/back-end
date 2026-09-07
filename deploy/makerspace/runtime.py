#!/usr/bin/env python3
"""Trusted host runner. Repository commands execute ONLY inside runsc containers.

Install this reviewed directory root-owned. Never run a copy from a creator repo.
The control API assigns identities; authors cannot choose host paths, images,
container options, networks, ports or quotas.
"""
from __future__ import annotations

import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path('/srv/unikorn-makerspace')
CONFIG = Path('/etc/unikorn-makerspace/worker.json')
NETWORK = 'unikorn-makerspace'
LABEL = 'unikorn.makerspace=1'
IDENTIFIER = re.compile(r'[a-f0-9]{32}\Z')
SHA = re.compile(r'[a-f0-9]{40}\Z')
REPOSITORY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z')
REF = re.compile(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,99}\Z')


class RuntimeFailure(Exception):
    pass


def command(args, *, timeout=60, check=True, env=None):
    # No shell=True, and no host execution of build/start commands.
    result = subprocess.run([str(value) for value in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, env=env)
    if check and result.returncode:
        raise RuntimeFailure('command failed: ' + str(args[0]) + '\n' + result.stdout.decode(errors='replace')[-12000:])
    return result.stdout.decode(errors='replace').strip()


def trusted(path):
    info = path.lstat()
    if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
        raise RuntimeFailure('untrusted platform file: ' + str(path))


def read_config():
    trusted(CONFIG)
    data = json.loads(CONFIG.read_text())
    if data['api'] != 'http://127.0.0.1:8001/makerspace' or len(data['token']) < 32:
        raise RuntimeFailure('invalid control endpoint')
    for image in data['images'].values():
        if not re.fullmatch(r'[a-z0-9./:_-]+@sha256:[a-f0-9]{64}', image):
            raise RuntimeFailure('base images must be pinned by digest')
    return data


def call_api(config, path, data):
    request = urllib.request.Request(config['api'] + path, data=json.dumps(data).encode(), headers={'Authorization': 'Bearer ' + config['token'], 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=25) as response:
        return json.load(response)


def save_state(state):
    path = ROOT / 'state.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state))
    temporary.chmod(0o600)
    temporary.replace(path)


def disk(name, size_mb, owner=65532):
    # Names are platform-generated identifiers, never repository values.
    if not re.fullmatch(r'(build|preview|public)-[a-f0-9]{32}', name):
        raise RuntimeFailure('invalid volume identity')
    image, mount = ROOT / 'volumes' / (name + '.img'), ROOT / 'mounts' / name
    if not image.exists():
        if shutil.disk_usage(ROOT).free < (size_mb + 10240) * 1024**2:
            raise RuntimeFailure('server disk reserve reached')
        command(['fallocate', '-l', str(size_mb) + 'M', image])
        image.chmod(0o600)
        command(['mkfs.ext4', '-q', '-m', '0', '-N', '65536', image])
    mount.mkdir(mode=0o700, exist_ok=True)
    if not os.path.ismount(mount):
        command(['mount', '-o', 'loop,nodev,nosuid', image, mount])
    os.chown(mount, owner, owner)
    mount.chmod(0o700)
    return mount


def destroy_disk(name):
    if not re.fullmatch(r'build-[a-f0-9]{32}', name):
        raise RuntimeFailure('only retired build volumes can be removed automatically')
    mount = ROOT / 'mounts' / name
    if os.path.ismount(mount):
        command(['umount', mount])
    mount.rmdir() if mount.exists() else None
    (ROOT / 'volumes' / (name + '.img')).unlink(missing_ok=True)


def digest_tree(root):
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort(); files.sort()
        for name in sorted(dirs + files):
            path = Path(directory) / name
            info = path.lstat()
            digest.update(str(path.relative_to(root)).encode() + b'\0' + str(stat.S_IMODE(info.st_mode)).encode() + b'\0')
            if stat.S_ISLNK(info.st_mode):
                digest.update(b'link\0' + os.readlink(path).encode())
            elif stat.S_ISREG(info.st_mode):
                with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(chunk)
            elif not stat.S_ISDIR(info.st_mode):
                raise RuntimeFailure('special files are not allowed in artifacts')
            digest.update(b'\0')
    return digest.hexdigest()


def sandbox_options(name, work, image, *, build=False, data=None, port=None):
    args = ['docker', 'run', '--detach', '--name', name, '--label', LABEL,
            '--runtime', 'runsc', '--network', NETWORK, '--dns', '1.1.1.1',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--read-only', '--user', '65532:65532', '--cpus', '0.5',
            '--memory', '768m' if build else '256m', '--memory-swap', '768m' if build else '256m',
            '--pids-limit', '64', '--ulimit', 'nofile=256:256', '--ulimit', 'core=0:0',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=67108864,mode=1777',
            '--log-driver', 'local', '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1', '--log-opt', 'compress=false',
            '--mount', f'type=bind,src={work},dst=/workspace' + ('' if build else ',readonly'),
            '--env', 'HOME=/tmp', '--env', 'PORT=8080', '--env', 'HOST=0.0.0.0',
            '--env', 'DATA_DIR=/data', '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'PYTHONUSERBASE=/workspace/.python',
            '--env', 'PATH=/workspace/.python/bin:/usr/local/bin:/usr/bin:/bin']
    if data:
        args += ['--mount', f'type=bind,src={data},dst=/data']
    if port:
        args += ['--publish', f'127.0.0.1:{port}:8080']
    return args


def check_sandbox(config):
    # Require both a trusted runtime registration and the exact isolation rules.
    runtimes = json.loads(command(['docker', 'info', '--format', '{{json .Runtimes}}']))
    if 'runsc' not in runtimes:
        raise RuntimeFailure('runsc runtime is missing')
    trusted(Path('/usr/local/bin/runsc'))
    daemon = Path('/etc/docker/daemon.json')
    trusted(daemon)
    if json.loads(daemon.read_text()).get('runtimes', {}).get('runsc', {}).get('path') != '/usr/local/bin/runsc':
        raise RuntimeFailure('unexpected sandbox runtime path')
    command(['iptables', '-C', 'DOCKER-USER', '-i', 'br-makerspace', '-j', 'MAKERSPACE-EGRESS'])
    command(['iptables', '-C', 'INPUT', '-i', 'br-makerspace', '-j', 'MAKERSPACE-HOST'])
    command(['iptables', '-C', 'MAKERSPACE-EGRESS', '-j', 'DROP'])
    command(['iptables', '-C', 'MAKERSPACE-HOST', '-j', 'DROP'])
    from firewall import EGRESS, HOST
    for chain, rules in [('MAKERSPACE-EGRESS', EGRESS), ('MAKERSPACE-HOST', HOST)]:
        actual = command(['iptables', '-S', chain])
        if len([line for line in actual.splitlines() if line.startswith('-A ')]) != len(rules):
            raise RuntimeFailure('sandbox firewall policy changed')
        for rule in rules:
            command(['iptables', '-C', chain] + rule)
    network = json.loads(command(['docker', 'network', 'inspect', NETWORK]))[0]
    if network.get('EnableIPv6') or network['Options'].get('com.docker.network.bridge.name') != 'br-makerspace':
        raise RuntimeFailure('unexpected sandbox network')


def validate_job(job):
    if not IDENTIFIER.fullmatch(job['id']) or not IDENTIFIER.fullmatch(job['space_id']) or job['kind'] not in ('build', 'publish'):
        raise RuntimeFailure('invalid job identity')
    snapshot = job['snapshot']
    if not REPOSITORY.fullmatch(snapshot['repository']) or not REF.fullmatch(job['source_ref']) or '..' in job['source_ref']:
        raise RuntimeFailure('invalid source')
    settings = snapshot['settings']
    if settings['runtime'] not in ('static', 'node', 'python'):
        raise RuntimeFailure('unsupported runtime')
    for key in ('directory', 'output_directory'):
        value = settings[key]
        if not re.fullmatch(r'[A-Za-z0-9_./-]{1,160}', value) or value.startswith('/') or '..' in value.split('/'):
            raise RuntimeFailure('invalid directory')
    for key in ('build_command', 'start_command'):
        if not isinstance(settings[key], str) or len(settings[key]) > 1000 or '\0' in settings[key]:
            raise RuntimeFailure('invalid container command')


def choose_port(state):
    used = {value for item in state.values() for key, value in item.items() if key.endswith('_port')}
    for port in range(20000, 20100):
        if port in used:
            continue
        with socket.socket() as check:
            try:
                check.bind(('127.0.0.1', port))
                return port
            except OSError:
                pass
    raise RuntimeFailure('runtime port capacity reached')


def container_log(name):
    return command(['docker', 'logs', '--tail', '150', name], check=False)[-16000:]


def fetch_source(job, work):
    account = pwd.getpwnam('unikorn-maker-fetch')
    os.chown(work, account.pw_uid, account.pw_gid)
    with tempfile.TemporaryDirectory(prefix='makerspace-key-', dir='/run/unikorn-makerspace') as directory:
        keydir = Path(directory)
        os.chown(keydir, account.pw_uid, account.pw_gid)
        key = keydir / 'key'
        key.write_text(job['private_key']); key.chmod(0o600)
        os.chown(key, account.pw_uid, account.pw_gid)
        env = {'PATH': '/usr/bin:/bin', 'HOME': directory, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0', 'GIT_SSH_COMMAND': f'/usr/bin/ssh -F /dev/null -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/usr/local/libexec/unikorn-makerspace/github_known_hosts -o ConnectTimeout=15'}
        git = ['runuser', '-u', account.pw_name, '--', 'git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.file.allow=never', '-c', 'protocol.ext.allow=never']
        command(git + ['init', work / 'repo'], env=env)
        git += ['-C', work / 'repo']
        remote = 'git@github.com:' + job['snapshot']['repository'] + '.git'
        command(git + ['fetch', '--depth=1', '--no-tags', remote, job['source_ref']], timeout=120, env=env)
        sha = command(git + ['rev-parse', 'FETCH_HEAD'], env=env)
        if not SHA.fullmatch(sha) or (job.get('source_sha') and sha != job['source_sha']):
            raise RuntimeFailure('source SHA mismatch')
        command(git + ['checkout', '--detach', sha], timeout=60, env=env)
        # Archive before any creator code is executed; reviewers see exact Git
        # source, never files mutated by a build. Submodules are not followed.
        archive = work / 'source.tar.gz'
        command(git + ['archive', '--format=tar.gz', '--output', archive, sha], timeout=60, env=env)
        if archive.stat().st_size > 50 * 1024**2:
            raise RuntimeFailure('review source archive exceeds 50 MB')
        target = ROOT / 'reviews' / (job['id'] + '.tar.gz')
        shutil.copyfile(archive, target)
        os.chown(target, 0, grp.getgrnam('unikorn-maker-review').gr_gid); target.chmod(0o640)
        archive.unlink()
        shutil.rmtree(work / 'repo' / '.git')
    # chown -h does not follow creator-controlled symlinks.
    command(['chown', '-hR', '65532:65532', work])
    return sha


def run_job(config, state, job):
    validate_job(job)
    identifier, public = job['id'], job['kind'] == 'publish'
    settings = job['snapshot']['settings']
    running = sum(bool(item.get(key)) for item in state.values() for key in ('preview_port', 'public_port'))
    if running >= min(config.get('max_runtimes', 8), 8):
        raise RuntimeFailure('runtime capacity reached; contact platform administrator')
    build_name = 'maker-build-' + identifier
    runtime_name = 'maker-' + ('public-' if public else 'preview-') + identifier
    work = ROOT / 'mounts' / ('build-' + identifier)
    log, started = '', time.monotonic()
    image = config['images']['python' if settings['runtime'] == 'python' else 'node']
    if public:
        receipt = state.get(identifier)
        if not receipt or receipt['digest'] != job['artifact_digest'] or receipt['sha'] != job['source_sha'] or digest_tree(work / 'repo') != receipt['digest']:
            raise RuntimeFailure('reviewed artifact is missing or has changed')
        sha, digest = receipt['sha'], receipt['digest']
    else:
        work = disk('build-' + identifier, 1024)
        sha = fetch_source(job, work)
        if settings['build_command']:
            args = sandbox_options(build_name, work / 'repo', image, build=True)
            args += ['--workdir', '/workspace/' + settings['directory']]
            with tempfile.NamedTemporaryFile(mode='w', dir='/run/unikorn-makerspace', prefix='env-', delete=True) as environment:
                environment.write('\n'.join(name + '=' + value for name, value in job['environment'].items()))
                environment.flush()
                command(args + ['--env-file', environment.name, '--entrypoint', '/bin/sh', image, '-lc', settings['build_command']])
            try:
                result = command(['docker', 'wait', build_name], timeout=300)
                log = container_log(build_name)
                if result != '0':
                    raise RuntimeFailure('build failed\n' + log)
            finally:
                command(['docker', 'rm', '-f', build_name], check=False)
        digest = digest_tree(work / 'repo')
    data = disk(('public-' if public else 'preview-') + job['space_id'], 256)
    port = choose_port(state)
    args = sandbox_options(runtime_name, work / 'repo', image, data=data, port=port)
    args += ['--workdir', '/workspace/' + settings['directory']]
    # Environment files avoid credentials in Docker CLI process arguments.
    with tempfile.NamedTemporaryFile(mode='w', dir='/run/unikorn-makerspace', prefix='env-', delete=True) as environment:
        environment.write('\n'.join(name + '=' + value for name, value in job['environment'].items()))
        environment.flush()
        args += ['--env-file', environment.name]
        if settings['runtime'] == 'static':
            server = Path(__file__).with_name('static-server.mjs')
            trusted(server)
            args += ['--mount', f'type=bind,src={server},dst=/platform-server.mjs,readonly', '--entrypoint', 'node', image, '/platform-server.mjs', settings['output_directory']]
        else:
            args += ['--entrypoint', '/bin/sh', image, '-lc', settings['start_command']]
        command(args)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for attempt in range(30):
            try:
                with opener.open(f'http://127.0.0.1:{port}/', timeout=2) as response:
                    if response.status < 400:
                        break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(1)
        else:
            raise RuntimeFailure('startup health check failed\n' + container_log(runtime_name))
    except Exception:
        command(['docker', 'rm', '-f', runtime_name], check=False)
        raise
    receipt = state.setdefault(identifier, {'sha': sha, 'digest': digest, 'space_id': job['space_id']})
    receipt['public_port' if public else 'preview_port'] = port
    save_state(state)
    return {'status': 'ready', 'runtime_port': port, 'source_sha': sha, 'artifact_digest': digest, 'log': log, 'resource_usage': {'build_seconds': time.monotonic() - started, 'storage_bytes': shutil.disk_usage(work).used}}


def collect(state, keep):
    # The control plane revokes session targets BEFORE giving this retirement
    # list to the runner. Persistent data is retained for backup/recovery.
    for identifier in list(state):
        if identifier not in keep:
            for kind in ('preview', 'public'):
                command(['docker', 'rm', '-f', f'maker-{kind}-{identifier}'], check=False)
            destroy_disk('build-' + identifier)
            (ROOT / 'reviews' / (identifier + '.tar.gz')).unlink(missing_ok=True)
            del state[identifier]
    save_state(state)
    allowed_names = {f'maker-{kind}-{identifier}' for identifier in state for kind in ('preview', 'public')}
    names = command(['docker', 'ps', '-a', '--filter', 'label=' + LABEL, '--format', '{{.Names}}']).splitlines()
    for name in names:
        if name not in allowed_names and re.fullmatch(r'maker-(build|preview|public)-[a-f0-9]{32}', name):
            command(['docker', 'rm', '-f', name], check=False)
    # A host/process crash may leave a pre-receipt build with no local state.
    for image in (ROOT / 'volumes').glob('build-*.img'):
        name = image.stem
        if re.fullmatch(r'build-[a-f0-9]{32}', name) and name[6:] not in state and name[6:] not in keep:
            destroy_disk(name)
            (ROOT / 'reviews' / (name[6:] + '.tar.gz')).unlink(missing_ok=True)


def recover(state):
    """Mount private volumes before restarting only our existing containers."""
    for identifier, receipt in state.items():
        if not IDENTIFIER.fullmatch(identifier) or not IDENTIFIER.fullmatch(receipt['space_id']):
            raise RuntimeFailure('invalid local runtime state')
        disk('build-' + identifier, 1024)
        for kind in ('preview', 'public'):
            if receipt.get(kind + '_port'):
                disk(kind + '-' + receipt['space_id'], 256)
                name = 'maker-' + kind + '-' + identifier
                state_text = command(['docker', 'inspect', '--format', '{{.State.Status}}', name], check=False)
                if state_text in ('exited', 'created'):
                    command(['docker', 'start', name])


def heartbeat(config, stopped):
    while not stopped.wait(20):
        try:
            check_sandbox(config)
            call_api(config, '/worker/heartbeat', {'worker_id': 'school-makerspace'})
        except Exception:
            # Do not keep advertising a worker with broken isolation.
            pass


def main():
    if os.geteuid() != 0:
        raise RuntimeFailure('trusted runner requires root')
    config = read_config()
    trusted(ROOT)
    with (ROOT / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads((ROOT / 'state.json').read_text()) if (ROOT / 'state.json').exists() else {}
        check_sandbox(config)
        recover(state)
        stopped = threading.Event()
        threading.Thread(target=heartbeat, args=(config, stopped), daemon=True).start()
        while True:
            try:
                check_sandbox(config)
                response = call_api(config, '/worker/lease', {'worker_id': 'school-makerspace', 'capabilities': {'runtime': 'runsc', 'disk_quota': True, 'network_isolation': True}})
                job = response.get('job')
                collect(state, set(response['keep_deployments']) | ({job['id']} if job else set()))
                if job:
                    try:
                        receipt = run_job(config, state, job)
                    except Exception as error:
                        receipt = {'status': 'failed', 'log': str(error)[-16000:]}
                        if job['kind'] == 'build' and job['id'] not in state:
                            command(['docker', 'rm', '-f', 'maker-build-' + job['id']], check=False)
                            command(['docker', 'rm', '-f', 'maker-preview-' + job['id']], check=False)
                            destroy_disk('build-' + job['id'])
                            (ROOT / 'reviews' / (job['id'] + '.tar.gz')).unlink(missing_ok=True)
                    receipt['lease'] = job['lease']
                    # On uncertain callback keep state; never reuse its port.
                    call_api(config, '/worker/deployments/' + job['id'], receipt)
            except Exception as error:
                # Never print job bodies, environment values or credentials.
                print('MakerSpace worker waiting:', type(error).__name__, flush=True)
            time.sleep(10)


if __name__ == '__main__':
    main()
