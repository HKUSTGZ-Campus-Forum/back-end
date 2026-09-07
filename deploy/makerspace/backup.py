#!/usr/bin/env python3
"""Consistent, bounded backups of creator data volumes; no platform secrets."""
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import signal

from runtime import ROOT, command


def backup_volume(image):
    if not re.fullmatch(r'(preview|public)-[a-f0-9]{32}\.img', image.name):
        raise ValueError('invalid data volume')
    destination = ROOT / 'backups' / image.stem
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(ROOT).free < 2 * 1024**3:
        raise RuntimeError('insufficient reserve for verified backup')
    mount = ROOT / 'mounts' / image.stem
    with tempfile.TemporaryDirectory(prefix='snapshot-', dir=ROOT / 'backups') as temporary:
        snapshot = Path(temporary) / 'data.img'
        frozen = False
        try:
            if os.path.ismount(mount):
                command(['fsfreeze', '--freeze', mount]); frozen = True
            shutil.copyfile(image, snapshot)
        finally:
            if frozen:
                command(['fsfreeze', '--unfreeze', mount])
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        output = destination / (stamp + '.img.gz')
        temporary_output = output.with_suffix('.partial')
        digest = hashlib.sha256()
        with snapshot.open('rb') as source, gzip.open(temporary_output, 'wb', compresslevel=1) as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk); target.write(chunk)
        verified = hashlib.sha256()
        with gzip.open(temporary_output, 'rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''): verified.update(chunk)
        if verified.digest() != digest.digest():
            raise RuntimeError('backup verification failed')
        temporary_output.replace(output)
        output.with_suffix('.json').write_text(json.dumps({'sha256': digest.hexdigest(), 'bytes': snapshot.stat().st_size, 'volume': image.name, 'created_at': stamp}))
        for old in sorted(destination.glob('*.img.gz'))[:-7]:
            old.with_suffix('.json').unlink(missing_ok=True)
            old.unlink()
        return output


def main():
    if os.geteuid() != 0:
        raise RuntimeError('requires root')
    os.umask(0o077)
    def interrupted(_signum, _frame):
        raise SystemExit('backup interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    for image in sorted((ROOT / 'volumes').glob('*.img')):
        if image.name.startswith(('preview-', 'public-')):
            backup_volume(image)
    print('Creator data backups verified')


if __name__ == '__main__':
    main()
