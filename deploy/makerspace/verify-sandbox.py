#!/usr/bin/env python3
"""Destructive only to its generated test identity; run on a prepared test host.

Exercises real runsc builds, runtime limits, network denial and preview/public
storage separation. A generated source fixture replaces Git transport; this
does not claim to test GitHub repository authorization.
"""
import json
import os
from pathlib import Path
import sys
import urllib.request
import uuid
import fcntl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime


def verify():
    config = json.loads(Path(__file__).with_name('worker.example.json').read_text())
    runtime.check_sandbox(config)
    identifier, space_id = uuid.uuid4().hex, uuid.uuid4().hex
    state = {}
    source = """const fs=require('fs');require('http').createServer((req,res)=>{if(req.url==='/seed')fs.writeFileSync('/data/value','preview-only');res.end(fs.existsSync('/data/value')?fs.readFileSync('/data/value'):'empty');}).listen(8080,'0.0.0.0');"""
    def fixture(job, work):
        repo = work / 'repo'; repo.mkdir()
        (repo / 'server.cjs').write_text(source)
        runtime.command(['chown', '-hR', '65532:65532', work])
        return 'a' * 40
    runtime.fetch_source = fixture
    job = {'id': identifier, 'space_id': space_id, 'kind': 'build', 'source_ref': 'main', 'environment': {}, 'snapshot': {'repository': 'fixture/local', 'settings': {'runtime': 'node', 'directory': '.', 'output_directory': 'dist', 'build_command': 'node -e "console.log(\'build-ok\')"', 'start_command': 'node server.cjs'}}}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def read(port, path='/'):
        with opener.open(f'http://127.0.0.1:{port}{path}',timeout=4) as response: return response.read().decode()
    try:
        preview = runtime.run_job(config, state, job)
        assert read(preview['runtime_port'], '/seed') == 'preview-only'
        job.update(kind='publish', source_sha=preview['source_sha'], artifact_digest=preview['artifact_digest'])
        public = runtime.run_job(config, state, job)
        assert read(public['runtime_port']) == 'empty'
        assert read(preview['runtime_port']) == 'preview-only'
        from backup import backup_volume
        (runtime.ROOT / 'backups').mkdir(mode=0o700, exist_ok=True)
        backup = backup_volume(runtime.ROOT / 'volumes' / f'preview-{space_id}.img')
        assert backup.is_file()
        name = 'maker-public-' + identifier
        info = json.loads(runtime.command(['docker', 'inspect', name]))[0]
        host = info['HostConfig']
        assert host['Runtime'] == 'runsc'
        assert host['Memory'] == 256 * 1024**2 and host['MemorySwap'] == host['Memory']
        assert host['NanoCpus'] == 500000000 and host['PidsLimit'] == 64
        assert host['ReadonlyRootfs'] is True and host['Privileged'] is False
        # Real writes beyond the persistent filesystem quota must fail.
        quota = runtime.command(['docker', 'exec', name, 'node', '-e', "const fs=require('fs');try{const fd=fs.openSync('/data/quota-test','w');for(let i=0;i<300;i++)fs.writeSync(fd,Buffer.alloc(1024*1024));console.log('UNBOUNDED')}catch(e){console.log(e.code)}finally{fs.rmSync('/data/quota-test',{force:true})}"])
        assert 'ENOSPC' in quota, quota
        for address in ('10.121.15.221', '169.254.169.254', '172.30.91.1', '1.1.1.1', '223.5.5.5'):
            script = f"const s=require('net').connect(443,'{address}');s.setTimeout(1000);s.on('connect',()=>{{console.log('UNSAFE');s.destroy()}});s.on('timeout',()=>{{console.log('blocked');s.destroy()}});s.on('error',()=>console.log('blocked'));"
            assert runtime.command(['docker', 'exec', name, 'node', '-e', script]) == 'blocked'
        print(json.dumps({'real_runsc_build': True, 'runtime_health': True, 'resource_configuration': True, 'disk_quota_enforced': True, 'private_network_blocked': True, 'public_runtime_egress_blocked': True, 'preview_public_data_separate': True, 'data_backup_verified': True}))
    finally:
        for kind in ('build', 'preview', 'public'):
            runtime.command(['docker', 'rm', '-f', f'maker-{kind}-{identifier}'], check=False)
        runtime.collect(state, set())
        runtime.destroy_disk('build-' + identifier)
        for kind in ('preview', 'public'):
            mount = runtime.ROOT / 'mounts' / f'{kind}-{space_id}'
            if os.path.ismount(mount): runtime.command(['umount', mount])
            if mount.exists(): mount.rmdir()
            (runtime.ROOT / 'volumes' / f'{kind}-{space_id}.img').unlink(missing_ok=True)
            import shutil
            shutil.rmtree(runtime.ROOT / 'backups' / f'{kind}-{space_id}', ignore_errors=True)


def main():
    # Do not overwrite a live worker's state or race its port/volume admission.
    with (runtime.ROOT / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = runtime.ROOT / 'state.json'
        if state.exists() and json.loads(state.read_text()):
            raise RuntimeError('Use an empty verification host, not live creator state')
        verify()


if __name__ == '__main__':
    main()
