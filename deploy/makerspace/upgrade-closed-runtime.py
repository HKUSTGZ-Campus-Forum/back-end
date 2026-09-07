#!/usr/bin/env python3
"""Approved upgrade of the existing MakerSpace runner. No host-wide rule flush."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

SOURCE = Path(__file__).resolve().parent
LIBRARY = Path('/usr/local/libexec/unikorn-makerspace')
ROOT = Path('/srv/unikorn-makerspace')

def run(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--approval-reference', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'tpds-planner-app-ub2403-prod-01':
        raise RuntimeError('Run through interactive sudo on the reviewed school host')
    if len(args.approval_reference.strip()) < 12:
        raise RuntimeError('A current concrete approval reference is required')
    state = ROOT / 'state.json'
    if state.exists() and json.loads(state.read_text()):
        raise RuntimeError('Existing creator deployments require a separate count-checked upgrade plan')
    run('systemctl', 'stop', 'unikorn-makerspace-worker.service')
    running = run('docker', 'ps', '-q', '--filter', 'label=unikorn.makerspace=1').strip()
    if running:
        raise RuntimeError('Creator containers must be stopped under the approved plan')
    backup = ROOT / 'backups' / ('closed-runtime-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    for name in ('runtime.py', 'firewall.py', 'verify-sandbox.py'):
        if (LIBRARY / name).exists(): shutil.copy2(LIBRARY / name, backup / name)
    (backup / 'iptables.rules').write_bytes(run('iptables-save'))
    (backup / 'approval.json').write_text(json.dumps({'approval_reference': args.approval_reference}))
    if subprocess.run(['docker','network','inspect','unikorn-makerbuild'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        run('docker','network','create','--driver','bridge','--subnet','172.30.92.0/24','--opt','com.docker.network.bridge.name=br-makerbuild','--opt','com.docker.network.bridge.enable_icc=false','unikorn-makerbuild')
    for name in ('runtime.py', 'firewall.py', 'verify-sandbox.py', 'worker.example.json'):
        source = SOURCE / name
        if source.is_symlink(): raise RuntimeError('Refuse symlinked platform source')
        shutil.copyfile(source, LIBRARY / name)
        os.chown(LIBRARY / name, 0, 0); (LIBRARY / name).chmod(0o755 if name.endswith('.py') else 0o644)
    run('python3', str(LIBRARY / 'firewall.py'))
    # This refuses nonempty state and verifies real runsc, volumes and egress.
    run('python3', str(LIBRARY / 'verify-sandbox.py'))
    run('systemctl', 'start', 'unikorn-makerspace-worker.service')
    print(json.dumps({'closed_runtime': 'v1', 'backup': str(backup), 'verified': True}))


if __name__ == '__main__': main()
