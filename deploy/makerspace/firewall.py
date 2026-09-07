#!/usr/bin/env python3
"""Install only MakerSpace-owned chains. Never flush host or Docker policy."""
import subprocess

EGRESS = [
    ['-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'RETURN'],
    *[['-d', network, '-j', 'DROP'] for network in ['0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12', '192.0.0.0/24', '192.0.2.0/24', '192.168.0.0/16', '198.18.0.0/15', '198.51.100.0/24', '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4']],
    ['-o', 'br-makerspace', '-j', 'DROP'],
    ['-p', 'tcp', '-m', 'multiport', '--dports', '80,443', '-j', 'RETURN'],
    ['-d', '223.5.5.5', '-p', 'udp', '--dport', '53', '-j', 'RETURN'],
    ['-d', '223.5.5.5', '-p', 'tcp', '--dport', '53', '-j', 'RETURN'],
    ['-j', 'DROP'],
]
HOST = [
    ['-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'RETURN'],
    ['-j', 'DROP'],
]


def run(args, check=True):
    return subprocess.run(['iptables', '-w', '10'] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def ensure_chain(name, rules):
    existing = run(['-S', name], check=False)
    if existing.returncode:
        run(['-N', name])
        for rule in rules:
            run(['-A', name] + rule)
    else:
        # Refuse unexpected rules. Do not briefly open a running network while
        # replacing a chain; operators must explicitly review policy changes.
        count = len([line for line in existing.stdout.decode().splitlines() if line.startswith('-A ')])
        if count != len(rules):
            raise RuntimeError('Unexpected MakerSpace firewall rules: ' + name)
        for rule in rules:
            run(['-C', name] + rule)


def main():
    ensure_chain('MAKERSPACE-EGRESS', EGRESS)
    ensure_chain('MAKERSPACE-HOST', HOST)
    for parent, target in [('DOCKER-USER', 'MAKERSPACE-EGRESS'), ('INPUT', 'MAKERSPACE-HOST')]:
        rule = ['-i', 'br-makerspace', '-j', target]
        if run(['-C', parent] + rule, check=False).returncode:
            run(['-I', parent, '1'] + rule)


if __name__ == '__main__':
    main()
